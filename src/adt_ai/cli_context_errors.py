from __future__ import annotations

import sys

from adt_ai.cli_constants import DROPBOX_PATH_RE


def _is_user_database_error(error: Exception) -> bool:
    text = str(error)
    if any(marker in text for marker in ("DPY-", "DPI-", "ORA-", "TNS-")):
        return True
    module = type(error).__module__
    return module.startswith("oracledb")

def _is_database_connection_error(error: Exception) -> bool:
    text = str(error)
    connection_markers = (
        "DPY-",
        "DPI-",
        "TNS-",
        "ORA-01017",
        "ORA-12154",
        "ORA-12514",
        "ORA-12541",
        "ORA-12545",
        "ORA-12560",
        "Connect failed",
        "connection",
        "listener",
        "tnsnames.ora",
        "wallet",
    )
    return any(marker.lower() in text.lower() for marker in connection_markers)

def _display(value: object) -> str:
    return DROPBOX_PATH_RE.sub("Dropbox/", str(value))

def _print_database_error(error: Exception) -> None:
    # A failing query attaches its SQL to the exception (OracleGateway). When the
    # SQL is present the failure happened *after* connecting, so it is a query
    # error, not a connection failure — show the offending query and a query
    # banner. Otherwise classify by message markers (TNS/wallet/credential codes).
    sql = getattr(error, "adt_sql", None)
    is_connection = sql is None and _is_database_connection_error(error)
    header = "DATABASE CONNECTION FAILED" if is_connection else "DATABASE QUERY FAILED"
    print(file=sys.stderr)
    print(header, file=sys.stderr)
    print("-" * len(header), file=sys.stderr)
    if sql is not None:
        print("Query:", file=sys.stderr)
        print(_display(sql), file=sys.stderr)
        print(file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    if is_connection:
        print(
            "Check the connection file and wallet under ADT.ai connections/wallets, then rerun.",
            file=sys.stderr,
        )
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)

def _print_config_error(error: Exception) -> None:
    print(file=sys.stderr)
    print("CONFIGURATION NOT FOUND", file=sys.stderr)
    print("-----------------------", file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Run ADT.ai from a project folder that has a connection file, or pass "
        "-config-dir / -root to point at one. See USAGE.md and `adtai doctor -init`.",
        file=sys.stderr,
    )
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)

def _print_unexpected_error(error: Exception) -> None:
    # Catch-all for any failure that is not a recognised config/database error.
    # The command banner has already printed (it is the first handler statement),
    # so this only adds a friendly framing instead of leaking a raw traceback.
    header = "UNEXPECTED ERROR"
    print(file=sys.stderr)
    print(header, file=sys.stderr)
    print("-" * len(header), file=sys.stderr)
    print(f"{type(error).__name__}: {_display(error)}", file=sys.stderr)
    print(file=sys.stderr)
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)
