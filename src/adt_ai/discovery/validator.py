"""SELECT-only statement validation for the discovery module.

This is the static half of discovery's narrow SQL surface: it admits the shape
of one ``SELECT`` (or ``WITH ... SELECT``) and rejects direct write statements,
PL/SQL blocks, inline ``WITH`` PL/SQL, row locking and multiple statements.
Comments and string literals are scrubbed before the keyword scan so payloads
such as ``'DROP TABLE x'`` or ``-- delete`` cannot false-trigger.

It is not a parser or a sandbox. Oracle permits stored functions in a SELECT and
discovery deliberately permits them; an autonomous-transaction function can
commit outside the caller's read-only transaction. The database user's grants
remain the security boundary for callable code.
"""

from __future__ import annotations

import re

ALLOWED_LEADING = {"SELECT", "WITH"}

# First-token keyword -> human family, used for precise rejection messages.
_FAMILY_BY_KEYWORD = {
    # DML
    "INSERT": "DML",
    "UPDATE": "DML",
    "DELETE": "DML",
    "MERGE": "DML",
    "UPSERT": "DML",
    "LOCK": "DML",
    # DDL
    "CREATE": "DDL",
    "ALTER": "DDL",
    "DROP": "DDL",
    "TRUNCATE": "DDL",
    "RENAME": "DDL",
    "COMMENT": "DDL",
    "FLASHBACK": "DDL",
    "PURGE": "DDL",
    "AUDIT": "DDL",
    "NOAUDIT": "DDL",
    "ANALYZE": "DDL",
    "ASSOCIATE": "DDL",
    "DISASSOCIATE": "DDL",
    # DCL
    "GRANT": "DCL",
    "REVOKE": "DCL",
    # TCL
    "COMMIT": "TCL",
    "ROLLBACK": "TCL",
    "SAVEPOINT": "TCL",
    "SET": "TCL",
    # PL/SQL
    "BEGIN": "PL/SQL",
    "DECLARE": "PL/SQL",
    "CALL": "PL/SQL",
    "EXEC": "PL/SQL",
    "EXECUTE": "PL/SQL",
}

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9$#]*")
_FOR_UPDATE_RE = re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE)
_WITH_PLSQL_RE = re.compile(r"\bWITH\s+(?:FUNCTION|PROCEDURE)\b", re.IGNORECASE)

_Q_CLOSERS = {"(": ")", "[": "]", "{": "}", "<": ">"}


