"""SQL and source-rebuilding helpers for the recompile -trailing pass.

``export_db`` strips trailing whitespace on the way out, so an untouched 10k-line
package still diffs against the database's stored source on every export. This
module powers the fix from the other side: find the objects whose *stored* source
carries trailing whitespace and rebuild them without it, so the database matches
what export_db writes and the diff noise goes away for good.

One topic module of the ``queries`` package; the package ``__init__`` re-exports
everything here, so callers keep importing from ``adt_ai.recompile.queries``.
"""

from __future__ import annotations

from collections.abc import Sequence

from adt_ai.shared.object_types import PLSQL_OBJECT_TYPES
from adt_ai.shared.sql_identifiers import safe_identifier, safe_identifiers

# Object types -trailing rebuilds from user_source. Identical to
# PLSQL_OBJECT_TYPES, listed through it so the two can never drift. Views are not
# here because they have no user_source rows at all — they take the separate
# user_views.text path below (#122), not because they are out of scope.
_TRAILING_TYPE_IN_LIST = ", ".join(f"'{object_type}'" for object_type in PLSQL_OBJECT_TYPES)


# Objects whose stored source carries trailing whitespace, with the count of
# affected lines per object. Detection runs in SQL so a sweep only ever fetches and
# rewrites objects that actually need it — an untouched schema costs one query.
#
# Scoped by the standard :object_type / :object_name filters (-type/-name), not by a
# flag-local pattern: -trailing acts on the same compilable objects as the recompile
# loop, so it uses the same filters rather than inventing a second name filter.
#
# The comparison strips the stored line terminator (CHR(10)) first, then checks
# whether any space, tab, or stray CR survives at the end. A CR counts as trailing
# whitespace, not as part of the terminator: export_db strips it on the way out, so
# leaving it in the database would keep producing the very diff noise this fixes.
# Wrapped objects are excluded outright: user_source returns their obfuscated blob,
# not recoverable source, so rewriting one would destroy it.
TRAILING_OBJECTS_QUERY = f"""
WITH objects_add AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:objects_prefix, '%')), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
),
object_names AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:object_name, '%')), ',')) t
),
object_types AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:object_type, '%')), ',')) t
)
SELECT
    s.type          AS object_type,
    s.name          AS object_name,
    COUNT(*)        AS trailing_lines
FROM user_source s
JOIN objects_add a
    ON s.name           LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON s.name           LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND s.type          IN ({_TRAILING_TYPE_IN_LIST})
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE s.type LIKE n_t.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE s.name LIKE n_n.object_like ESCAPE '\\'
    )
    AND RTRIM(RTRIM(s.text, CHR(10)), ' ' || CHR(9) || CHR(13))
        != RTRIM(s.text, CHR(10))
    AND NOT EXISTS (
        SELECT 1
        FROM user_source w
        WHERE w.name        = s.name
            AND w.type      = s.type
            AND w.line      = 1
            AND REGEXP_LIKE(w.text, '\\swrapped(\\s|$)', 'i')
    )
GROUP BY s.type, s.name
ORDER BY s.type, s.name
""".strip()


# One object's stored source, line by line. Fetched per object immediately before
# that object is rewritten — never for the whole schema up front, because the
# database is live and somebody else's change must not be silently reverted.
OBJECT_SOURCE_QUERY = """
SELECT
    s.line          AS line,
    s.text          AS text
FROM user_source s
WHERE 1 = 1
    AND s.type      = :object_type
    AND s.name      = :object_name
ORDER BY s.line
""".strip()


# A trigger's ENABLED/DISABLED state, read before CREATE OR REPLACE so it can be
# restored after. CREATE OR REPLACE TRIGGER always leaves the trigger ENABLED.
TRIGGER_STATUS_QUERY = """
SELECT t.status AS status
FROM user_triggers t
WHERE t.trigger_name = :object_name
""".strip()


def build_trailing_source_ddl(lines: Sequence[str]) -> str | None:
    """Rebuild one object's DDL from ``user_source`` with trailing whitespace stripped.

    ``user_source`` stores each line with its terminator, so a line reads
    ``"  x := 1;   \\n"``. Strip each line the same way export_db does
    (``rstrip()``) and rejoin with ``\\n`` — that is exactly what makes the stored
    source match the exported file and kills the diff noise.

    Returns ``None`` when no line carries trailing whitespace. That is the
    load-bearing guarantee: the caller must then leave the object completely
    untouched — no CREATE OR REPLACE, no LAST_DDL_TIME churn, no dependent
    invalidation — even though the detection query offered it up.
    """
    cleaned = [line.rstrip() for line in lines]
    # The same lines with only their terminator (\n) removed. A trailing \r is *not*
    # part of the terminator here — export_db strips it too, so a CRLF-stored line
    # counts as needing the fix. If stripping changed nothing these are identical and
    # the object is clean.
    if cleaned == [line.rstrip("\n") for line in lines]:
        return None
    return "CREATE OR REPLACE " + "\n".join(cleaned)


