from __future__ import annotations

import importlib

from adt_ai.cli.constants import (
    APEX_VERSION_QUERY,
    DATABASE_VERSION_OLD_QUERY,
    DATABASE_VERSION_QUERY,
    QueryGateway,
    print_adt_header,
)
from adt_ai.cli.context_errors import _is_database_connection_error
from adt_ai.shared.progress import schema_label


def _print_connection_block(
    gateway: QueryGateway,
    connection: object | None,
    *,
    schema: str | None = None,
    environment: str | None = None,
    debug: bool = False,
) -> dict[str, str]:
    resolved_schema = schema or str(getattr(connection, "schema", "APP"))
    resolved_environment = environment or str(getattr(connection, "environment", "DEV"))
    print_adt_header(
        f"CONNECTING TO SCHEMA {schema_label(resolved_schema)}, {resolved_environment}:"
    )
    return _print_connection_versions(gateway, connection, debug=debug)

def _print_connection_versions(
    gateway: QueryGateway,
    connection: object | None,
    *,
    debug: bool = False,
) -> dict[str, str]:
    # in -debug mode surface probe failures instead of silently swallowing them
    ignore_errors = not debug
    versions: dict[str, str] = {}
    apex_version = _fetch_version(gateway, APEX_VERSION_QUERY, ignore_errors=ignore_errors)
    if apex_version:
        versions["APEX"] = apex_version

    database_version = _fetch_version(gateway, DATABASE_VERSION_QUERY, ignore_errors=ignore_errors)
    if not database_version:
        database_version = _fetch_version(
            gateway, DATABASE_VERSION_OLD_QUERY, ignore_errors=ignore_errors
        )
    if database_version:
        versions["DATABASE"] = database_version

    thick = getattr(connection, "thick", False)
    if thick:
        versions["THICK"] = _oracle_client_version() or "Y"

    if not versions:
        return versions
    for key in sorted(versions):
        print(f"{key:>18} | {versions[key]}")
    print()
    return versions

def _oracle_client_version() -> str:
    try:
        version = importlib.import_module("oracledb").clientversion()
    except Exception:
        return ""
    if not version:
        return ""
    if isinstance(version, tuple) and len(version) >= 2:
        return f"{version[0]}.{version[1]}"
    return str(version)

def _fetch_version(
    gateway: QueryGateway,
    sql: str,
    ignore_errors: bool = False,
) -> str | None:
    try:
        rows = gateway.fetch_all(sql)
    except KeyboardInterrupt:
        raise
    except Exception as error:
        # A connect/auth failure (the lazy connect fires on the first probe) is
        # never a benign feature-probe miss: surface it inside the CONNECTING
        # block instead of swallowing it and letting it resurface later at the
        # dependency refresh. Only genuine probe misses degrade gracefully.
        if ignore_errors and not _is_database_connection_error(error):
            return None
        raise
    if not rows:
        return None
    row = rows[0]
    return str(row.get("VERSION") or row.get("version") or "") or None
