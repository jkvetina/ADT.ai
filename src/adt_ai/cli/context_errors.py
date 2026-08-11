from __future__ import annotations

import sys

from adt_ai.cli.constants import DROPBOX_PATH_RE, print_adt_header
from adt_ai.shared.config import InvalidConfigValueError


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
    header = "DATABASE CONNECTION FAILED:" if is_connection else "DATABASE QUERY FAILED:"
    print_adt_header(header, file=sys.stderr)
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
    # A config file that cannot be located and a config *value* that cannot be
    # used are different failures: the "run from a project folder" remedy is
    # noise on a bad value, and `CONFIGURATION NOT FOUND` above an unresolved
    # `path_objects` placeholder sends the reader hunting for a missing file.
    # Branching on the base class rather than on each subclass means a new
    # invalid-value error (`ut_pattern`, `ut_match`, `ut_module`) is reported
    # right without touching this file.
    is_invalid_value = isinstance(error, InvalidConfigValueError)
    header = "CONFIGURATION INVALID:" if is_invalid_value else "CONFIGURATION NOT FOUND:"
    print_adt_header(header, file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    if not is_invalid_value:
        print(
            "Run ADT.ai from a project folder that has a connection file, or pass "
            "-config-dir / -root to point at one. See USAGE.md and `adtai doctor -init`.",
            file=sys.stderr,
        )
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)

def _print_sqlcl_error(error: Exception) -> None:
    # SQLcl exiting non-zero is a failure ADT.ai raises on purpose, so it gets a
    # banner that says so rather than the internal-surprise catch-all. The
    # message IS the captured transcript: printed on its own lines, never after
    # a type name, because whatever line lands beside the type reads as the
    # diagnosis (ADT #271).
    print_adt_header("SQLCL SCRIPT FAILED:", file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)

def _print_unexpected_error(error: Exception) -> None:
    # Catch-all for any failure that is not a recognised config/database error.
    # The command banner has already printed (it is the first handler statement),
    # so this only adds a friendly framing instead of leaking a raw traceback.
    print_adt_header("UNEXPECTED ERROR:", file=sys.stderr)
    message = _display(error)
    if "\n" in message:
        # A multi-line message is a captured transcript, not a sentence. Gluing
        # the type onto its first line makes that line the reported cause --
        # `RuntimeError: Connection <name> has been deleted` above the SP2-0556
        # that actually failed the run (ADT #271).
        print(f"{type(error).__name__}:", file=sys.stderr)
        print(message, file=sys.stderr)
    else:
        print(f"{type(error).__name__}: {message}", file=sys.stderr)
    print(file=sys.stderr)
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)
