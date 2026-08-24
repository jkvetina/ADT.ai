from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from adt_ai.shared import crypto
from adt_ai.shared.config import is_enabled
from adt_ai.shared.connection_errors import (
    ConnectFailedError,
    ConnectionError,
    ConnectionNotFoundError,
    CredentialUnavailableError,
    InvalidConnectionError,
)
from adt_ai.shared.dict_merge import deep_merge
from adt_ai.shared.file_list import file_rows
from adt_ai.shared.schema_selection import (
    expand_schema_patterns,
    match_schema,
    split_schema_values,
)
from adt_ai.shared.secret import Secret
from adt_ai.shared.secret_command import SecretCommandError, block_secret
from adt_ai.shared.sqlcl_names import derive_sqlcl_name

# Standard Oracle listener port, applied wherever a connection omits `port`
# (driver DSNs, SQLcl connect strings, and the connection editor default).
DEFAULT_PORT = 1521


# The error classes live in `connection_errors.py` (ADT #407) and are re-exported
# because `from adt_ai.shared.connections import ConnectionError` is the spelling
# the whole tree already uses.
__all__ = [
    "ConnectFailedError",
    "Connection",
    "ConnectionError",
    "ConnectionLoader",
    "ConnectionNotFoundError",
    "ConnectionResult",
    "CredentialUnavailableError",
    "DEFAULT_PORT",
    "InvalidConnectionError",
]


@dataclass(frozen=True)
class Connection:
    # `password` and `wallet_password` always hold a `Secret` once the object
    # exists (ADT #400). The constructor still accepts `str`, `bytes` or `None`
    # and `__post_init__` coerces, so the invariant holds for every caller,
    # runtime and test alike, rather than resting on each construction site
    # remembering to wrap. `password_mode` is the `pwd!` marker, not a
    # credential, so it stays a plain string.
    environment     : str
    schema          : str
    username        : str
    password        : Secret
    password_mode   : str | None
    hostname        : str | None
    port            : int | None
    service         : str | None
    sid             : str | None
    thick           : bool
    lang            : str | None
    export          : dict[str, Any]
    apex            : dict[str, Any]
    wallet_path     : str | None = None
    wallet_password : Secret = field(default_factory=Secret)
    client_lib_dir  : str | None = None
    # Named SQLcl connection identity (ADT #148): the name generated SQLcl
    # scripts connect with, the fingerprint recorded at last registration, and
    # the YAML file registrations are written back to. All three stay ``None``
    # on an ad-hoc Connection, which keeps the inline-connect behavior.
    sqlcl_name      : str | None = None
    sqlcl_sync      : str | None = None
    sqlcl_source    : str | None = None
    # `auth: external` (ADT #395). The credential is read out of `cwallet.sso`
    # inside the Oracle client library, so ADT never holds one: no `pwd`, no
    # `pwd!`, no `ADT_KEY`, and `_decrypt_if_enabled` is never reached. `tns` is
    # the alias the wallet files that credential under, and is what both the
    # driver and SQLcl connect to.
    auth            : str | None = None
    tns             : str | None = None

    @property
    def external_auth(self) -> bool:
        return str(self.auth or "").strip().lower() == "external"

    def __post_init__(self) -> None:
        # The wrap happens here rather than at `resolve()` so that no way of
        # building a Connection can produce one carrying a bare string: an
        # ad-hoc construction in a command, a test fixture, and the loader all
        # land on the same guarantee. `object.__setattr__` is how a frozen
        # dataclass normalizes its own fields.
        object.__setattr__(self, "password", Secret(self.password))
        object.__setattr__(self, "wallet_password", Secret(self.wallet_password))


