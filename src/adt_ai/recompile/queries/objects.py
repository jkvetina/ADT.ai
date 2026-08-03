"""Overview, selection, and error read SQL for the recompile module.

The module's core SQL topic: the overview / objects_to_recompile /
objects_errors_summary reads, ported faithfully from old ADT ``lib/queries.py``.
The ALTER ... COMPILE / DBMS_MVIEW.REFRESH statement builders and the ``-force``
drift-selection binds live in the sibling module
:mod:`adt_ai.recompile.queries.statements`; the report and trailing-whitespace
topics are :mod:`adt_ai.recompile.queries.reports` and
:mod:`adt_ai.recompile.queries.trailing`. The package ``__init__`` re-exports
every topic, so callers keep importing everything from ``adt_ai.recompile.queries``.
"""

from __future__ import annotations

from adt_ai.shared.object_types import PLSQL_OBJECT_TYPES

# get database objects overview
OVERVIEW_QUERY = """
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
-- No `fixed` column here: old ADT selected one as a NULL placeholder and filled it
-- in Python, and this port carried the placeholder without the fill. The overview
-- reads one point in time, so the catalog cannot answer "what did this run repair"
-- at all — the runner computes VALIDATED from the before/after invalid sets (#186).
SELECT
    o.object_type,
    COUNT(*) AS total,
    SUM(CASE WHEN o.status != 'VALID' THEN 1 ELSE 0 END) AS invalid,
    SUM(CASE
        WHEN o.object_type IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION', 'TRIGGER')
            AND NVL(p.PLSCOPE_SETTINGS, '-') NOT LIKE '%IDENTIFIERS:ALL%'
            THEN 1
        ELSE 0
    END) AS MISSING_PLSCOPE_IDENTIFIERS,
    SUM(CASE
        WHEN o.object_type IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION', 'TRIGGER')
            AND NVL(p.PLSCOPE_SETTINGS, '-') NOT LIKE '%STATEMENTS:ALL%'
            THEN 1
        ELSE 0
    END) AS MISSING_PLSCOPE_STATEMENTS
FROM user_objects o
JOIN objects_add a
    ON o.object_name        LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON o.object_name        LIKE g.object_like ESCAPE '\\'
LEFT JOIN user_plsql_object_settings p
    ON p.name               = o.object_name
    AND p.type              = o.object_type
WHERE 1 = 1
    AND g.object_like       IS NULL
    AND o.object_type       IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION',
        'TRIGGER', 'VIEW', 'MATERIALIZED VIEW', 'SYNONYM', 'TYPE', 'TYPE BODY')
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE o.object_type LIKE n_t.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE o.object_name LIKE n_n.object_like ESCAPE '\\'
    )
GROUP BY o.object_type
--
UNION ALL
SELECT
    'MVIEW LOG' AS object_type,
    COUNT(*)    AS total,
    NULL        AS invalid,
    NULL        AS MISSING_PLSCOPE_IDENTIFIERS,
    NULL        AS MISSING_PLSCOPE_STATEMENTS
FROM user_mview_logs l
JOIN objects_add a
    ON REPLACE(l.log_table, 'MLOG$_') LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON REPLACE(l.log_table, 'MLOG$_') LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like       IS NULL
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE REPLACE(l.log_table, 'MLOG$_') LIKE n_n.object_like ESCAPE '\\'
    )
    -- An MVIEW LOG has no user_objects row, so this branch supplies its own type name
    -- and matches it against the -type patterns exactly as every other branch matches
    -- o.object_type: does ANY supplied pattern match 'MVIEW LOG'? Matching on the
    -- shape of the filter instead would report logs for any M-ish -type, including
    -- ones that name a different object class entirely.
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE 'MVIEW LOG' LIKE n_t.object_like ESCAPE '\\'
    )
HAVING COUNT(*) > 0
ORDER BY 1
""".strip()