class DiscoveryValidationError(Exception):
    """Raised when a discovery statement is outside the SELECT-only surface."""

    def __init__(self, message: str, *, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def validate_select_only(sql: str) -> str:
    """Validate that ``sql`` has the shape of a single SELECT.

    Returns the statement trimmed of surrounding whitespace and a single trailing
    semicolon, ready to execute. Raises :class:`DiscoveryValidationError` (with a
    stable ``reason``) for anything outside the accepted SELECT/WITH surface.
    """
    if not sql or not sql.strip():
        raise DiscoveryValidationError("empty statement", reason="empty")

    analysis = _scrub(sql).strip()
    analysis = _strip_trailing_semicolon(analysis)
    if not analysis:
        raise DiscoveryValidationError("empty statement", reason="empty")

    # Classify the leading keyword first so PL/SQL blocks (whose internal
    # semicolons would otherwise look like multiple statements) are reported as
    # PL/SQL rather than as a statement-count error.
    match = _WORD_RE.search(analysis)
    if match is None:
        raise DiscoveryValidationError("empty statement", reason="empty")

    leading = match.group(0).upper()
    if leading not in ALLOWED_LEADING:
        family = _FAMILY_BY_KEYWORD.get(leading, "non-SELECT")
        raise DiscoveryValidationError(
            f"{family} statement '{leading}' is not allowed; discovery permits SELECT only",
            reason=family,
        )

    if _WITH_PLSQL_RE.search(analysis):
        raise DiscoveryValidationError(
            "inline PL/SQL (WITH FUNCTION/PROCEDURE) is not allowed",
            reason="PL/SQL",
        )

    # A WITH clause may front any statement, not just a SELECT (e.g.
    # ``WITH t AS (...) DELETE FROM t``). Resolve the real statement keyword that
    # follows the CTE list and reject it if it is a known write family.
    if leading == "WITH":
        main = _main_keyword_after_with(analysis)
        if main is not None and main in _FAMILY_BY_KEYWORD:
            family = _FAMILY_BY_KEYWORD[main]
            raise DiscoveryValidationError(
                f"{family} statement '{main}' after a WITH clause is not allowed; "
                "discovery permits SELECT only",
                reason=family,
            )

    if _FOR_UPDATE_RE.search(analysis):
        raise DiscoveryValidationError(
            "FOR UPDATE locks rows and is not allowed in discovery",
            reason="for-update",
        )

    if ";" in analysis:
        raise DiscoveryValidationError(
            "multiple statements are not allowed; submit one SELECT at a time",
            reason="multiple-statements",
        )

    return _strip_trailing_semicolon(sql.strip())


def _main_keyword_after_with(analysis: str) -> str | None:
    """Return the statement keyword that follows a ``WITH`` CTE list, upper-cased.

    Oracle's ``WITH`` clause is a comma-separated list of ``name [(cols)] AS
    (body)`` definitions; the statement keyword (normally ``SELECT``) comes after
    the final body. This walks the scrubbed SQL at parenthesis depth 0, skipping
    each CTE's optional column list and parenthesised body, and returns the first
    depth-0 word that appears once a CTE body has closed and no comma introduces a
    further definition. Returns ``None`` when the structure can't be resolved (the
    caller then falls back to its other checks).
    """
    depth = 0
    expect_body = False  # seen this CTE's AS, so the next depth-0 "(" is its body
    closed_body = False  # a CTE body has closed and no comma has followed yet
    i = 0
    n = len(analysis)
    while i < n:
        ch = analysis[i]
        if ch == "(":
            depth += 1
            i += 1
            continue
        if ch == ")":
            depth -= 1
            i += 1
            if depth == 0 and expect_body:
                closed_body = True
                expect_body = False
            continue
        if depth == 0:
            if ch == ",":
                closed_body = False
                i += 1
                continue
            word = _WORD_RE.match(analysis, i)
            if word is not None:
                upper = word.group(0).upper()
                if closed_body:
                    return upper
                if upper == "AS":
                    expect_body = True
                i = word.end()
                continue
        i += 1
    return None


def _strip_trailing_semicolon(text: str) -> str:
    if text.endswith(";"):
        return text[:-1].rstrip()
    return text


def _scrub(sql: str) -> str:
    """Return ``sql`` with comments removed and string-literal bodies blanked.

    Double-quoted identifiers are replaced with a neutral placeholder so a quoted
    identifier like ``"DROP"`` cannot be mistaken for a keyword. Statement
    structure (parentheses, semicolons, bare keywords) is preserved.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        pair = sql[i : i + 2]
        if pair == "--":
            newline = sql.find("\n", i)
            i = n if newline == -1 else newline
            out.append(" ")
            continue
        if pair == "/*":
            close = sql.find("*/", i + 2)
            i = n if close == -1 else close + 2
            out.append(" ")
            continue
        if ch in "qQ" and i + 1 < n and sql[i + 1] == "'":
            i = _skip_q_quote(sql, i, out)
            continue
        if ch == "'":
            i = _skip_quote(sql, i, out)
            continue
        if ch == '"':
            close = sql.find('"', i + 1)
            i = n if close == -1 else close + 1
            out.append(' "id" ')
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _skip_quote(sql: str, start: int, out: list[str]) -> int:
    """Skip a normal ``'...'`` literal (with ``''`` escaping); blank its body."""
    n = len(sql)
    j = start + 1
    while j < n:
        if sql[j] == "'":
            if j + 1 < n and sql[j + 1] == "'":
                j += 2
                continue
            j += 1
            break
        j += 1
    out.append("''")
    return j


def _skip_q_quote(sql: str, start: int, out: list[str]) -> int:
    """Skip an Oracle ``q'<delim>...<delim>'`` literal; blank its body."""
    n = len(sql)
    opener = sql[start + 2] if start + 2 < n else ""
    closer = _Q_CLOSERS.get(opener, opener)
    needle = closer + "'"
    close = sql.find(needle, start + 3) if needle else -1
    out.append(" '' ")
    return n if close == -1 else close + len(needle)
