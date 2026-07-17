from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adt_ai.shared import crypto
from adt_ai.shared.config import is_enabled
from adt_ai.shared.dict_merge import deep_merge

# Standard Oracle listener port, applied wherever a connection omits `port`
# (driver DSNs, SQLcl connect strings, and the connection editor default).
DEFAULT_PORT = 1521


class ConnectionError(Exception):
    """Base error for connection loading failures."""


class ConnectionNotFoundError(ConnectionError):
    """Raised when a requested connection file, environment, or schema is missing."""


@dataclass(frozen=True)
class Connection:
    environment     : str
    schema          : str
    username        : str
    password        : str | None
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
    wallet_password : str | None = None
    client_lib_dir  : str | None = None


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
        return _expand_schema_patterns(_split_schema_values(values), available)

    def resolve(
        self,
        environment: str | None = None,
        schema: str | None = None,
        kind: str = "db",
    ) -> Connection:
        environment_name = environment or self.default_environment
        environment_data = self._environment(environment_name)
        schema_name = schema or self.default_schema(environment_name, kind=kind)
        schema_data = environment_data.get("schemas", {}).get(schema_name)
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
        password = _decrypt_if_enabled(
            db.get("pwd"),
            db.get("pwd!"),
            key     = self.key,
            context = f"{environment_name}.{schema_name} pwd",
        )
        wallet_password = _decrypt_if_enabled(
            db.get("wallet_password") or db.get("wallet_pwd"),
            db.get("wallet_password!") or db.get("wallet_pwd!"),
            key     = self.key,
            context = f"{environment_name} wallet_pwd",
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
            thick           = is_enabled(db.get("thick")),
            lang            = db.get("lang"),
            export          = dict(schema_data.get("export") or {}),
            apex            = dict(schema_data.get("apex") or {}),
            wallet_path     = _resolve_wallet_path(
                db.get("wallet_path") or db.get("wallet"),
                self.wallet_roots,
            ),
            wallet_password = wallet_password,
            client_lib_dir  = db.get("client_lib_dir") or db.get("lib_dir"),
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
            *[f"  - {path}" for path in self.files],
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
            searched_text = "\n".join(f"  - {path}" for path in candidates)
            raise ConnectionNotFoundError(
                "Connection file not found. Searched:\n" + searched_text
            )

        # First match wins: load only the first existing candidate, no layering.
        loaded = yaml.safe_load(chosen.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ConnectionError(f"Connection file must contain a YAML mapping: {chosen}")
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
    return "\n".join([f"{label}:", *[f"  - {item}" for item in items]])


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


def _decrypt_if_enabled(
    value: Any,
    marker: Any,
    *,
    key: str | None,
    context: str,
) -> Any:
    if not is_enabled(marker):
        return value
    try:
        resolved_key = crypto.resolve_key(key)
        return crypto.decrypt(value, resolved_key)
    except crypto.CryptoError as error:
        raise ConnectionError(
            f"Could not decrypt {context}; pass -key or set {crypto.KEY_ENV}: {error}"
        ) from error


def _is_legacy_adt_wallet_path(path: Path) -> bool:
    parts = path.parts
    return any(
        parts[index:index + 3] == ("PROJECTS", "ADT", "wallets")
        for index in range(len(parts) - 2)
    )


def _split_schema_values(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list | tuple) else [value]
    schemas: list[str] = []
    for item in values:
        schemas.extend(
            part.strip()
            for part in str(item).split(",")
            if part.strip()
        )
    return schemas


def _expand_schema_patterns(patterns: list[str], available: list[str]) -> list[str]:
    schemas: list[str] = []
    for pattern in patterns:
        if pattern == "%":
            matches = available
        elif "%" in pattern or "*" in pattern:
            wildcard = pattern.upper().replace("%", "*")
            matches = [
                schema
                for schema in available
                if fnmatch.fnmatchcase(schema.upper(), wildcard)
            ]
        else:
            matches = [pattern]
        for schema in matches:
            if schema not in schemas:
                schemas.append(schema)
    return schemas