# get database objects to recompile
OBJECTS_TO_RECOMPILE_QUERY = """
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
    o.object_type,
    o.object_name
FROM (
    SELECT
        o.object_type,
        o.object_name
    FROM user_objects o
    JOIN objects_add a
        ON o.object_name        LIKE a.object_like ESCAPE '\\'
    LEFT JOIN objects_ignore g
        ON o.object_name        LIKE g.object_like ESCAPE '\\'
    WHERE 1 = 1
        AND g.object_like       IS NULL
        AND o.status            != 'VALID'
        AND o.object_type       NOT IN ('SEQUENCE')
        AND EXISTS (
            SELECT 1
            FROM object_types n_t
            WHERE o.object_type LIKE n_t.object_like ESCAPE '\\'
        )
        AND EXISTS (
            SELECT 1
            FROM object_names n_n
            WHERE o.object_name LIKE n_n.object_like ESCAPE '\\'
        )
    --
    UNION ALL
    SELECT
        o.object_type,
        o.object_name
    FROM user_objects o
    JOIN objects_add a
        ON o.object_name        LIKE a.object_like ESCAPE '\\'
    LEFT JOIN objects_ignore g
        ON o.object_name        LIKE g.object_like ESCAPE '\\'
    LEFT JOIN user_plsql_object_settings p
        ON p.name               = o.object_name
        AND p.type              = o.object_type
    WHERE 1 = 1
        AND g.object_like       IS NULL
        AND :force              = 'Y'
        AND o.object_type       IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION',
        'TRIGGER', 'VIEW', 'MATERIALIZED VIEW', 'SYNONYM', 'TYPE', 'TYPE BODY')
        -- When -force is combined with a compile modifier (-scope/-level/-native/
        -- -interpreted/-warnings), :drift_only = 'Y' narrows the force sweep to VALID
        -- PL/SQL objects whose *current* settings drift from the requested target
        -- state: any one mismatch (OR) selects the object, and non-PL/SQL types are
        -- skipped entirely (they carry no settings to drift from). Bare -force
        -- (:drift_only = 'N') keeps today's meaning — every matching object. Each
        -- predicate is self-gated by its own :drift_* flag, so an inactive modifier
        -- contributes nothing. The PL/Scope / warning LIKE checks mirror the
        -- OBJECTS_MISSING_PLSCOPE_QUERY gap pattern against USER_PLSQL_OBJECT_SETTINGS.
        AND (
            :drift_only         = 'N'
            OR (
                o.status        = 'VALID'
                AND o.object_type IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE',
                    'FUNCTION', 'TRIGGER')
                AND (
                    (:drift_code_type = 'Y'
                        AND NVL(p.plsql_code_type, 'INTERPRETED') != :target_code_type)
                    OR (:drift_level = 'Y'
                        AND NVL(p.plsql_optimize_level, -1) != :target_level)
                    OR (:drift_scope_identifiers = 'Y'
                        AND NVL(p.plscope_settings, '-') NOT LIKE '%IDENTIFIERS:ALL%')
                    OR (:drift_scope_statements = 'Y'
                        AND NVL(p.plscope_settings, '-') NOT LIKE '%STATEMENTS:ALL%')
                    OR (:drift_warn_severe = 'Y'
                        AND NVL(p.plsql_warnings, '-') NOT LIKE '%ENABLE:SEVERE%')
                    OR (:drift_warn_perf = 'Y'
                        AND NVL(p.plsql_warnings, '-') NOT LIKE '%ENABLE:PERFORMANCE%')
                    OR (:drift_warn_info = 'Y'
                        AND NVL(p.plsql_warnings, '-') NOT LIKE '%ENABLE:INFORMATIONAL%')
                )
            )
        )
        AND EXISTS (
            SELECT 1
            FROM object_types n_t
            WHERE o.object_type LIKE n_t.object_like ESCAPE '\\'
        )
        AND EXISTS (
            SELECT 1
            FROM object_names n_n
            WHERE o.object_name LIKE n_n.object_like ESCAPE '\\'
        )
) o
ORDER BY CASE o.object_type
    WHEN 'TYPE'                 THEN 1
    WHEN 'PACKAGE'              THEN 2
    WHEN 'PROCEDURE'            THEN 3
    WHEN 'FUNCTION'             THEN 4
    WHEN 'TRIGGER'              THEN 5
    WHEN 'PACKAGE BODY'         THEN 7
    WHEN 'TYPE BODY'            THEN 8
    WHEN 'MATERIALIZED VIEW'    THEN 9
    ELSE                             6 END, o.object_name
""".strip()


