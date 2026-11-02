"""Make a hand-written patch script survive its second deploy, ADT #309 (was #17).

A patch goes to DEV, then UAT, then PROD, and the same folder is often re-run
against a database that already has half of it. A bare `ALTER TABLE ... ADD note`
fails with ORA-01430 the second time it lands, which stops the whole install
script, so old ADT rewrote every statement it copied out of `patch_scripts_dir`
into an existence-checked PL/SQL block (`fix_patch_script`, patch.py:2211-2312).
That is what makes `-continue` and a re-deploy mean anything.

Three transforms, all old ADT's:

* a `--` comment line between statements becomes `PROMPT "-- ...";`, so the
  author's narration reaches the deploy log SQLcl spools instead of being
  invisible in it (one inside a statement, a block or a literal stays, #923);
* a `CREATE` / `ALTER` / `DROP` is wrapped in the matching guard from
  `queries.HARDENING_TEMPLATES`;
* a comment header names the source file and tabulates what was found, which is
  the only provenance left once ADT #309 moves the script into the patch folder
  and deletes the original.

Anything else (`INSERT`, `MERGE`, a PL/SQL block) passes through untouched.
Making DML idempotent is the author's call and guessing at it would change what
the script does.
"""

from __future__ import annotations

import re
from typing import Any

from adt_ai.export_db.normalizers import sql_spans
from adt_ai.export_db.render import _compute_adt_layout
from adt_ai.patch.queries import HARDENING_TEMPLATES
from adt_ai.patch.queries.hardening import SCHEMA_GUARD
from adt_ai.patch.sql_literal import escape_literal

# The first line of a hardened script, and the sentinel that keeps the transform
# idempotent. It has to be BOTH: ADT #309 leaves the hardened copy as the only
# surviving version of the script, so a re-create that recovers it would
# otherwise convert this very header into `PROMPT`s and wrap the generated
# PL/SQL in more PL/SQL.
_SOURCE_HEADER = "-- SOURCE FILE: "

_STATEMENT_STARTS = ("CREATE", "DROP", "ALTER")

# SQL that ends on a `;` or a `/` line however many lines it spans (ADT #923), so
# a `--` line before that end is inside it. Any other first word, `PROMPT`, `SET`
# or a SQLcl verb, is one line long and keeps the treatment it always had.
_SQL_STARTS = frozenset({
    "INSERT", "UPDATE", "DELETE", "MERGE", "SELECT", "WITH", "GRANT", "REVOKE",
    "COMMENT", "TRUNCATE", "RENAME", "LOCK", "CALL", "COMMIT", "ROLLBACK", "SAVEPOINT",
})

# PL/SQL, which no `;` ends and its own `/` line always does: an anonymous block,
# or a `CREATE` of a stored unit.
_BLOCK_STARTS = frozenset({"DECLARE", "BEGIN"})
_UNIT_RE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:NON)?EDITIONABLE\s+)?"
    r"(?:FUNCTION|PROCEDURE|PACKAGE|TRIGGER|TYPE|LIBRARY|JAVA)\b",
    re.IGNORECASE,
)

# `<verb> [UNIQUE] <OBJECT TYPE> [IF [NOT] EXISTS] [schema.]name`, quoted or not.
# Old ADT patch.py:2319-2322. Old ADT predates the 23ai clause, and without it the
# guard was written for an object called `IF`: none exists, so `DROP INDEX IF
# EXISTS x` never ran its DROP and the deploy still reported success (ADT #1084).
# `UNIQUE` is read here, in the one head that takes it, rather than stripped from
# the whole statement, which turned `ADD UNIQUE (code)` into `ADD (code)` (#1094).
# `#` is legal in an unquoted name, and `t_orders#ix` was cut at it: the same
# skipped DROP one character over (#1094). The patterns run case-insensitively
# on the case-preserved text, and the name's opening quote is a group of its
# own, so a quoted name keeps the case Oracle holds it in.
_IF_EXISTS = r"(?:IF\s+(?:NOT\s+)?EXISTS\s+)?"
_HEAD_RE = r"(CREATE|DROP|ALTER)\s+(?:UNIQUE\s+)?({})\s+" + _IF_EXISTS
_NAME = r"[A-Z0-9_$#-]+"
_OBJECT_RE = (
    _HEAD_RE
    + rf'(?:(?P<owner_quote>"?)(?P<owner>{_NAME})"?\.)?'
    + rf'(?P<quote>"?)(?P<name>{_NAME})"?'
)
_ParsedStatement = tuple[str, str, str, str, str, str]