@dataclass(frozen=True)
class ConnectionResult:
    data         : dict[str, Any]
    files        : list[Path]
    wallet_roots : list[Path]
    key          : str | None = None

    @property
    def default_environment(self) -> str:
        try:
            return next(iter(self.data))
        except StopIteration as error:
            raise ConnectionNotFoundError("No connection environments configured") from error

    def default_schema(self, environment: str | None = None, kind: str = "db") -> str:
        return self.default_schemas(environment=environment, kind=kind)[0]

    def default_schemas(self, environment: str | None = None, kind: str = "db") -> list[str]:
        environment_name = environment or self.default_environment
        environment_data = self._environment(environment_name)
        default_key = "schema_apex" if kind == "apex" else "schema_db"
        schema = environment_data.get("defaults", {}).get(default_key)
        if not schema:
            raise ConnectionNotFoundError(
                "\n".join(
                    [
                        f"Default {kind} schema not configured for environment: {environment_name}",
                        *self._source_lines(),
                        self._available_schema_line(environment_name),
                    ]
                )
            )
        return self.expand_schemas(schema, environment=environment_name)

    def schema_names(self, environment: str | None = None) -> list[str]:
        environment_name = environment or self.default_environment
        schemas = self._environment(environment_name).get("schemas", {})
        if not isinstance(schemas, dict):
            return []
        return [str(schema) for schema in schemas]

    def expand_schemas(self, values: Any, environment: str | None = None) -> list[str]:
        environment_name = environment or self.default_environment
        available = self.schema_names(environment_name)
        return expand_schema_patterns(split_schema_values(values), available)

    # An Oracle schema name is a case-insensitive identifier, but ADT.ai reaches this
    # lookup with names from two different places: what the user typed in the
    # connection file, and what a command derived. `patch` derives its group from the
    # exported folder and uppercases it, so a file keyed `ict_owner` could not serve a
    # patch group named `ICT_OWNER`, the same project resolved fine for
    # `dependencies -refresh -schema ict_owner` and failed for `patch -deploy`, which
    # reads as a broken connection file rather than a lookup rule (`#198`).
    #
    # The exact key still wins, so a file deliberately carrying two casings keeps both.
    def resolve(
        self,
        environment: str | None = None,
        schema: str | None = None,
        kind: str = "db",
    ) -> Connection:
        environment_name = environment or self.default_environment
        environment_data = self._environment(environment_name)
        schema_name = schema or self.default_schema(environment_name, kind=kind)
        schema_name, schema_data = match_schema(
            environment_data.get("schemas", {}), schema_name
        )
        if not isinstance(schema_data, dict):
            raise ConnectionNotFoundError(
                "\n".join(
                    [
                        f"Schema not configured: {environment_name}.{schema_name}",
                        *self._source_lines(),
                        self._available_schema_line(environment_name),
                    ]
                )
            )

        wallet_data = environment_data.get("wallet", {})
        db = deep_merge(environment_data.get("db", {}), wallet_data)
        db = deep_merge(db, schema_data.get("db", {}))
        # A `pwd_cmd:` / `wallet_pwd_cmd:` block fetches its secret from the
        # customer's own vault (ADT #397), so it never reaches the stored-value
        # path below and needs no key at all. `None` means no command is
        # configured; a command standing beside a stored value raises rather than
        # ranking the two.
        password_context = f"{environment_name}.{schema_name} pwd"
        external = str(db.get("auth") or "").strip().lower() == "external"
        if external:
            # `auth: external` skips password resolution entirely (ADT #395).
            # Not "resolves to empty": the whole point is that no branch below
            # can ask for `ADT_KEY`, run a vault command, or reach a decrypt, so
            # a file under this mode needs none of them and cannot fail on them.
            password = None
        else:
            password = _fetched_secret(db, "pwd", password_context)
            if password is None:
                password = _decrypt_if_enabled(
                    db.get("pwd"),
                    db.get("pwd!"),
                    db.get("pwd_key"),
                    key     = self.key,
                    context = password_context,
                )
        wallet_context = f"{environment_name} wallet_pwd"
        wallet_password = _fetched_secret(db, "wallet_pwd", wallet_context)
        if wallet_password is None:
            wallet_password = _decrypt_if_enabled(
                db.get("wallet_password") or db.get("wallet_pwd"),
                db.get("wallet_password!") or db.get("wallet_pwd!"),
                db.get("wallet_password_key") or db.get("wallet_pwd_key"),
                key     = self.key,
                context = wallet_context,
            )
        return Connection(
            environment     = environment_name,
            schema          = schema_name,
            username        = str(db.get("user") or schema_name),
            password        = password,
            password_mode   = db.get("pwd!"),
            hostname        = db.get("hostname"),
            port            = db.get("port"),
            service         = db.get("service"),
            sid             = db.get("sid"),
            # External authentication is thick-mode only in python-oracledb, so
            # the mode implies the flag rather than asking the reader to set two
            # things that only work together.
            thick           = is_enabled(db.get("thick")) or external,
            lang            = db.get("lang"),
            export          = dict(schema_data.get("export") or {}),
            apex            = dict(schema_data.get("apex") or {}),
            wallet_path     = _resolve_wallet_path(
                db.get("wallet_path") or db.get("wallet"),
                self.wallet_roots,
            ),
            wallet_password = wallet_password,
            client_lib_dir  = db.get("client_lib_dir") or db.get("lib_dir"),
            sqlcl_name      = self._sqlcl_name(db, environment_name, schema_name),
            sqlcl_sync      = db.get("sqlcl_sync"),
            sqlcl_source    = str(self.files[0]) if self.files else None,
            auth            = db.get("auth"),
            tns             = db.get("tns"),
        )

    def _sqlcl_name(
        self,
        db: dict[str, Any],
        environment_name: str,
        schema_name: str,
    ) -> str | None:
        recorded = db.get("sqlcl")
        if recorded:
            return str(recorded)
        if not self.files:
            return None
        environments = [
            name for name, node in self.data.items() if isinstance(node, dict)
        ]
        schemas = self._environment(environment_name).get("schemas") or {}
        return derive_sqlcl_name(
            self.files[0],
            environment_name,
            schema_name,
            multi_environment = len(environments) > 1,
            multi_schema      = isinstance(schemas, dict) and len(schemas) > 1,
        )

    def _environment(self, name: str) -> dict[str, Any]:
        environment = self.data.get(name)
        if not isinstance(environment, dict):
            raise ConnectionNotFoundError(
                "\n".join(
                    [
                        f"Environment not configured: {name}",
                        *self._source_lines(),
                        self._available_environment_line(),
                    ]
                )
            )
        return environment

    def _source_lines(self) -> list[str]:
        if not self.files:
            return []
        if len(self.files) == 1:
            return [f"Edit connection file: {self.files[0]}"]
        return [
            "Edit one of these connection files:",
            *file_rows([str(path) for path in self.files], nested=False),
        ]

    def _available_environment_line(self) -> str:
        return _available_list("Available environments", self.data)

    def _available_schema_line(self, environment: str) -> str:
        return _available_list(
            f"Available schemas in {environment}",
            self.schema_names(environment),
        )