# get summary of errors
ERRORS_SUMMARY_QUERY = """
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
    e.type          AS object_type,
    e.name          AS object_name,
    COUNT(e.line)   AS errors,
    COALESCE(
        MIN(REGEXP_SUBSTR(e.text, 'ORA-\\d+')),
        MIN(REGEXP_SUBSTR(e.text, 'PLS-\\d+'))
    ) AS error
FROM user_errors e
JOIN objects_add a
    ON e.name       LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON e.name       LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE e.type LIKE n_t.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE e.name LIKE n_n.object_like ESCAPE '\\'
    )
    AND e.text          NOT LIKE 'PLW%'     -- skip warnings
GROUP BY
    e.type,
    e.name
ORDER BY 1, 2
""".strip()


# get the full compile error messages, one row per error line. Normal recompile
# output prints these when invalid objects remain, so an AI agent can jump straight
# to the offending line/position/text. Same 4-key scope and warning filter as
# ERRORS_SUMMARY_QUERY, so per-object detail rows match the summary errors count.
ERRORS_DETAIL_QUERY = """
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
    ROW_NUMBER() OVER (ORDER BY e.type, e.name, e.line, e.position, e.sequence) AS id,
    e.type          AS object_type,
    e.name          AS object_name,
    e.line          AS line,
    e.position      AS position,
    REGEXP_SUBSTR(e.text, 'ORA-\\d+') AS error,
    e.text          AS text
FROM user_errors e
JOIN objects_add a
    ON e.name       LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON e.name       LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE e.type LIKE n_t.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE e.name LIKE n_n.object_like ESCAPE '\\'
    )
    AND e.text          NOT LIKE 'PLW%'     -- skip warnings
ORDER BY e.type, e.name, e.line, e.position, e.sequence
""".strip()


# VALID PL/SQL objects missing full PL/Scope (IDENTIFIERS:ALL + STATEMENTS:ALL).
# Used as the dependencies refresh prerequisite: anything returned here is
# recompiled with scope=["ALL"] so USER_IDENTIFIERS / USER_STATEMENTS populate.
# Whole-schema scan — no binds. The IN-list is built from PLSQL_OBJECT_TYPES so
# it cannot drift from build_compile_statement's accepted types.
_PLSCOPE_TYPE_IN_LIST = ", ".join(f"'{object_type}'" for object_type in PLSQL_OBJECT_TYPES)

OBJECTS_MISSING_PLSCOPE_QUERY = f"""
SELECT
    o.object_type,
    o.object_name
FROM user_objects o
LEFT JOIN user_plsql_object_settings p
    ON p.name               = o.object_name
    AND p.type              = o.object_type
WHERE 1 = 1
    AND o.status            = 'VALID'
    AND o.object_type       IN ({_PLSCOPE_TYPE_IN_LIST})
    AND (
        NVL(p.PLSCOPE_SETTINGS, '-') NOT LIKE '%IDENTIFIERS:ALL%'
        OR NVL(p.PLSCOPE_SETTINGS, '-') NOT LIKE '%STATEMENTS:ALL%'
    )
ORDER BY CASE o.object_type
    WHEN 'PACKAGE'              THEN 1
    WHEN 'PROCEDURE'            THEN 2
    WHEN 'FUNCTION'             THEN 3
    WHEN 'TRIGGER'              THEN 4
    WHEN 'PACKAGE BODY'         THEN 5
    ELSE                             6 END, o.object_name
""".strip()
