"""Running the Oracle table diff, and rendering what it answers (ADT #753).

`table_diff.py` prepares the two versions; `queries/table_diff.py` holds the SQL;
this file is the lifecycle between them: drop, build, suppress, ask, drop, and
turn Oracle's answer into statements a patch script can carry.

The drop is the part with a rule of its own. Both shadow tables go **before**
they are built, because a leftover from a run that died with its connection is
not the version this run means to compare, and **again after**, in a `finally`,
because leaving them is what made old ADT need a `-deldiff` flag in the first
place. `shared/diff_tables.py` still sweeps whatever survives a lost VPN on the
next connecting run; that is the third net, not the first.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

from adt_ai.export_db.normalizers import sql_spans
from adt_ai.patch.queries.table_diff import (
    DROP_SHADOW_TABLE_STATEMENT,
    SET_TRANSFORM_PARAM_BLOCK,
    SUPPRESSED_TRANSFORMS,
    TABLE_DIFF_BLOCK,
)
from adt_ai.patch.table_diff import (
    SHADOW_MARKER,
    SOURCE_MARKER,
    TARGET_MARKER,
    shadow_ddl,
    shadow_table_names,
)
from adt_ai.patch.table_rebuild import rebuild_table
from adt_ai.shared.sql_identifiers import safe_identifier

#: Only the owner of the object being altered. A `REFERENCES "OTHER"."T"` keeps
#: its qualifier: the patch deploys into one schema, and a foreign key pointing
#: at another one is a real cross-schema reference, not noise. Old ADT stripped
#: every `"X".` on the line (`patch.py:2546`) and would have rewritten that
#: reference into a different table.
_OWNER_PREFIX_RE = re.compile(r'\b(ALTER\s+(?:TABLE|INDEX)\s+)"[^"]+"\.', flags=re.IGNORECASE)

#: A quoted name Oracle folded to upper case is a name nobody quoted, so it goes
#: back out the way every other exported file in this repo spells one. A quoted
#: mixed-case name is a DIFFERENT identifier and is left exactly as it is.
_SIMPLE_QUOTED_RE = re.compile(r'"([A-Z][A-Z0-9_$#]*)"')

#: Oracle answers a change it cannot express as a line of its own INSIDE the
#: ALTER CLOB, beside the statements. Measured on the story fixture: taking a
#: `DEFAULT 0` back off a column answers `ORA-39267: Cannot remove default from
#: table column.` and no `MODIFY`. It is a message, so it ships as a comment
#: rather than a statement: `harden` turns a comment into a `PROMPT`, which puts
#: Oracle's own words in the deploy log where the missing ALTER would have run.
_DIAGNOSTIC_RE = re.compile(r"^ORA-\d+", flags=re.IGNORECASE)


class TableDiffRefused(Exception):
    """One of the two versions would not build, so Oracle was never asked.

    Raised rather than answered with an empty string, because the two read
    identically at the call site and mean opposite things. Oracle answering
    nothing means the versions agree and no ALTER is needed; a version that will
    not build means nothing was compared at all, and the patch then ships its
    `CREATE TABLE IF NOT EXISTS` against a table that already exists, which
    deploys green and changes nothing. Old ADT printed `DIFF SOURCE TABLE FAIL`
    for exactly this (`patch.py:2514`).

    Measured on the story fixture, 2026-09-09: a version adding a foreign key to
    a key the target does not have yet builds no shadow, and the run reported no
    table change of any kind.
    """

    def __init__(self, table: str, reason: str) -> None:
        self.table = table
        self.reason = reason
        super().__init__(f"{table}: {reason}")


def table_alter_sql(
    gateway: Any,
    table_name: str,
    previous: str,
    current: str,
) -> str:
    """What Oracle says has to happen to get from ``previous`` to ``current``.

    ``previous`` and ``current`` are two exported `CREATE TABLE` files, the same
    two versions the commit walk has always selected. Empty string when nothing
    changed, and :class:`TableDiffRefused` when either version will not build: a
    version carrying an error cannot answer for itself, and comparing one real
    table against nothing would report every column of a live table as newly
    added.
    """
    safe_identifier(table_name, role="table name")
    source, target = shadow_table_names(table_name)
    try:
        _drop_shadows(gateway, source, target)
        _build_shadows(gateway, table_name, previous, current)
        for parameter in SUPPRESSED_TRANSFORMS:
            gateway.execute(SET_TRANSFORM_PARAM_BLOCK, {"parameter": parameter})
        answer = gateway.fetch_clob(
            TABLE_DIFF_BLOCK,
            {
                # `GET_SXML` looks the name up in the dictionary, where an
                # unquoted identifier is stored folded. The DDL above keeps the
                # file's own casing because that is what a person reads; this
                # bind is a lookup key and gets the spelling Oracle recorded.
                # Measured on SANDBOX: a lower-case bind answers `ORA-31603:
                # object "adt753_live$1" of type TABLE not found`.
                "source_table"  : source.upper(),
                "target_table"  : target.upper(),
                "source_marker" : SOURCE_MARKER,
                "target_marker" : TARGET_MARKER,
            },
        )
    finally:
        _drop_shadows(gateway, source, target)
    return render_alter_statements(answer)


def render_alter_statements(answer: str | None) -> str:
    """Oracle's ALTERXML output, as statements a deploy can run.

    Four rewrites, one per property of what comes back: the continuation indent
    goes, the owner qualifier goes, a quoted upper-case name is unquoted, and
    every statement earns the terminator Oracle does not write. Without the last
    one the whole answer is a single unparseable statement, which is the shape
    old ADT hand-patched line by line (`patch.py:2552`).

    A line Oracle wrote as a DIAGNOSTIC keeps neither rewrite nor terminator,
    see :data:`_DIAGNOSTIC_RE`: it is not SQL and giving it a `;` only offers it
    to a parser that has no business reading it.
    """
    rows = [line.strip() for line in (answer or "").splitlines() if line.strip()]
    return "".join(
        f"-- {row}\n" if _DIAGNOSTIC_RE.match(row) else f"{_rendered(row)};\n"
        for row in rows
    )


def _rendered(statement: str) -> str:
    """One statement, with its string literals untouched.

    Read through `sql_spans()` for the reason every scanner in this repo is: a
    `DEFAULT 'SAY "HI"'` carries double quotes that are not an identifier, and a
    CHECK condition is copied verbatim out of the source.
    """
    return "".join(
        _rendered_code(statement[start:end]) if kind == "code" else statement[start:end]
        for kind, start, end in sql_spans(statement)
    )


def _rendered_code(code: str) -> str:
    return _SIMPLE_QUOTED_RE.sub(
        lambda match: match.group(1).lower(), _OWNER_PREFIX_RE.sub(r"\1", code)
    )


def _drop_shadows(gateway: Any, *tables: str) -> None:
    """Drop each shadow table, saying nothing about one that is not there."""
    for table in tables:
        with contextlib.suppress(Exception):
            gateway.execute(DROP_SHADOW_TABLE_STATEMENT.format(table_name=table))


def _build_shadows(
    gateway: Any,
    table_name: str,
    previous: str,
    current: str,
) -> None:
    """Create both versions, or say which one the database would not accept.

    The caller's `finally` drops whatever this left standing, so the refusal
    carries only the reason: which side failed, and what Oracle said about it.

    The target's version gets one second attempt, rebuilt from the columns and
    constraints it declares (ADT #859, which widened the one comma `#855` put
    back into every comma, and past a partition clause, see `table_rebuild.py`).
    It is history: the target already stands at it, and the commit that fixed
    the file is usually the one being patched, so refusing it leaves nothing to
    correct and ships no ALTER for the change. This patch's version never gets
    one, because it deploys as written and a `CREATE` Oracle refuses here is a
    `CREATE` the deploy refuses. When the rebuild does not help either, the
    refusal still quotes Oracle about the file as committed rather than about a
    text nobody wrote.
    """
    sides = (
        (previous, SOURCE_MARKER, "the target's version", True),
        (current, TARGET_MARKER, "this patch's version", False),
    )
    for version, marker, side, repairable in sides:
        try:
            gateway.execute(shadow_ddl(version).replace(SHADOW_MARKER, marker))
        except Exception as error:
            rebuilt = rebuild_table(version) if repairable else None
            if rebuilt is None or not _built(gateway, rebuilt, marker):
                raise TableDiffRefused(table_name, f"{side} would not build: {error}") from error


def _built(gateway: Any, version: str, marker: str) -> bool:
    """Whether ``version`` builds as the ``marker`` shadow table."""
    try:
        gateway.execute(shadow_ddl(version).replace(SHADOW_MARKER, marker))
    except Exception:
        return False
    return True


__all__ = ["TableDiffRefused", "render_alter_statements", "table_alter_sql"]
