from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from adt_ai.connection.errors import ConnectionEditError
from adt_ai.connection.stored_secrets import (
    SECRET_SAFE_YAML_WIDTH,
    fingerprint_key,
    rekey_secrets,
    write_fingerprint,
)
from adt_ai.shared import crypto, text_files
from adt_ai.shared.connections import DEFAULT_PORT
from adt_ai.shared.secret_command import COMMAND_KEYS

# Keys that belong to a stored secret: the value, its encryption marker, and the
# key fingerprint recorded beside it (ADT #399). They are stripped together when
# cloning an environment with -like, so a copied skeleton never inherits another
# environment's password, nor a fingerprint describing a password it no longer
# has.
#
# The `_cmd` keys (ADT #397) are stripped for the same reason and it is the more
# dangerous case of the two: a vault path names the environment it belongs to
# (`op://vault/DEV_APP/password`), so a UAT skeleton cloned from DEV would carry
# a working pointer at the DEV credential and connect with it.
_SECRET_KEYS = (
    "pwd",
    "pwd!",
    "pwd_key",
    "wallet_pwd",
    "wallet_pwd!",
    "wallet_pwd_key",
    "wallet_password",
    "wallet_password_key",
    *COMMAND_KEYS,
)


@dataclass(frozen=True)
class ConnectionEditRequest:
    path        : Path
    action      : str
    environment : str
    schema      : str | None = None
    username    : str | None = None
    password    : str | None = None
    wallet      : str | None = None
    wallet_password: str | None = None
    hostname    : str | None = None
    port        : int | None = None
    service     : str | None = None
    sid         : str | None = None
    workspace   : str | None = None
    app         : str | None = None
    prefix      : str | None = None
    ignore      : str | None = None
    like        : str | None = None
    default     : bool = False
    apply       : bool = False
    encrypt     : bool = False
    key         : str | None = None
    # -rekey only (ADT #398). Both are required for that action and unused by
    # every other one; `key` stays the single-key flag the write actions use.
    old_key     : str | None = None
    new_key     : str | None = None


@dataclass(frozen=True)
class ConnectionEditResult:
    action      : str
    environment : str
    schema      : str | None
    summary     : str
    preview     : str
    written     : bool


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    # A stored secret is a single 130 character token with no spaces in it, and
    # the default width of 80 pushes it onto a continuation line, leaving the
    # key spelled `pwd: ` with a trailing space above it. The value survives
    # that (YAML can only fold at a space, and there is none, so the token is
    # emitted whole) but the layout is wrong twice over: trailing whitespace in
    # a file ADT writes, and a shape that flips back and forth depending on
    # which writer touched the file last. `sqlcl_names.py` sets the same width
    # for that second reason, so the two dumpers cannot disagree.
    yaml.width = SECRET_SAFE_YAML_WIDTH
    return yaml


def _reject_unsafe_yaml(text: str) -> None:
    """Reject anything a safe loader would refuse before round-tripping.

    The editor loads with ruamel's RoundTrip loader to preserve comments and key
    order on write, but RoundTrip is *not* a safe loader. A connection file
    authored elsewhere could carry a ``!!python/...`` (or other custom) tag. A
    safe-load pass first ensures the document is plain YAML; only then do we hand
    it to the round-tripper. (In practice ruamel's RoundTrip loader degrades such
    tags to plain data rather than executing them, so this is defense-in-depth,
    not an RCE patch, but it keeps unsupported documents out entirely.)
    """
    try:
        YAML(typ="safe").load(text)
    except Exception as error:
        raise ConnectionEditError(
            f"unsupported or unsafe YAML in connection file: {error}"
        ) from error


def _plain(node: Any) -> Any:
    """Rebuild a ruamel node as plain dict/list, dropping comments and anchors.

    Cloned content (``-like``) should start clean rather than carry the source
    environment's inline comments or YAML anchors into the new block.
    """
    if isinstance(node, dict):
        return {key: _plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_plain(value) for value in node]
    return node


def _is_blank(value: Any) -> bool:
    """Whether a stored YAML scalar is an empty placeholder rather than a value."""
    return value is None or (isinstance(value, str) and not value.strip())


def _strip_secrets(mapping: dict[str, Any]) -> dict[str, Any]:
    for key in _SECRET_KEYS:
        mapping.pop(key, None)
    return mapping


def _strip_env_secrets(env_node: dict[str, Any]) -> dict[str, Any]:
    """Deep structural copy of an environment with every stored secret removed.

    Previews go to stdout, and stdout lands in shell history and agent
    transcripts, so a preview may show the shape of an environment but never
    the secrets of the sibling schemas or the wallet it happens to reuse.
    """
    plain = _plain(env_node) or {}
    for section in ("db", "wallet"):
        if isinstance(plain.get(section), dict):
            _strip_secrets(plain[section])
    schemas = plain.get("schemas")
    if isinstance(schemas, dict):
        for schema_node in schemas.values():
            if isinstance(schema_node, dict) and isinstance(schema_node.get("db"), dict):
                _strip_secrets(schema_node["db"])
    return plain