class ConnectionLoader:
    def __init__(
        self,
        search_paths: list[Path] | tuple[Path, ...],
        wallet_roots: list[Path] | tuple[Path, ...] = (),
        key: str | None = None,
    ) -> None:
        self.search_paths = [Path(path) for path in search_paths]
        self.wallet_roots = [Path(path).expanduser() for path in wallet_roots]
        self.key = key

    def load(
        self,
        filename: str | list[str] = "connections.yaml",
        *,
        candidates: list[Path] | None = None,
    ) -> ConnectionResult:
        if candidates is None:
            filenames = [filename] if isinstance(filename, str) else filename
            candidates = [
                path
                for name in filenames
                for path in _expected_connection_paths(name, self.search_paths)
            ]
        candidates = [Path(path).expanduser() for path in candidates]

        chosen = next((path for path in candidates if path.is_file()), None)
        if chosen is None:
            searched_text = "\n".join(
                file_rows([str(path) for path in candidates], nested=False)
            )
            raise ConnectionNotFoundError(
                "Connection file not found. Searched:\n" + searched_text
            )

        # First match wins: load only the first existing candidate, no layering.
        try:
            loaded = yaml.safe_load(chosen.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as error:
            # Route a hand-edit syntax error through the friendly connection
            # banner instead of the generic UNEXPECTED ERROR catch-all.
            raise InvalidConnectionError(
                f"Connection file is not valid YAML: {chosen}\n{error}"
            ) from error
        if not isinstance(loaded, dict):
            raise InvalidConnectionError(
                f"Connection file must contain a YAML mapping: {chosen}"
            )
        return ConnectionResult(
            data         = loaded,
            files        = [chosen],
            wallet_roots = self.wallet_roots,
            key          = self.key,
        )


def _expected_connection_paths(filename: str, search_paths: list[Path]) -> list[Path]:
    path = Path(filename).expanduser()
    if path.is_absolute():
        return [path]
    return [search_path / path for search_path in search_paths]


def _available_list(label: str, values: Any) -> str:
    items = sorted((str(value) for value in values), key=str.upper)
    if not items:
        items = ["<none>"]
    return "\n".join([f"{label}:", *file_rows(items, nested=False)])


def _resolve_wallet_path(value: Any, wallet_roots: list[Path]) -> str | None:
    if not value:
        return None
    if not wallet_roots:
        return str(value)
    wallet = Path(str(value)).expanduser()
    candidates = [root / wallet.name for root in wallet_roots]
    if wallet.is_absolute():
        if not _is_legacy_adt_wallet_path(wallet):
            candidates.append(wallet)
    else:
        candidates.extend(root / wallet for root in wallet_roots)
        candidates.append(wallet)
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0] if candidates else wallet)


