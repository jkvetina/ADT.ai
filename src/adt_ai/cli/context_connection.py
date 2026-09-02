from __future__ import annotations

import importlib
from collections.abc import Callable

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
    before_versions: Callable[[], None] | None = None,
) -> dict[str, str]:
    """The `CONNECTING TO SCHEMA <schema>, <env>:` section for one connection.

    ``before_versions`` runs between the header and the version probe (ADT
    #670), which is the one point in this section where a header is on screen
    and has no body under it yet. That matters because a header's claim EXPIRES:
    `cli/stream_tracker.py` retires it at the blank line under its last row, so
    a caller's own dictionary read placed after this function returns is a
    blocking call behind a finished section, and `AnnouncedGateway.guard` records
    it. Two of them did. `patch -deploy` swept SQLcl DIFF leftovers and `patch
    -drop` read the APEX release and application list, both immediately after
    this block, and both were invisible to the suite only because a fake gateway
    reports no version and so never gives the section a body to close it.

    It is `export_db`'s own answer to the same question, one module over
    (`export_db/runner.py`: the sweep "runs under the overview header and
    reports under the overview table"). Jan settled the rule it implements in
    `#372`: *"I did not asked you to ADD NEW HEADERS, I asked you to print
    PRECEEDING header!"* -- so a silent read moves under a header that already
    exists rather than earning a label, and an empty section is never the fix.

    The callback must not PRINT. Anything it writes lands between the header and
    the version rows, which would put the section's own body under somebody
    else's. A caller with something to report does the READ here and prints
    afterwards, which is exactly the split `export_db` draws.
    """
    resolved_schema = schema or str(getattr(connection, "schema", "APP"))
    resolved_environment = environment or str(getattr(connection, "environment", "DEV"))
    print_adt_header(
        f"CONNECTING TO SCHEMA {schema_label(resolved_schema)}, {resolved_environment}:"
    )
    if before_versions is not None:
        before_versions()
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