# Views in scope, with their stored defining text (#122).
#
# Unlike the user_source path, detection CANNOT run in SQL here: user_views.text is
# a LONG, and RTRIM/comparison operators do not work on LONG. So this fetches the
# text for every in-scope view and the trailing check happens in Python
# (build_trailing_view_ddl). Views are few and their text is small, so the cost is
# nothing like fetching every package body up front.
#
# 'VIEW' is matched against the standard :object_type patterns the same way the
# other filters are, so `-type PACKAGE%` skips this query's rows entirely and
# `-type VIEW` runs only it.
#
# Three classes of view are excluded outright rather than rebuilt:
#   - editioning views: they belong to Edition-Based Redefinition and a plain
#     CREATE OR REPLACE VIEW is not how they are maintained.
#   - any view carrying a constraint: WITH READ ONLY records constraint type 'O'
#     and WITH CHECK OPTION type 'V', and neither clause survives a rebuild from
#     user_views.text — the property would be silently dropped. Testing for any
#     user_constraints row covers both without depending on the READ_ONLY column,
#     which only exists on newer releases.
TRAILING_VIEWS_QUERY = """
WITH objects_add AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:objects_prefix, '%')), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
),
object_names AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:object_name, '%')), ',')) t
),
object_types AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:object_type, '%')), ',')) t
)
SELECT
    v.view_name     AS object_name,
    v.text          AS view_text
FROM user_views v
JOIN objects_add a
    ON v.view_name      LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON v.view_name      LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE 'VIEW' LIKE n_t.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE v.view_name LIKE n_n.object_like ESCAPE '\\'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM user_editioning_views e
        WHERE e.view_name = v.view_name
    )
    AND NOT EXISTS (
        SELECT 1
        FROM user_constraints c
        WHERE c.table_name = v.view_name
    )
ORDER BY v.view_name
""".strip()


# One view's stored text, re-read immediately before that view is rewritten.
#
# TRAILING_VIEWS_QUERY's copy is for *detection* only. This is the authoritative
# read: the database is live, and a colleague's change to the view in the window
# between the detection sweep and this view's turn must not be silently reverted.
# Same invariant the user_source path gets from OBJECT_SOURCE_QUERY.
VIEW_TEXT_QUERY = """
SELECT v.text AS view_text
FROM user_views v
WHERE v.view_name = :object_name
""".strip()


# One view's column list, in declaration order. Re-attached to the rebuilt CREATE
# because user_views.text holds only the SELECT: a view created as
# `CREATE VIEW v (a, b) AS SELECT x+1, y FROM t` stores no aliases at all, so
# without the list the rebuild fails outright (ORA-00998) or, worse, renames the
# columns to whatever the SELECT happens to expose.
VIEW_COLUMNS_QUERY = """
SELECT c.column_name AS column_name
FROM user_tab_columns c
WHERE c.table_name = :object_name
ORDER BY c.column_id
""".strip()


def build_trailing_view_ddl(
    view_name: str,
    columns: Sequence[str],
    text: str,
) -> str | None:
    """Rebuild one view's DDL from ``user_views.text`` with trailing whitespace stripped.

    ``text`` is the stored defining query — just the ``SELECT``, without the
    ``CREATE OR REPLACE VIEW ... AS`` wrapper — so the wrapper and the column list
    have to be rebuilt around it. ``FORCE`` matches the #121/#122 contract: a view
    that is already invalid for an unrelated reason must come back exactly as
    invalid, not fail the sweep.

    Returns ``None`` when no line carries trailing whitespace, which is the same
    load-bearing guarantee the package path makes: the caller then leaves the view
    completely untouched, so an unchanged view never churns LAST_DDL_TIME or
    invalidates its dependents.

    Raises ``ValueError`` when the view or a column is not a plain unquoted
    identifier. Guessing the quoting could silently rename a column, so the runner
    reports it as a failed object instead.
    """
    lines = text.split("\n")
    cleaned = [line.rstrip() for line in lines]
    if cleaned == lines:
        return None
    safe_identifier(view_name, role="object name")
    safe_identifiers(columns, role="view column name")
    column_list = ", ".join(columns)
    body = "\n".join(cleaned)
    return f"CREATE OR REPLACE FORCE VIEW {view_name} ({column_list}) AS\n{body}"


def count_trailing_view_lines(text: str) -> int:
    """How many of a view's stored lines carry trailing whitespace."""
    return sum(1 for line in text.split("\n") if line != line.rstrip())


def build_disable_trigger_statement(object_name: str) -> str:
    """Build the ALTER TRIGGER ... DISABLE that restores a trigger's disabled state.

    CREATE OR REPLACE TRIGGER silently re-enables a disabled trigger, so a
    -trailing sweep has to put the state back or it quietly arms triggers somebody
    switched off on purpose.
    """
    safe_identifier(object_name, role="object name")
    return f"ALTER TRIGGER {object_name} DISABLE"
