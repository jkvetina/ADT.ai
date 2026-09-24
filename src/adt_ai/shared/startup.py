"""Parse and run ``config/STARTUP.sql`` on every new database connection.

``STARTUP.sql`` is an optional, per-project script of session setup that should
run once on each fresh connection (NLS settings, ``ALTER SESSION`` tuning, a
``DBMS_SESSION.SET_IDENTIFIER`` block, and so on). It is authored as a normal
SQL*Plus/SQLcl script, so it mixes three statement kinds that must be handled
differently when replayed through python-oracledb:

- **SQL*Plus and SQLcl directives** (``SET SERVEROUTPUT ON``, ``SET SQLFORMAT
  ANSICONSOLE``, ``VAR`` …) are client-side commands the database never sees.
  python-oracledb cannot execute them, so they are filtered out. The meaningful
  ones are emulated server-side (``SET SERVEROUTPUT ON`` →
  ``DBMS_OUTPUT.ENABLE``, ``EXEC call`` → ``BEGIN call; END;``).
- **Session SQL** (``ALTER SESSION SET ...``) runs verbatim, with the trailing
  ``;`` stripped (oracledb rejects a statement terminator).
- **PL/SQL blocks** (``BEGIN ... END;`` / ``DECLARE ...`` / ``CREATE ...``) run
  as a single statement, terminated by a lone ``/`` line which is stripped.

A script include (``@file``, ``@@file``, ``START file``) is the one thing only
SQLcl can do. It is split off as a statement of its own line, so the database
refuses it there, naming that line, instead of it swallowing the next statement.

The SQLcl deploy path consumes ``STARTUP.sql`` natively, so it is injected
verbatim there; only the python-oracledb path needs this parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from adt_ai.shared import queries

# Second token of the ``SET`` lines that are SQL, not client settings: the three
# statements the SQL reference spells with ``SET``. Every other ``SET <word>`` is
# a SQL*Plus or SQLcl setting. It was an allowlist of SQL*Plus options until
# ADT #923, so SQLcl's own (``SQLFORMAT``, ``STATUSBAR``, ``HIGHLIGHTING``,
# ``ENCODING``, ``HISTORY``, ``DDL`` …) read as unterminated SQL and swallowed
# the statement below them: every python-oracledb connect failed on a file SQLcl
# ran happily.
_SQL_SET_STATEMENTS = frozenset({"CONSTRAINT", "CONSTRAINTS", "ROLE", "TRANSACTION"})

# First token of other single-line SQL*Plus and SQLcl client commands that never
# reach the database and are skipped entirely.
_SQLPLUS_COMMANDS = frozenset(
    {
        "ACCEPT", "ALIAS", "BTITLE", "CD", "CLEAR", "COL", "COLUMN", "COMPUTE",
        "DEFINE", "ECHO", "EXEC", "EXECUTE", "FORMAT", "HISTORY", "INFO",
        "INFORMATION", "PAUSE", "PRINT", "PROMPT", "REM", "REMARK", "REPFOOTER",
        "REPHEADER", "SHOW", "SPOOL", "TTITLE", "UNDEFINE", "VAR", "VARIABLE",
        "WHENEVER",
    }
)

# `EXECUTE statement` is SQL*Plus shorthand for `BEGIN statement; END;`, so it is
# emulated rather than skipped: the session gets the same call on both paths.
_EXEC_COMMANDS = frozenset({"EXEC", "EXECUTE"})

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
        # A short uppercase headline, the database's own words under it (ADT #934).
        super().__init__(
            f"STARTUP.sql FAILED AT LINE {statement.line}\n\n"
            f"{error}\n"
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
        return len(tokens) >= 2 and tokens[1].upper() not in _SQL_SET_STATEMENTS
    return head in _SQLPLUS_COMMANDS


def _script_include(line: str) -> bool:
    """``@file``, ``@@file`` or ``START file``: another script, run by SQLcl only."""
    tokens = line.split()
    return bool(tokens) and (line.startswith("@") or tokens[0].upper() == "START")


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
            if _script_include(stripped):
                # One line, one statement: sent on its own, the database refuses
                # it with this line's number rather than with the next one's.
                include = _TRAILING_TERMINATOR.sub("", stripped)
                statements.append(Statement(kind="sql", text=include, line=index))
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

    Two carry over to a python-oracledb session: ``SET SERVEROUTPUT``, and
    ``EXEC``, which SQL*Plus itself runs as ``BEGIN statement; END;``. The rest
    (DEFINE, TIMING, SQLFORMAT, VAR …) are pure client concerns and are skipped.
    """
    tokens = statement.text.split()
    if len(tokens) >= 2 and tokens[0].upper() in _EXEC_COMMANDS:
        call = _TRAILING_TERMINATOR.sub("", statement.text.split(None, 1)[1]).strip()
        return f"BEGIN {call}; END;"
    if len(tokens) >= 2 and tokens[0].upper() == "SET" and tokens[1].upper() == "SERVEROUTPUT":
        # `SET SERVEROUTPUT OFF;` is as valid as the bare form, and its `;` made
        # the value read `OFF;`, which enabled the output it switched off.
        value = tokens[2].upper().rstrip(";") if len(tokens) >= 3 else "ON"
        if value == "OFF":
            return queries.DBMS_OUTPUT_DISABLE_BLOCK
        return queries.DBMS_OUTPUT_ENABLE_BLOCK
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
