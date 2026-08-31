"""Parse and run ``config/STARTUP.sql`` on every new database connection.

``STARTUP.sql`` is an optional, per-project script of session setup that should
run once on each fresh connection (NLS settings, ``ALTER SESSION`` tuning, a
``DBMS_SESSION.SET_IDENTIFIER`` block, and so on). It is authored as a normal
SQL*Plus/SQLcl script, so it mixes three statement kinds that must be handled
differently when replayed through python-oracledb:

- **SQL*Plus directives** (``SET SERVEROUTPUT ON``, ``SET DEFINE OFF`` …) are
  client-side commands the database never sees. python-oracledb cannot execute
  them, so they are filtered out. The meaningful ones are emulated server-side
  (``SET SERVEROUTPUT ON`` → ``DBMS_OUTPUT.ENABLE``).
- **Session SQL** (``ALTER SESSION SET ...``) runs verbatim, with the trailing
  ``;`` stripped (oracledb rejects a statement terminator).
- **PL/SQL blocks** (``BEGIN ... END;`` / ``DECLARE ...`` / ``CREATE ...``) run
  as a single statement, terminated by a lone ``/`` line which is stripped.

The SQLcl deploy path consumes ``STARTUP.sql`` natively, so it is injected
verbatim there; only the python-oracledb path needs this parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Second token of a ``SET`` line that marks it as a SQL*Plus client directive
# rather than server SQL. ``SET TRANSACTION`` is intentionally absent: it is real
# SQL and must reach the database.
_SQLPLUS_SET_OPTIONS = frozenset(
    {
        "APPINFO", "ARRAYSIZE", "AUTOCOMMIT", "AUTOPRINT", "AUTORECOVERY",
        "AUTOTRACE", "BLOCKTERMINATOR", "CMDSEP", "COLSEP", "CONCAT",
        "COPYCOMMIT", "DEFINE", "ECHO", "EDITFILE", "EMBEDDED", "ERRORLOGGING",
        "ESCAPE", "ESCCHAR", "EXITCOMMIT", "FEEDBACK", "FLAGGER", "FLUSH",
        "HEADING", "HEADSEP", "INSTANCE", "LINESIZE", "LOBOFFSET", "LOGSOURCE",
        "LONG", "LONGCHUNKSIZE", "MARKUP", "NEWPAGE", "NULL", "NUMFORMAT",
        "NUMWIDTH", "PAGESIZE", "PAUSE", "RECSEP", "RECSEPCHAR", "SECUREDCOL",
        "SERVEROUTPUT", "SHIFTINOUT", "SHOWMODE", "SQLBLANKLINES", "SQLCASE",
        "SQLCONTINUE", "SQLNUMBER", "SQLPLUSCOMPATIBILITY", "SQLPREFIX",
        "SQLPROMPT", "SQLTERMINATOR", "SUFFIX", "TAB", "TERMOUT", "TIME",
        "TIMING", "TRIMOUT", "TRIMSPOOL", "UNDERLINE", "VERIFY", "WRAP",
        "XQUERY",
    }
)

# First token of other single-line SQL*Plus client commands that never reach the
# database and are skipped entirely.
_SQLPLUS_COMMANDS = frozenset(
    {
        "ACCEPT", "BTITLE", "CLEAR", "COL", "COLUMN", "COMPUTE", "DEFINE",
        "ECHO", "PAUSE", "PROMPT", "REM", "REMARK", "REPFOOTER", "REPHEADER",
        "SHOW", "SPOOL", "TTITLE", "UNDEFINE", "WHENEVER",
    }
)

_PLSQL_START = re.compile(
    r"^\s*(DECLARE|BEGIN|CREATE\s+(OR\s+REPLACE\s+)?"
    r"(EDITIONABLE\s+|NONEDITIONABLE\s+)?"
    r"(PACKAGE|PROCEDURE|FUNCTION|TRIGGER|TYPE)\b)",
    re.IGNORECASE,
)

_TRAILING_TERMINATOR = re.compile(r";\s*$")


@dataclass(frozen=True)
class Statement:
    """One parsed statement and where it began (1-based) for error context."""

    kind: str  # "sqlplus" | "sql" | "plsql"
    text: str  # executable text (terminator stripped) or the raw directive line
    line: int


@dataclass
class StartupResult:
    """What ``apply_startup`` did, for verbose reporting."""

    executed: list[Statement] = field(default_factory=list)
    emulated: list[Statement] = field(default_factory=list)
    skipped: list[Statement] = field(default_factory=list)


class StartupError(RuntimeError):
    """A STARTUP.sql statement failed; carries line context (fail-fast)."""

    def __init__(self, statement: Statement, error: Exception) -> None:
        first_line = statement.text.splitlines()[0] if statement.text else ""
        super().__init__(
            f"STARTUP.sql statement at line {statement.line} failed: {error}\n"
            f"  {first_line}"
        )
        self.statement = statement
        self.error = error


def _sqlplus_directive(line: str) -> bool:
    tokens = line.split()
    if not tokens:
        return False
    head = tokens[0].upper()
    if head == "SET":
        return len(tokens) >= 2 and tokens[1].upper() in _SQLPLUS_SET_OPTIONS
    return head in _SQLPLUS_COMMANDS


def split_statements(text: str) -> list[Statement]:
    """Split a SQL*Plus/SQLcl script into executable statements.

    Mirrors SQLcl terminator rules: a lone ``/`` ends a PL/SQL block, a trailing
    ``;`` ends a plain SQL statement (never split inside a PL/SQL block), and
    blank lines plus full-line comments between statements are ignored.
    """
    statements: list[Statement] = []
    buffer: list[str] = []
    start_line = 0
    in_plsql = False

    def flush(kind: str) -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body:
            statements.append(Statement(kind=kind, text=body, line=start_line))
        buffer = []

    for index, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()

        if not buffer:
            if not stripped or stripped.startswith("--"):
                continue
            if _sqlplus_directive(stripped):
                statements.append(Statement(kind="sqlplus", text=stripped, line=index))
                continue
            start_line = index
            in_plsql = bool(_PLSQL_START.match(line))

        if stripped == "/":
            flush("plsql" if in_plsql else "sql")
            in_plsql = False
            continue

        buffer.append(line)

        if not in_plsql and stripped.endswith(";"):
            buffer[-1] = _TRAILING_TERMINATOR.sub("", buffer[-1])
            flush("sql")

    if any(part.strip() for part in buffer):
        flush("plsql" if in_plsql else "sql")

    return statements


def _emulation_for(statement: Statement) -> str | None:
    """Server-side equivalent for a meaningful SQL*Plus directive, else ``None``.

    Only ``SET SERVEROUTPUT`` carries over to a python-oracledb session; the rest
    (DEFINE, TIMING, SQLBLANKLINES …) are pure client concerns and are skipped.
    """
    tokens = statement.text.split()
    if len(tokens) >= 2 and tokens[0].upper() == "SET" and tokens[1].upper() == "SERVEROUTPUT":
        value = tokens[2].upper() if len(tokens) >= 3 else "ON"
        if value == "OFF":
            return "BEGIN DBMS_OUTPUT.DISABLE; END;"
        return "BEGIN DBMS_OUTPUT.ENABLE(NULL); END;"
    return None


def apply_startup(connection: Any, text: str) -> StartupResult:
    """Replay ``STARTUP.sql`` against an open connection, fail-fast on error.

    Returns a :class:`StartupResult` describing executed / emulated / skipped
    statements. Any database error aborts immediately with line context.
    """
    result = StartupResult()
    cursor = connection.cursor()
    try:
        for statement in split_statements(text):
            if statement.kind == "sqlplus":
                emulation = _emulation_for(statement)
                if emulation is None:
                    result.skipped.append(statement)
                    continue
                _execute(cursor, emulation, statement)
                result.emulated.append(statement)
                continue
            _execute(cursor, statement.text, statement)
            result.executed.append(statement)
    finally:
        close = getattr(cursor, "close", None)
        if callable(close):
            close()
    return result


def _execute(cursor: Any, sql: str, statement: Statement) -> None:
    try:
        cursor.execute(sql)
    except Exception as error:  # noqa: BLE001 - re-raised with line context
        raise StartupError(statement, error) from error