#: The `ALTER TABLE` targets that name no single column or constraint to check:
#: a constraint given by kind rather than by name, and a rename of the table
#: itself. The statement ships verbatim, as a multi-column `ADD (a, b)` does.
#: `ADD UNIQUE (code)` read as `ADD COLUMN code`, whose column exists, so the
#: constraint was never created and the deploy was green (ADT #1094).
_UNGUARDED_TARGETS = frozenset({"UNIQUE", "PRIMARY", "FOREIGN", "CHECK", "TO"})

_OVERVIEW_COLUMNS = (
    "line",
    "template",
    "statement",
    "object_type",
    "object_name",
    "col./constr.",
)


def harden_patch_script(text: str, config: dict[str, Any], *, source: str) -> str:
    """The hardened form of one per-patch script, header included.

    ``source`` is the repo-relative path the script came from; it survives only
    in the header once the file has been moved into the patch folder.

    **Only a comment BETWEEN statements becomes a `PROMPT`** (ADT #923). Every
    `--` line was converted, and a `PROMPT` is a SQLcl command: inside `BEGIN` it
    is PLS-00103, inside a multi-line `ALTER ... ADD (` it is left in the
    statement, and inside `q'[...]'` it changes the text an INSERT stores. So the
    reader tracks what it is inside: a statement until its code `;` or `/` line,
    PL/SQL until its `/` line, and a literal until it closes. A statement ending
    at the first line holding any `;` cut `DEFAULT 'a;` off the rest of itself.
    """
    if text.lstrip().startswith(_SOURCE_HEADER):
        return text
    lines = text.splitlines(keepends=True)
    object_types = _object_types(config)
    rows: list[dict[str, object]] = []
    replacements: dict[int, tuple[int, str]] = {}
    buffer: list[str] = []
    start = 0
    # What the reader is inside: a SQL statement (`ddl` when it is one this
    # module wraps), PL/SQL, and the literal an earlier line left open.
    statement = ddl = block = False
    carried = comment = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not (statement or block):
            # A `/* ... */` opened between statements is left as written until
            # the line that closes it, read through `_lex` like a literal a
            # statement leaves open: a line of prose inside one beginning
            # `Alter` opened a statement that swallowed the `*/` and the next
            # real ALTER with it (ADT #1094).
            if comment:
                _ends, comment = _lex(comment, line)
                continue
            if stripped.startswith("/*"):
                _ends, comment = _lex("", line)
                if comment:
                    continue
            if stripped.startswith("--"):
                lines[index] = 'PROMPT "{}";\n'.format(stripped.replace('"', ""))
                continue
            word = stripped.split(None, 1)[0].upper() if stripped else ""
            if word in _BLOCK_STARTS:
                block = True
            elif word in _STATEMENT_STARTS:
                statement = ddl = True
                start = index
            elif word in _SQL_STARTS:
                statement = True
            else:
                # One line long: `PROMPT`, `SET`, a SQLcl verb, a stray `/`.
                continue
            carried = ""
        continued = bool(carried)
        ends, carried = _lex(carried, line)
        slash = not continued and stripped == "/"
        if ddl and not slash and (continued or not stripped.startswith("--")):
            buffer.append(line)
            # The unit's own word can sit on the second line of `CREATE OR
            # REPLACE`, so the first few lines are asked, never the whole body.
            if not block and len(buffer) <= 3:
                block = bool(_UNIT_RE.match("".join(buffer).lstrip()))
        # A stored unit ends on its `/` line alone: its first `;` cut `CREATE
        # PROCEDURE p AS BEGIN NULL;` into the guard and left `END;` and `/`
        # loose behind it (ADT #1094).
        if statement and (slash or (ends and not block)):
            if ddl:
                statement_text = _strip_trailing_comment(buffer)
                parsed = _parse_statement(statement_text, object_types)
                rows.append(_overview_row(start, statement_text, parsed))
                wrapped = _wrap(statement_text, parsed, unit=block)
                if wrapped:
                    replacements[start] = (index - 1 if slash else index, wrapped)
                buffer = []
            statement = ddl = False
        if slash:
            block = False
            carried = ""
    for begin in sorted(replacements, reverse=True):
        end, wrapped = replacements[begin]
        lines[begin:end + 1] = [wrapped]
    return _header(source, rows) + "".join(lines)


