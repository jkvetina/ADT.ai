"""Two versions of one exported table, read as columns and written as ALTERs.

Split out of `helpers.py` by ADT #494, which is the second time this family
has moved for the same reason and the same seam: `#287` cut it out of
`create.py` at the 20 000 byte context guard, and the question it answers has
always been its own. `helpers.py` asks what SQL a patch needs generated for
it; this file asks what CHANGED between two `CREATE TABLE` statements.

That question is a SQL parse, which is what makes it worth keeping apart:
every scanner here reads through `sql_spans()` because a comment is not SQL,
and it is easier to hold that rule over one small module than over a large
one where the parsing sits under three writers that do not parse anything.
"""

from __future__ import annotations

import re
from pathlib import Path

from adt_ai.export_db.normalizers import sql_spans
from adt_ai.patch import queries
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_files import git_show
from adt_ai.shared.sql_identifiers import safe_identifier


def _table_versions(root: Path, file: str, records: list[CommitRecord]) -> list[tuple[int, str]]:
    versions: list[tuple[int, str]] = []
    for record in records:
        if file not in record.usable_files:
            continue
        content = git_show(root, record.commit_hash, file)
        if content is not None:
            versions.append((record.number, content.decode("utf-8")))
    return versions

def _table_baseline(root: Path, file: str, records: list[CommitRecord]) -> str | None:
    """The version of ``file`` standing before this patch touched it.

    Read from the PARENT of the first selected commit carrying the file, which
    is the state the target database sits at when the patch runs. `None` when
    nothing is there: the window's own first commit created the file, or that
    commit is the repository's first and has no parent.

    Resolved through the commit hash rather than a commit number. The numbers
    come from the commit store and count only what the store holds, so
    `number - 1` names a different commit wherever the store skips one, and
    names nothing at all for the oldest commit it has cached.
    """
    first = next((record for record in records if file in record.usable_files), None)
    if first is None:
        return None
    content = git_show(root, f"{first.commit_hash}^", file)
    return None if content is None else content.decode("utf-8")

def _table_alter_sql(table_name: str, previous: str, current: str) -> str:
    safe_identifier(table_name, role="table name")
    previous_columns = _parse_table_columns(previous)
    current_columns = _parse_table_columns(current)
    statements: list[str] = []
    for column, definition in current_columns.items():
        if column not in previous_columns:
            statements.append(
                queries.ALTER_TABLE_ADD_STATEMENT.format(
                    table_name = table_name,
                    definition = definition,
                )
            )
        elif previous_columns[column] != definition:
            statements.append(
                queries.ALTER_TABLE_MODIFY_STATEMENT.format(
                    table_name = table_name,
                    definition = definition,
                )
            )
    for column in previous_columns:
        if column not in current_columns:
            statements.append(
                queries.ALTER_TABLE_DROP_COLUMN_STATEMENT.format(
                    table_name = table_name,
                    column     = _drop_column_name(previous_columns[column]),
                )
            )
    return "\n".join([*statements, ""]) if statements else ""

_CONSTRAINT_KEYWORDS = frozenset({"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"})


def _column_identity(token: str) -> str | None:
    """The `user_tab_columns.column_name` a declared column resolves to, or None.

    Oracle folds an unquoted identifier to upper case and keeps a quoted one
    exactly as written, so `"Note"` and `note` are two columns. Upper-casing both
    keyed them onto one `NOTE`, the later declaration overwrote the earlier, and
    a table carrying both left the diff a column short with nothing reporting it
    (ADT #554).

    None is a table-level constraint clause. A quoted token can never be one:
    `"CHECK"` in this position is a column called CHECK, and only the unquoted
    word is the keyword.
    """
    if len(token) > 1 and token.startswith('"') and token.endswith('"'):
        return token[1:-1]
    name = token.upper()
    return None if name in _CONSTRAINT_KEYWORDS else name


def _drop_column_name(definition: str) -> str:
    """The name a `DROP COLUMN` names, spelled the way the DDL declared it.

    A quoted identifier goes back out quoted and with its case intact. Rendering
    it the way an unquoted name is rendered, lower-cased and bare, drops `NOTE`
    where the DDL declared `"Note"`, which is a different column or none at all.
    """
    token = definition.split(None, 1)[0].strip(",")
    if len(token) > 1 and token.startswith('"') and token.endswith('"'):
        return f'"{safe_identifier(token[1:-1], role="column name")}"'
    return safe_identifier(token, role="column name").lower()


