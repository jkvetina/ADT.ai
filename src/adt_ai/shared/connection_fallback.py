"""Every environment after the first falls back to the first one (ADT #1078).

The first environment in a connection file, DEV on most projects, carries the
schemas, users and passwords. TEST and STAGE only state what differs, usually
the server, so a schema added to DEV reaches them all. What an environment
takes is decided key by key, except for three sets that only mean something
together:

- Where the database is. An environment naming its own server or wallet takes
  none of DEV's, or a DEV `tns:`, `ords:` or wallet left beside TEST's own
  hostname would quietly connect TEST work to DEV. `port` stays outside the
  set: it is the same 1521 on nearly every server.
- How the password is supplied. A TEST `pwd:` beside DEV's `pwd!:` marker would
  be decrypted as if it were ciphertext, and a TEST password beside DEV's
  `pwd_cmd:` is refused as two sources for one secret.
- The recorded SQLcl connection, which names DEV's database and is never taken.
"""

from __future__ import annotations

from typing import Any

from adt_ai.shared.dict_merge import deep_merge

SERVER_KEYS = frozenset(
    {
        "hostname",
        "service",
        "sid",
        "tns",
        "ords",
        "wallet",
        "wallet_path",
        "wallet_password",
        "wallet_password!",
        "wallet_password_key",
        "wallet_password_cmd",
        "wallet_pwd",
        "wallet_pwd!",
        "wallet_pwd_key",
        "wallet_pwd_cmd",
    }
)
PASSWORD_KEYS = frozenset({"pwd", "pwd!", "pwd_key", "pwd_cmd", "pwd_pattern", "auth"})
NEVER_INHERITED = frozenset({"sqlcl"})


def base_environment(data: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The first environment, or `None` when `name` is the first one itself.

    `None` keeps the first environment resolving exactly as it always has.
    """
    for key, node in data.items():
        if isinstance(node, dict):
            return None if key == name else node
    return None


def schemas_of(environment: dict[str, Any]) -> dict[str, Any]:
    schemas = environment.get("schemas")
    return schemas if isinstance(schemas, dict) else {}


def db_layers(environment: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    db = deep_merge(environment.get("db") or {}, environment.get("wallet") or {})
    return deep_merge(db, schema.get("db") or {})


def filled(block: dict[str, Any]) -> dict[str, Any]:
    # A key left blank is a placeholder still to fill, not a value, so it falls
    # back like a missing one rather than blanking DEV's.
    return {key: value for key, value in block.items() if value not in (None, "")}


def pattern_serves(environment: dict[str, Any], base: dict[str, Any] | None) -> bool:
    """Whether `pwd_pattern:` lets a schema the file does not list connect (#1082).

    The pattern exists so a file need not list its schemas, so an unlisted one
    connects as itself whenever its environment, or the first one it falls back
    to, carries a pattern.
    """
    db = db_layers(environment, {})
    if base is not None:
        db = with_base(db_layers(base, {}), db)
    return bool(db.get("pwd_pattern"))


def with_base(base: dict[str, Any], own: dict[str, Any]) -> dict[str, Any]:
    own = filled(own)
    dropped = set(NEVER_INHERITED)
    for group in (SERVER_KEYS, PASSWORD_KEYS):
        if group & own.keys():
            dropped |= group
    base = {key: value for key, value in base.items() if key not in dropped}
    return deep_merge(base, own)