class ConnectionEditor:
    """Round-trip editor for an ADT connections YAML file.

    Loads the file with ruamel (preserving comments and key order), applies one
    structural mutation, and (only when ``request.apply`` is set) writes the
    whole document back. Otherwise the file is left untouched and the caller
    renders the returned ``preview`` block. Passwords are never rendered into the
    preview; only the structural skeleton is shown.
    """

    def run(self, request: ConnectionEditRequest) -> ConnectionEditResult:
        if request.path.exists():
            text = request.path.read_text(encoding="utf-8")
        elif request.action == "create":
            text = ""
        else:
            # Only `create` may start from a blank document; every other action
            # edits an existing file, so surface the miss as an edit error (exit 2)
            # instead of a raw FileNotFoundError traceback.
            raise ConnectionEditError(f"connection file not found: {request.path}")
        _reject_unsafe_yaml(text)
        yaml = _yaml()
        data = yaml.load(text)
        if data is None:
            data = {}

        if request.action == "create":
            summary, preview = self._create_connection(yaml, data, request)
        elif request.action == "add-env":
            summary, preview = self._add_env(yaml, data, request)
        elif request.action == "add-schema":
            summary, preview = self._add_schema(yaml, data, request)
        elif request.action == "set-pwd":
            summary, preview = self._set_pwd(data, request)
        elif request.action == "set-wallet-pwd":
            summary, preview = self._set_wallet_pwd(data, request)
        elif request.action == "rekey":
            summary, preview = rekey_secrets(data, request)
        else:
            raise ConnectionEditError(f"unknown action: {request.action}")

        if request.apply:
            request.path.parent.mkdir(parents=True, exist_ok=True)
            buffer = io.StringIO()
            yaml.dump(data, buffer)
            text_files.write_private_text(request.path, buffer.getvalue())

        return ConnectionEditResult(
            action      = request.action,
            environment = request.environment,
            schema      = request.schema,
            summary     = summary,
            preview     = preview,
            written     = request.apply,
        )

    def _add_env(self, yaml: YAML, data: Any, request: ConnectionEditRequest):
        env = request.environment
        if env in data:
            raise ConnectionEditError(f"environment already exists: {env}")
        if request.like is not None:
            if request.like not in data:
                raise ConnectionEditError(f"source environment not found: {request.like}")
            new_env = self._clone_env(data[request.like], request)
            summary = f"add environment {env} (like {request.like})"
        else:
            new_env = self._fresh_env(request)
            summary = f"add environment {env}"
        data[env] = new_env
        return summary, self._dump_node(yaml, {env: new_env})

    def _fresh_env(self, request: ConnectionEditRequest) -> dict[str, Any]:
        db: dict[str, Any] = {}
        if request.hostname:
            db["hostname"] = request.hostname
        db["port"] = request.port if request.port is not None else DEFAULT_PORT
        if request.service:
            db["service"] = request.service
        if request.sid:
            db["sid"] = request.sid
        return {"db": db, "defaults": {}, "schemas": {}}

    def _create_connection(self, yaml: YAML, data: Any, request: ConnectionEditRequest):
        env_node = data.get(request.environment) if isinstance(data, dict) else None
        if not isinstance(env_node, dict):
            env_node = self._fresh_env(request)
            data[request.environment] = env_node
        else:
            self._merge_db_defaults(env_node, request)

        self._merge_wallet(env_node, request)
        schemas = env_node.get("schemas")
        if not isinstance(schemas, dict):
            schemas = {}
            env_node["schemas"] = schemas
        schema_node = schemas.get(request.schema)
        if not isinstance(schema_node, dict):
            schema_node = {}
            schemas[request.schema] = schema_node
        self._merge_schema(schema_node, request)

        if request.default:
            defaults = env_node.get("defaults")
            if not isinstance(defaults, dict):
                defaults = {}
                env_node["defaults"] = defaults
            default_key = "schema_apex" if schema_node.get("apex") else "schema_db"
            defaults.setdefault(default_key, request.schema)

        summary = f"create or update connection {request.environment}.{request.schema}"
        return summary, self._dump_node(yaml, {request.environment: _strip_env_secrets(env_node)})

    def _merge_db_defaults(self, env_node: dict[str, Any], request: ConnectionEditRequest) -> None:
        db = env_node.get("db")
        if not isinstance(db, dict):
            db = {}
            env_node["db"] = db
        for key, value in (
            ("hostname", request.hostname),
            ("port", request.port if request.port is not None else DEFAULT_PORT),
            ("service", request.service),
            ("sid", request.sid),
        ):
            if value not in (None, "") and key not in db:
                db[key] = value

    def _merge_wallet(self, env_node: dict[str, Any], request: ConnectionEditRequest) -> None:
        if not request.wallet:
            return
        wallet = env_node.get("wallet")
        if not isinstance(wallet, dict):
            wallet = {}
            env_node["wallet"] = wallet
        wallet.setdefault("wallet", request.wallet)
        if request.wallet_password and "wallet_pwd" not in wallet:
            self._write_password(
                wallet, "wallet_pwd", "wallet_pwd!", request, request.wallet_password
            )

    def _merge_schema(self, schema_node: dict[str, Any], request: ConnectionEditRequest) -> None:
        db = schema_node.get("db")
        if not isinstance(db, dict):
            db = {}
            schema_node["db"] = db
        db.setdefault("user", request.username or request.schema)
        if request.password and "pwd" not in db:
            self._write_password(db, "pwd", "pwd!", request, request.password)

        if request.workspace:
            apex = schema_node.get("apex")
            if not isinstance(apex, dict):
                apex = {}
                schema_node["apex"] = apex
            apex.setdefault("workspace", request.workspace)
            if request.app:
                apex.setdefault("app", request.app)

        export_values = {
            "prefix": request.prefix,
            "ignore": request.ignore,
        }
        if any(value for value in export_values.values()):
            export = schema_node.get("export")
            if not isinstance(export, dict):
                export = {}
                schema_node["export"] = export
            for key, value in export_values.items():
                if not value:
                    continue
                # A blank placeholder is not a value: a connection file
                # conventionally seeds `ignore: ''` / `prefix: ''`, so
                # setdefault() made -ignore/-prefix a permanent silent no-op on
                # exactly the files that need them. A real existing value is
                # still preserved.
                if not _is_blank(export.get(key)):
                    continue
                export[key] = value

    def _clone_env(self, source: Any, request: ConnectionEditRequest) -> dict[str, Any]:
        db = _strip_secrets(_plain(source.get("db", {})) or {})
        new_env: dict[str, Any] = {"db": db}
        if "wallet" in source:
            new_env["wallet"] = _strip_secrets(_plain(source.get("wallet", {})) or {})
        if request.hostname:
            db["hostname"] = request.hostname
        if request.port is not None:
            db["port"] = request.port
        if request.service:
            db["service"] = request.service
        new_env["defaults"] = {}
        new_env["schemas"] = {}
        return new_env

    def _add_schema(self, yaml: YAML, data: Any, request: ConnectionEditRequest):
        env_node = self._require_environment(data, request.environment)
        schema = request.schema
        schemas = env_node.get("schemas")
        if not isinstance(schemas, dict):
            schemas = {}
            env_node["schemas"] = schemas
        if schema in schemas:
            raise ConnectionEditError(
                f"schema already exists: {request.environment}.{schema}"
            )
        user = request.username or schema
        db: dict[str, Any] = {"user": user}
        if request.password:
            self._write_password(db, "pwd", "pwd!", request, request.password)
        schemas[schema] = {"db": db}
        summary = f"add schema {request.environment}.{schema}"
        # Preview is structural only, never render the password.
        preview = self._dump_node(yaml, {schema: {"db": {"user": user}}})
        return summary, preview

    def _set_pwd(self, data: Any, request: ConnectionEditRequest):
        env_node = self._require_environment(data, request.environment)
        schemas = env_node.get("schemas")
        schema_node = schemas.get(request.schema) if isinstance(schemas, dict) else None
        if not isinstance(schema_node, dict):
            raise ConnectionEditError(
                f"schema not found: {request.environment}.{request.schema}"
            )
        if request.password:
            db = schema_node.get("db")
            if not isinstance(db, dict):
                db = {}
                schema_node["db"] = db
            self._write_password(db, "pwd", "pwd!", request, request.password)
        summary = f"set password for {request.environment}.{request.schema}"
        return summary, ""

    def _set_wallet_pwd(self, data: Any, request: ConnectionEditRequest):
        env_node = self._require_environment(data, request.environment)
        if request.password:
            wallet = env_node.get("wallet")
            if not isinstance(wallet, dict):
                wallet = {}
                env_node["wallet"] = wallet
            self._write_password(wallet, "wallet_pwd", "wallet_pwd!", request, request.password)
        summary = f"set wallet password for {request.environment}"
        return summary, ""

    def _write_password(
        self,
        target: dict[str, Any],
        value_key: str,
        marker_key: str,
        request: ConnectionEditRequest,
        password: str | None,
    ) -> None:
        if password is None:
            return  # pragma: no cover, dead guard: every call site already checks truthiness first
        if request.encrypt:
            try:
                key = crypto.resolve_key(request.key)
                stored = crypto.encrypt(password, key)
                recorded = crypto.fingerprint(stored, key)
            except crypto.CryptoError as error:
                raise ConnectionEditError(str(error)) from error
            target[value_key] = stored
            target[marker_key] = "Y"
            write_fingerprint(target, value_key, recorded)
            return
        target[value_key] = password
        target.pop(marker_key, None)
        # A fingerprint outliving the encrypted value it described would make
        # the loader refuse a password that is not encrypted at all.
        target.pop(fingerprint_key(value_key), None)

    def _require_environment(self, data: Any, env: str) -> Any:
        env_node = data.get(env) if isinstance(data, dict) else None
        if not isinstance(env_node, dict):
            raise ConnectionEditError(f"environment not found: {env}")
        return env_node

    def _dump_node(self, yaml: YAML, node: Any) -> str:
        buffer = io.StringIO()
        yaml.dump(node, buffer)
        return buffer.getvalue().rstrip("\n")
