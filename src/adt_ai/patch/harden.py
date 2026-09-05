"""Make a hand-written patch script survive its second deploy, ADT #309 (was #17).

A patch goes to DEV, then UAT, then PROD, and the same folder is often re-run
against a database that already has half of it. A bare `ALTER TABLE ... ADD note`
fails with ORA-01430 the second time it lands, which stops the whole install
script, so old ADT rewrote every statement it copied out of `patch_scripts_dir`
into an existence-checked PL/SQL block (`fix_patch_script`, patch.py:2211-2312).
That is what makes `-continue` and a re-deploy mean anything.

Three transforms, all old ADT's:

* a `--` comment line becomes `PROMPT "-- ...";`, so the author's narration
  reaches the deploy log SQLcl spools instead of being invisible in it;
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

# The first line of a hardened script, and the sentinel that keeps the transform
# idempotent. It has to be BOTH: ADT #309 leaves the hardened copy as the only
# surviving version of the script, so a re-create that recovers it would
# otherwise convert this very header into `PROMPT`s and wrap the generated
# PL/SQL in more PL/SQL.
_SOURCE_HEADER = "-- SOURCE FILE: "

_STATEMENT_STARTS = ("CREATE", "DROP", "ALTER")

# `<verb> <OBJECT TYPE> [schema.]name`, quoted or not. Old ADT patch.py:2319-2322.
_QUALIFIED_RE = r'(CREATE|DROP|ALTER)\s+({})\s+"?[A-Z0-9_$-]+"?\."?([A-Z0-9_$-]+)"?'
_PLAIN_RE = r'(CREATE|DROP|ALTER)\s+({})\s+"?([A-Z0-9_$-]+)"?'

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
    """
    if text.lstrip().startswith(_SOURCE_HEADER):
        return text
    lines = text.splitlines(keepends=True)
    object_types = _object_types(config)
    rows: list[dict[str, object]] = []
    replacements: dict[int, tuple[int, str]] = {}
    buffer: list[str] = []
    start = 0
    for index, line in enumerate(lines):
        if line.lstrip().startswith("--"):
            lines[index] = 'PROMPT "{}";\n'.format(line.strip().replace('"', ""))
            continue
        if not buffer:
            first = line.strip().split(" ", 1)[0].upper()
            if first not in _STATEMENT_STARTS:
                continue
            start = index
        buffer.append(line)
        if ";" not in line:
            continue
        statement = _strip_trailing_comment(buffer)
        parsed = _parse_statement(statement, object_types)
        rows.append(_overview_row(start, statement, parsed))
        block = _wrap(statement, parsed)
        if block:
            replacements[start] = (index, block)
        buffer = []
    for begin in sorted(replacements, reverse=True):
        end, block = replacements[begin]
        lines[begin:end + 1] = [block]
    return _header(source, rows) + "".join(lines)


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


def _parse_statement(statement: str, object_types: list[str]) -> tuple[str, str, str, str, str]:
    """`(statement_type, object_type, object_name, operation, cc_name)`.

    Old ADT's `get_object_from_statement` (patch.py:2316-2358). ``cc_name`` is the
    column or constraint an `ALTER TABLE` names, or `?` when it names several --
    the multi-column form has no single thing to check, so it earns no wrapper.
    """
    normalized = re.sub(r"\s+", " ", statement, flags=re.M).strip().upper()
    normalized = normalized.replace(" UNIQUE ", " ").rstrip(";").strip()
    for object_type in object_types:
        for pattern in (_QUALIFIED_RE, _PLAIN_RE):
            match = re.search(pattern.format(re.escape(object_type)), normalized)
            if not match:
                continue
            statement_type, found_type, object_name = match.groups()
            operation, cc_name = "", ""
            if statement_type == "ALTER" and found_type == "TABLE":
                # Read the clause from where the NAME matched, never by
                # re-searching the line for its text: both patterns end on the
                # object-name group, so `match.end()` is exactly the clause
                # (ADT #558).
                operation, cc_name = _alter_table_operation(normalized[match.end() :])
            return statement_type, found_type, object_name, operation, cc_name
    return "", "", "", "", ""


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
    """
    words = clause.strip().split()
    if len(words) < 2:
        return "", ""
    verb, target = words[0], words[1]
    if target in ("CONSTRAINT", "PARTITION"):
        return f"{verb} {target}", _first_name(" ".join(words[2:]))
    rest = words[2:] if target == "COLUMN" else words[1:]
    return f"{verb} COLUMN", _first_name(" ".join(rest))


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


def _template_name(parsed: tuple[str, str, str, str, str]) -> str:
    statement_type, object_type, _name, operation, cc_name = parsed
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


def _literal(value: str) -> str:
    """A value going into a single-quoted PL/SQL literal, apostrophes doubled.

    Every `{}` slot in `HARDENING_TEMPLATES` except `{header}` lands inside one
    (`:= '{object_name}';`), and only `{statement}` was escaped. `_first_name`
    hands back a quoted identifier with its quotes stripped, so `"IT'S"` reached
    `:= 'IT'S';` and the generated block would not compile (ADT #554). Escaping
    at the one renderer rather than at each producer, so a name read some other
    way inherits it (the SOP's one-owner rule).
    """
    return value.replace("'", "''")


def _wrap(statement: str, parsed: tuple[str, str, str, str, str]) -> str:
    name = _template_name(parsed)
    if not name:
        return ""
    statement_type, object_type, object_name, operation, cc_name = parsed
    # `{header}` sits inside a double-quoted `PROMPT`, which is why it drops `"`
    # rather than doubling `'`, the same treatment a comment line gets above.
    header = " | ".join(
        part for part in (statement_type, object_type, object_name, operation, cc_name) if part
    ).replace('"', "")
    return HARDENING_TEMPLATES[name].format(
        header      = header,
        statement   = _literal(statement.strip().strip(";").strip()),
        object_type = _literal(object_type),
        object_name = _literal(object_name),
        cc_name     = _literal(cc_name),
    )


def _overview_row(
    start: int,
    statement: str,
    parsed: tuple[str, str, str, str, str],
) -> dict[str, object]:
    statement_type, object_type, object_name, _operation, cc_name = parsed
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