def _lex(carried: str, line: str) -> tuple[bool, str]:
    """Whether ``line`` holds a statement-ending `;`, and the literal it leaves open.

    ``carried`` is a literal or block comment an earlier line of the same
    statement opened and did not close. Read in front of ``line`` through
    `sql_spans()`, like every scanner in this family, so a `;` or a `--` inside
    `'a;b'` or `q'[...]'` is data rather than structure. Only statement lines are
    read this way: a `PROMPT don't` line is text, and its apostrophe must not open
    a literal over the rest of the script.
    """
    text = carried + line
    spans = sql_spans(text, identifiers=True)
    ends = any(kind == "code" and ";" in text[begin:end] for kind, begin, end in spans)
    kind, begin, end = spans[-1] if spans else ("code", 0, 0)
    # A `--` comment never reaches the next line, whatever ends this one.
    opened = kind != "code" and end == len(text) and not text.startswith("--", begin)
    return ends, text[begin:] if opened else ""


def _object_types(config: dict[str, Any]) -> list[str]:
    # Longest first, so `PACKAGE BODY` is matched before `PACKAGE` and
    # `MATERIALIZED VIEW` before `VIEW`. Old ADT got the same effect from
    # `sorted(..., reverse=True)`, which is only accidentally right, `TYPE BODY`
    # sorts above `TYPE` but `MVIEW LOG` does not sort above `MATERIALIZED VIEW`.
    types = [str(name).upper() for name in (config.get("object_types") or {})]
    return sorted(types, key=lambda name: (-len(name), name))


def _strip_trailing_comment(buffer: list[str]) -> str:
    """Drop a `-- ...` note sitting after the closing `;` (old ADT patch.py:2248).

    Both marks have to be CODE for this to be a trailing note, so the buffer is
    read through `sql_spans()` like every other scanner in this family. A raw
    `;\\s*--` search found the same two characters inside a literal, and
    `DEFAULT 'a;--b'` came out truncated at the semicolon with an unbalanced
    quote behind it, which the deploy meets rather than the guard (ADT #554).

    The whole buffer is scanned rather than its last line: a literal opened on an
    earlier line decides whether the last line's `;--` is code at all.
    """
    text = "".join(buffer)
    for kind, start, _end in sql_spans(text, identifiers=True):
        if kind != "comment":
            continue
        head = text[:start].rstrip()
        if head.endswith(";"):
            return head + "\n"
    return text