def _parse_table_columns(sql: str) -> dict[str, str]:
    """Every column a `CREATE TABLE` declares, keyed by its Oracle identity.

    **Comments are removed before a single character of a segment is read**
    (ADT #494). The old reader skipped a segment that STARTED with `--`, which
    is a different question and answers it wrong twice over: a comment sharing a
    segment with a real column took that column out of the parse, so `-create`
    reported it dropped and wrote `ALTER TABLE ... DROP COLUMN` for a column
    still standing in the DDL; and a trailing comment on a kept column rode into
    the generated definition, where `ADD name varchar2(30) -- note;` commented
    out its own terminator.

    Dropping the comment is also what makes a comment-only edit produce no
    script at all: the definitions on both sides compare equal, so no MODIFY.
    """
    body = _column_list_body(sql)
    if body is None:
        return {}
    columns: dict[str, str] = {}
    for raw in _split_sql_columns(body):
        line = _sql_without_comments(raw).strip().strip(",")
        if not line:
            continue
        name = _column_identity(line.split(None, 1)[0])
        if name is None:
            continue
        # Formatting whitespace is insignificant; literal/identifier bytes are
        # not. Flattening a default can change its value or hide a real MODIFY.
        columns[name] = "".join(
            re.sub(r"\s+", " ", line[start:end]) if kind == "code" else line[start:end]
            for kind, start, end in sql_spans(line, identifiers=True)
        ).strip()
    return columns

def _column_list_body(sql: str) -> str | None:
    """The text inside `CREATE TABLE x (...)`, ending at ITS closing paren.

    A greedy `\\((?P<body>.*)\\)` under `re.S` ran to the LAST `)` in the file
    and swallowed every trailing physical clause with it, `TABLESPACE`,
    `STORAGE (...)`, `PARTITION BY (...)`, all of which the export normalizer is
    documented to preserve (ADT #558). The swallowed text then parsed as part of
    the final column, so a table whose partitioning moved reported a column
    change nobody made, and a table whose trailing clause matched hid the bug
    entirely by comparing equal.

    Matched by depth through `sql_spans()`, so a paren inside a comment or a
    string literal is not structure, the same rule `_split_sql_columns` below
    already follows and `#299` established for the export scanners.
    """
    opening = re.search(r"\bcreate\s+table\b[^(]*\(", sql, flags=re.I | re.S)
    if not opening:
        return None
    start = opening.end()
    depth = 1
    for kind, span_start, span_end in sql_spans(sql):
        if kind != "code":
            continue
        for index in range(max(span_start, start), max(span_end, start)):
            char = sql[index]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return sql[start:index]
    # An unbalanced statement keeps the old reading rather than dropping the
    # table: a truncated export is still worth diffing on what it does carry.
    return sql[start:]


def _sql_without_comments(text: str) -> str:
    """`text` with its `--` and `/* */` spans removed, strings untouched."""
    return "".join(
        text[start:end] for kind, start, end in sql_spans(text) if kind != "comment"
    )

def _split_sql_columns(body: str) -> list[str]:
    """A table body cut at its top-level commas, comments and strings intact.

    Reads through `sql_spans()` because a comment is not SQL: the comma in
    `-- kept around, DELIBERATELY. removing it breaks the job` is not a column
    separator, and the apostrophe in `-- don't reorder` is not a string
    delimiter. This walked the characters itself with an `in_quote` flag and got
    both wrong, which is the bug `#299` fixed for the export scanners and the
    project SOP has banned by name ever since: *"A new scanner reuses it or it
    repeats that bug."* Six hand-rolled walks were in the tree when `#494`
    measured it and `#474` row D counted five, because that audit greps for
    `in_string` and this one spelled its flag `in_quote`.

    The slices stay RAW, comments and all. What each segment means is the
    caller's question, and `_parse_table_columns` answers it by stripping the
    comments from the segment rather than by getting a pre-cleaned string it
    could not have checked.
    """
    parts: list[str] = []
    start = 0
    depth = 0
    for kind, span_start, span_end in sql_spans(body):
        if kind != "code":
            continue
        for index in range(span_start, span_end):
            char = body[index]
            if char == "(":
                depth += 1
            elif char == ")" and depth > 0:
                depth -= 1
            elif char == "," and depth == 0:
                parts.append(body[start:index])
                start = index + 1
    parts.append(body[start:])
    return parts
