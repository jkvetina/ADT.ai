from __future__ import annotations

import re
from pathlib import Path

import oracledb

from adt_ai.cli.constants import DROPBOX_PATH_RE
from adt_ai.export_db.failures import ExportObjectsFailedError
from adt_ai.shared.config import InvalidConfigValueError
from adt_ai.shared.connection_errors import (
    ConnectFailedError,
    CredentialUnavailableError,
    InvalidConnectionError,
)
from adt_ai.shared.error_screen import print_adt_error

# The remedy for a configuration nothing could locate, and the line ADT #407 is
# about: it is noise above every failure where the file WAS found and read.
# Wrapped by hand rather than left as one long line: the body is indented two
# columns now, so an 88-character remedy wrapped on an 80-column terminal and
# the continuation landed flush left, outside the block it belongs to (#764).
_PROJECT_FOLDER_REMEDY = (
    "1) run ADT.ai from a project folder that has a connection file\n"
    "2) or pass -config-dir / -root to point at one"
)

# Code and remedy both branch on the error CLASS, so a raise site says which
# screen it wants by choosing its exception and nothing here has to recognise a
# message. Most specific first; a class matching no row is a configuration that
# could not be located, the one case the remedy above is written for.
_CONFIG_ERROR_SCREENS: tuple[tuple[type[Exception], str, str | None], ...] = (
    (CredentialUnavailableError, "CREDENTIAL UNAVAILABLE", None),
    (InvalidConnectionError, "CONFIGURATION INVALID", None),
    (InvalidConfigValueError, "CONFIGURATION INVALID", None),
)


_DATABASE_ERROR_CODE_RE = re.compile(
    r"(?<![\w-])(?:ORA-\d{5}|TNS-\d{5}|DPY-\d{4}|DPI-\d{4})(?=[:\s]|$)",
    re.IGNORECASE,
)

_CONNECTION_ERROR_CODES = frozenset(
    {
        "ORA-01017",
        "ORA-12154",
        "ORA-12514",
        "ORA-12541",
        "ORA-12545",
        "ORA-12560",
        "DPY-4011",
        "DPY-4026",
        "DPY-6000",
        "DPY-6001",
        "DPY-6002",
        "DPY-6003",
        "DPY-6004",
        "DPY-6005",
        "DPY-6006",
        "DPI-1047",
        "DPI-1072",
        "TNS-12154",
        "TNS-12514",
        "TNS-12541",
        "TNS-12545",
        "TNS-12560",
    }
)


def _database_error_codes(error: Exception) -> set[str]:
    return {match.group(0).upper() for match in _DATABASE_ERROR_CODE_RE.finditer(str(error))}


def _is_user_database_error(error: Exception) -> bool:
    if isinstance(error, oracledb.Error):
        return True
    return bool(_database_error_codes(error))


def _is_database_connection_error(error: Exception) -> bool:
    # A connect attempt ADT.ai itself declared failed needs no message reading,
    # and must not depend on one: the SQLcl transcript carries no ORA code.
    if isinstance(error, ConnectFailedError):
        return True
    return bool(_database_error_codes(error) & _CONNECTION_ERROR_CODES)


def _display(value: object) -> str:
    return DROPBOX_PATH_RE.sub("Dropbox/", str(value))