def _parse_statement(statement: str, object_types: list[str]) -> _ParsedStatement:
    """`(statement_type, object_type, object_name, operation, cc_name, owner)`.

    Old ADT's `get_object_from_statement` (patch.py:2316-2358). ``cc_name`` is the
    column or constraint an `ALTER TABLE` names, or `?` when it names several:
    the multi-column form has no single thing to check, so it earns no wrapper.

    Comments are blanked before anything is read: `DROP TABLE -- note` over a
    second line read the `--` itself as the name (ADT #1094). The text keeps its
    case so a quoted name comes back as written, `"MyTable"` rather than
    `MYTABLE`, which is the name `user_objects` holds; an unquoted one is
    upper-cased, as Oracle does.
    """
    collapsed = re.sub(r"\s+", " ", _without_comments(statement), flags=re.M).strip()
    collapsed = collapsed.rstrip(";").strip()
    for object_type in object_types:
        match = re.search(_OBJECT_RE.format(re.escape(object_type)), collapsed, re.IGNORECASE)
        if not match:
            continue
        statement_type, found_type = match.group(1).upper(), match.group(2).upper()
        object_name = match.group("name")
        if not match.group("quote"):
            object_name = object_name.upper()
        owner = match.group("owner") or ""
        if not match.group("owner_quote"):
            owner = owner.upper()
        operation, cc_name = "", ""
        if statement_type == "ALTER" and found_type == "TABLE":
            # Start after the matched name, never at a substring inside ALTER.
            operation, cc_name = _alter_table_operation(collapsed[match.end() :].upper())
        return statement_type, found_type, object_name, operation, cc_name, owner
    return "", "", "", "", "", ""


def _without_comments(statement: str) -> str:
    """``statement`` with every comment span blanked, through `sql_spans()` (ADT #1094)."""
    return "".join(
        " " if kind == "comment" else statement[begin:end]
        for kind, begin, end in sql_spans(statement, identifiers=True)
    )


def _alter_table_operation(clause: str) -> tuple[str, str]:
    """`("ADD COLUMN", "NOTE")` for the `ADD note VARCHAR2(30)` after the name.

    The keyword is optional in Oracle's own grammar, `ADD note` and
    `ADD COLUMN note` are the same statement, so both spellings have to reach
    the same answer. Old ADT unconditionally spliced `COLUMN` in at position 1
    (patch.py:2342, 2349) and so read the literal word `COLUMN` as the column
    name whenever the author had written it out.

    Takes the clause rather than the whole line and the name, because deriving
    it here by `split(object_name, 1)` cut at the first occurrence of that text
    anywhere: a table called `T` split inside the word `ALTER` and the statement
    came out unparsed, so it shipped with no existence guard at all (ADT #558).

    An `IF [NOT] EXISTS` after the verb and its target is stepped over the way
    the head clause is (`#1084`): `DROP CONSTRAINT IF EXISTS c1` guarded a
    constraint called `IF` until ADT #1094. A target in `_UNGUARDED_TARGETS`
    answers `?`, the no-wrap marker, so the statement ships as written.
    """
    words = clause.strip().split()
    if len(words) < 2:
        return "", ""
    verb, target = words[0], words[1]
    if target in _UNGUARDED_TARGETS:
        kind = words[:3] if target in ("PRIMARY", "FOREIGN") else words[:2]
        return " ".join(kind), "?"
    if target in ("CONSTRAINT", "PARTITION"):
        return f"{verb} {target}", _first_name(_after_if_exists(words[2:]))
    rest = words[2:] if target == "COLUMN" else words[1:]
    return f"{verb} COLUMN", _first_name(_after_if_exists(rest))


def _after_if_exists(words: list[str]) -> str:
    """The clause words after an optional `IF [NOT] EXISTS`, joined (ADT #1094)."""
    if words[:2] == ["IF", "EXISTS"]:
        words = words[2:]
    elif words[:3] == ["IF", "NOT", "EXISTS"]:
        words = words[3:]
    return " ".join(words)


def _first_name(text: str) -> str:
    """The single column or constraint this clause names, or `?` for several.

    `?` is old ADT's own marker for "no single thing to check" (patch.py:2354),
    and `_template_name` refuses to wrap a statement carrying it: a guard written
    against one column of a multi-column `ADD` would skip the whole statement the
    moment that one column existed.

    The name comes back RAW, quotes stripped and nothing else: `"IT'S"` is a
    legal column and the apostrophe is part of it. Making it safe for the PL/SQL
    literal it ends up in belongs to `_wrap`, which is the only thing that knows
    which quoting the value is about to meet.
    """
    text = text.strip()
    if not text:
        return ""
    if text.startswith("("):
        inner = _balanced_group(text)
        if inner is None or _has_top_level_comma(inner):
            return "?"
        return _first_name(inner)
    name = text.split()[0].strip(",;")
    return "?" if "(" in name or ")" in name else name.strip('"')