def _fetched_secret(db: dict[str, Any], kind: str, context: str) -> Secret | None:
    """The secret a `_cmd` key fetches, as a connection failure when it cannot.

    A vault CLI that is missing, unauthenticated, or slow is a connect failure
    like a wrong password, so it re-raises as `CredentialUnavailableError` and
    lands on the credential banner rather than surfacing as an unexpected error.
    `block_secret`'s other refusal, a block naming two sources for one secret, is
    a file defect and is reported the same way on purpose: separating it would
    cost an exception class one module down, and the message names the keys to
    edit either way.
    """
    try:
        return block_secret(db, kind, context=context)
    except SecretCommandError as error:
        raise CredentialUnavailableError(str(error)) from error


def _decrypt_if_enabled(
    value: Any,
    marker: Any,
    recorded_fingerprint: Any = None,
    *,
    key: str | None,
    context: str,
) -> Any:
    if not is_enabled(marker):
        return value

    def cannot_decrypt(error: Exception) -> CredentialUnavailableError:
        return CredentialUnavailableError(
            f"Could not decrypt {context}; pass -key or set {crypto.KEY_ENV}: {error}"
        )

    try:
        resolved_key = crypto.resolve_key(key)
        # Fernet answers a wrong key and a damaged value with the same
        # exception, so without a recorded fingerprint (`pwd_key:`, ADT #399)
        # the commonest mistake of all reads as a corrupt connection file. The
        # comparison is close to free: it needs the same PBKDF2 derivation the
        # decrypt below is about to do, and `crypto` memoises that. A file
        # written before `#399` records nothing, and keeps the older, honestly
        # vaguer message rather than a guess about which case it hit.
        #
        # `readable_fingerprint` also returns None for a recorded value that is
        # not a digest, which a YAML loader can produce from one that happens to
        # look numeric. Such a value says nothing about the key, so it counts as
        # absent rather than as a mismatch: an unreadable fingerprint must never
        # refuse a correct key (ADT #398).
        expected = crypto.readable_fingerprint(recorded_fingerprint)
        actual = crypto.fingerprint(value, resolved_key) if expected else None
    except crypto.CryptoError as error:
        raise cannot_decrypt(error) from error

    if expected and actual != expected:
        raise CredentialUnavailableError(
            f"Wrong encryption key for {context}: the stored value carries key "
            f"fingerprint {expected}, the key in use fingerprints as {actual}. "
            f"Pass -key or set {crypto.KEY_ENV} to the key this value was encrypted with."
        )

    try:
        return crypto.decrypt(value, resolved_key)
    except crypto.CryptoError as error:
        raise cannot_decrypt(error) from error


def _is_legacy_adt_wallet_path(path: Path) -> bool:
    parts = path.parts
    return any(
        parts[index:index + 3] == ("PROJECTS", "ADT", "wallets")
        for index in range(len(parts) - 2)
    )