def _project_relative(path: Path, root: Path) -> str:
    """A path as the reader thinks of it: relative to the project root (ADT #415).

    `_display` above only shortens a Dropbox prefix, which leaves a path that is
    neither absolute nor relative to anything, and long enough to wrap. Every
    caller already knows the root the run was given, so the honest rendering is
    the part below it.

    An unrelated path falls back to `_display` rather than raising: this runs at
    a print site, and a patch that is already written on disk must not die on the
    line that says where it landed.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return _display(path)


def _about(subject: str | None, lines: list[str]) -> list[str]:
    """The description, nested under the object it is about when there is one.

    `export_db` reports its refusals after the listing (`#917`), where the query
    alone binds `:object_name` and never says which object that was.
    """
    if subject is None:
        return lines
    return [subject, *(f"  {line}".rstrip() for line in lines)]


def _print_export_failures(
    error: ExportObjectsFailedError, *, debug_available: bool = True
) -> None:
    """One screen per object the export could not write, in listing order.

    Each takes the screen its own error would have taken had it ended the run,
    so a refused DDL pull is still `DATABASE QUERY FAILED` with its query, and
    a normalizer defect is still `UNEXPECTED ERROR`. The `-debug` hint closes
    the last screen only: one flag answers all of them.
    """
    last = len(error.failures) - 1
    for index, failure in enumerate(error.failures):
        hint = debug_available and index == last
        if _is_user_database_error(failure.error):
            _print_database_error(failure.error, debug_available=hint, subject=failure.label)
        else:
            _print_unexpected_error(failure.error, debug_available=hint, subject=failure.label)


def _print_database_error(
    error: Exception, *, debug_available: bool = True, subject: str | None = None
) -> None:
    # A failing query attaches its SQL to the exception (OracleGateway). When the
    # SQL is present the failure happened *after* connecting, so it is a query
    # error, not a connection failure, show the offending query and a query
    # code. Otherwise classify by message markers (TNS/wallet/credential codes).
    #
    # The error leads and the SQL follows it (ADT #764). It was the other way
    # round, which put a statement long enough to wrap between the header and
    # the one line saying what went wrong.
    sql = getattr(error, "adt_sql", None)
    is_connection = sql is None and _is_database_connection_error(error)
    code = "DATABASE CONNECTION FAILED" if is_connection else "DATABASE QUERY FAILED"
    details: list[str] = []
    if sql is not None:
        details.append("Query:")
        details.extend(f"  {line}" for line in _display(sql).splitlines())
    if is_connection:
        details.extend([
            "1) check the connection file and wallet under ADT.ai connections/wallets",
            "2) rerun",
        ])
    print_adt_error(
        code,
        _about(subject, _display(error).splitlines()),
        details or None,
        debug_available=debug_available,
    )


def _config_error_screen(error: Exception) -> tuple[str, str | None]:
    for error_type, code, remedy in _CONFIG_ERROR_SCREENS:
        if isinstance(error, error_type):
            return code, remedy
    return "CONFIGURATION NOT FOUND", _PROJECT_FOLDER_REMEDY


def _print_config_error(error: Exception, *, debug_available: bool = True) -> None:
    # A configuration that cannot be LOCATED and one that was found and read are
    # different failures. ADT #182 split the first pair (a missing config file
    # against a `path_objects` value ADT cannot use) and ADT #407 finished the
    # job for connections, where every error still took the not-found screen:
    # the "run from a project folder" remedy is noise when the file is sitting
    # right there, and the header sent the reader hunting for one that was
    # already read.
    #
    # Branching on classes rather than on each raise means a new error is
    # reported right by inheriting the class whose screen it wants, without
    # touching this file. A connect attempt that was actually made is not a
    # configuration failure at all, so it goes to the shared database banner,
    # whose wallet and credential advice is the remedy that fits it.
    if isinstance(error, ConnectFailedError):
        _print_database_error(error, debug_available=debug_available)
        return
    code, remedy = _config_error_screen(error)
    print_adt_error(code, _display(error), remedy, debug_available=debug_available)


def _print_sqlcl_error(error: Exception, *, debug_available: bool = True) -> None:
    # SQLcl exiting non-zero is a failure ADT.ai raises on purpose, so it gets a
    # code that says so rather than the internal-surprise catch-all. The
    # message IS the captured transcript: printed on its own lines, never after
    # a type name, because whatever line lands beside the type reads as the
    # diagnosis (ADT #271).
    print_adt_error("SQLCL SCRIPT FAILED", _display(error), debug_available=debug_available)


#: The two builtin types ADT.ai raises with a message of its own, each named on
#: its own header rather than under `UNEXPECTED ERROR:` (ADT #966).
_NAMED_ERRORS: dict[type[Exception], str] = {
    ValueError   : "VALUE ERROR",
    RuntimeError : "RUNTIME ERROR",
}


def _print_unexpected_error(
    error: Exception, *, debug_available: bool = True, subject: str | None = None
) -> None:
    # Catch-all for any failure that is not a recognised config/database error.
    # The command banner has already printed (it is the first handler statement),
    # so this only adds a friendly framing instead of leaking a raw traceback.
    message = _display(error)
    named = _NAMED_ERRORS.get(type(error))
    if named is not None:
        # The header names the type, so the body is the message alone (ADT #966).
        print_adt_error(
            named, _about(subject, message.splitlines()), debug_available=debug_available
        )
        return
    if "\n" in message:
        # A multi-line message is a captured transcript, not a sentence. Gluing
        # the type onto its first line makes that line the reported cause --
        # `RuntimeError: Connection <name> has been deleted` above the SP2-0556
        # that actually failed the run (ADT #271).
        description = [f"{type(error).__name__}:", *message.splitlines()]
    else:
        description = [f"{type(error).__name__}: {message}"]
    print_adt_error(
        "UNEXPECTED ERROR", _about(subject, description), debug_available=debug_available
    )