def _balanced_group(text: str) -> str | None:
    """The contents of the leading `( ... )`, or ``None`` when it never closes."""
    depth = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[1:index]
    return None


def _has_top_level_comma(text: str) -> bool:
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            return True
    return False


def _template_name(parsed: _ParsedStatement) -> str:
    statement_type, object_type, _name, operation, cc_name, _owner = parsed
    if not statement_type or cc_name == "?":
        return ""
    for candidate in (
        " | ".join(filter(None, (statement_type, object_type, operation))),
        " | ".join(filter(None, (statement_type, operation))),
        operation,
        " | ".join(filter(None, (statement_type, object_type))),
        statement_type,
    ):
        if candidate and HARDENING_TEMPLATES.get(candidate):
            return candidate
    return ""


def _wrap(statement: str, parsed: _ParsedStatement, *, unit: bool = False) -> str:
    """The guard for one parsed statement, or "" when no template covers it.

    Every `{}` slot in `HARDENING_TEMPLATES` except `{header}` lands inside a
    single-quoted literal (`:= '{object_name}';`), and only `{statement}` was
    escaped. `_first_name` hands back a quoted identifier with its quotes
    stripped, so `"IT'S"` reached `:= 'IT'S';` and the generated block would not
    compile (ADT #554). Escaped here, at the one renderer rather than at each
    producer, so a name read some other way inherits it (the SOP's one-owner
    rule), and through `sql_literal.escape_literal`, the rule's one home (#923).

    A stored ``unit`` keeps its closing `;`: inside the literal it ends the
    PL/SQL body, and `CREATE PROCEDURE p AS BEGIN NULL; END` without it is
    PLS-00103 the moment the guard runs (ADT #1094). A SQL statement sheds it.
    """
    name = _template_name(parsed)
    if not name:
        return ""
    statement_type, object_type, object_name, operation, cc_name, owner = parsed
    # `{header}` sits inside a double-quoted `PROMPT`, which is why it drops `"`
    # rather than doubling `'`, the same treatment a comment line gets above.
    header = " | ".join(
        part for part in (statement_type, object_type, object_name, operation, cc_name) if part
    ).replace('"', "")
    body = statement.strip()
    if not unit:
        body = body.strip(";").strip()
    owner_expression = (
        f"'{escape_literal(owner)}'" if owner else "SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')"
    )
    return HARDENING_TEMPLATES[name].format(
        header      = header,
        statement   = escape_literal(body),
        object_type = escape_literal(object_type),
        object_name = escape_literal(object_name),
        cc_name     = escape_literal(cc_name),
        schema_guard = SCHEMA_GUARD.format(owner_expression=owner_expression),
    )


def _overview_row(
    start: int,
    statement: str,
    parsed: _ParsedStatement,
) -> dict[str, object]:
    statement_type, object_type, object_name, _operation, cc_name, _owner = parsed
    return {
        "line": start + 1,
        "template": _template_name(parsed),
        "statement": statement_type,
        "object_type": object_type,
        "object_name": object_name,
        "col./constr.": cc_name,
    }


def _header(source: str, rows: list[dict[str, object]]) -> str:
    """`-- SOURCE FILE:` plus the overview table, commented out.

    Old ADT captured its own console header and table and prefixed every line
    with `--` (patch.py:2298-2301). Same idea, rendered through the shared ADT
    table layout so the columns line up the way every other ADT table does.
    """
    lines = [f"{_SOURCE_HEADER}{source}", "--"]
    if rows:
        layout = _compute_adt_layout(rows, _OVERVIEW_COLUMNS, {}, ("line",))
        lines.append(f"--{layout.header_line()}")
        lines.append(f"--{layout.separator_line()}")
        lines.extend(
            f"--{layout.row_line([row.get(column, '') for column in _OVERVIEW_COLUMNS])}"
            for row in rows
        )
        lines.append("--")
    return "\n".join(lines) + "\n"
