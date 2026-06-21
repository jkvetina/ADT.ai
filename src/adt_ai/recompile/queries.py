"""SQL constants and generated SQL templates for the recompile module.

Ported faithfully from old ADT ``lib/queries.py`` (overview /
objects_to_recompile / objects_errors_summary) and ``recompile.build_query``.
"""

from __future__ import annotations

from adt_ai.sql_identifiers import safe_identifier, safe_object_type

# PL/SQL object types that accept the PLSQL_* compilation flags + REUSE SETTINGS.
PLSQL_OBJECT_TYPES = ("PACKAGE", "PACKAGE BODY", "PROCEDURE", "FUNCTION", "TRIGGER")


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
)
SELECT
    o.object_type,
    COUNT(*) AS total,
    NULL AS fixed,
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
    AND (o.object_type      LIKE :object_type ESCAPE '\\' OR :object_type IS NULL)
    AND (o.object_name      LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
GROUP BY o.object_type
--
UNION ALL
SELECT
    'MVIEW LOG' AS object_type,
    COUNT(*)    AS total,
    NULL        AS fixed,
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
    AND REPLACE(l.log_table, 'MLOG$_') LIKE :object_name ESCAPE '\\'
    AND (:object_type LIKE 'M%' OR :object_type IS NULL)
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
        AND (o.object_type      LIKE :object_type ESCAPE '\\' OR :object_type IS NULL)
        AND (o.object_name      LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
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
    WHERE 1 = 1
        AND g.object_like       IS NULL
        AND :force              = 'Y'
        AND o.object_type       IN ('PACKAGE', 'PACKAGE BODY', 'PROCEDURE', 'FUNCTION',
        'TRIGGER', 'VIEW', 'MATERIALIZED VIEW', 'SYNONYM', 'TYPE', 'TYPE BODY')
        AND (o.object_type      LIKE :object_type ESCAPE '\\' OR :object_type IS NULL)
        AND (o.object_name      LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
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
    AND (e.type         LIKE :object_type ESCAPE '\\' OR :object_type IS NULL)
    AND (e.name         LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
    AND e.text          NOT LIKE 'PLW%'     -- skip warnings
GROUP BY
    e.type,
    e.name
ORDER BY 1, 2
""".strip()


# get the full compile error messages, one row per error line. ``-errors`` prints
# these so an AI agent can jump straight to the offending line/position/text. Same
# 4-key scope and warning filter as ERRORS_SUMMARY_QUERY, so the per-object detail
# rows match that summary's ``errors`` count exactly.
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
)
SELECT
    ROW_NUMBER() OVER (ORDER BY e.type, e.name, e.line, e.position, e.sequence) AS id,
    e.type          AS object_type,
    e.name          AS object_name,
    e.line          AS line,
    e.position      AS position,
    e.text          AS text
FROM user_errors e
JOIN objects_add a
    ON e.name       LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON e.name       LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND (e.type         LIKE :object_type ESCAPE '\\' OR :object_type IS NULL)
    AND (e.name         LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
    AND e.text          NOT LIKE 'PLW%'     -- skip warnings
ORDER BY e.type, e.name, e.line, e.position, e.sequence
""".strip()


# list session/object locks held on the schema's objects (gv$locked_object).
# Portable across any schema: user_objects keeps it scoped to the connected
# user, gv$session adds the holding session's identity. Requires SELECT on the
# gv$ views; callers degrade gracefully when that grant is missing.
LOCKED_OBJECTS_QUERY = """
WITH objects_add AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:objects_prefix, '%')), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
)
SELECT
    o.object_type,
    o.object_name,
    lo.session_id                       AS sid,
    s.serial#                           AS serial#,
    NVL(lo.oracle_username, s.username) AS oracle_user,
    lo.os_user_name                     AS os_user,
    s.machine                           AS machine,
    s.program                           AS program,
    DECODE(lo.locked_mode,
        0, 'NONE',
        1, 'NULL',
        2, 'ROW SHARE',
        3, 'ROW EXCLUSIVE',
        4, 'SHARE',
        5, 'SHARE ROW EXCLUSIVE',
        6, 'EXCLUSIVE',
        TO_CHAR(lo.locked_mode)) AS lock_mode
FROM gv$locked_object lo
JOIN user_objects o
    ON o.object_id          = lo.object_id
LEFT JOIN gv$session s
    ON s.inst_id            = lo.inst_id
    AND s.sid               = lo.session_id
JOIN objects_add a
    ON o.object_name        LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON o.object_name        LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like       IS NULL
    AND (o.object_type      LIKE :object_type ESCAPE '\\' OR :object_type IS NULL)
    AND (o.object_name      LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
ORDER BY o.object_type, o.object_name, lo.session_id
""".strip()


# materialized-view health (staleness, compile state, last refresh, indexes).
# Modeled on CORE23's core_daily_materialized_views_v but rewritten against the
# portable user_* views so it works in any schema. Scoped by name/prefix/ignore;
# object_type is irrelevant here because -mviews opts MVs in explicitly.
MATERIALIZED_VIEWS_QUERY = """
WITH objects_add AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:objects_prefix, '%')), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
)
SELECT
    m.mview_name                                                          AS object_name,
    MAX(m.staleness)                                                      AS staleness,
    MAX(m.compile_state)                                                  AS compile_state,
    MAX(TO_CHAR(m.last_refresh_end_time, 'YYYY-MM-DD HH24:MI'))           AS last_refreshed_at,
    MAX(ROUND(86400 * (m.last_refresh_end_time - m.last_refresh_date)))   AS last_timer,
    MAX(m.refresh_method)                                                 AS refresh_method,
    MAX(CASE WHEN EXISTS (
            SELECT 1
            FROM user_mview_detail_relations d
            JOIN user_mview_logs l
                ON l.master = d.detailobj_name
            WHERE d.mview_name = m.mview_name
        ) THEN 'Y' END)                                                   AS has_log,
    LISTAGG(i.index_name, ', ') WITHIN GROUP (ORDER BY i.index_name)      AS indexes
FROM user_mviews m
LEFT JOIN user_indexes i
    ON i.table_name         = m.mview_name
JOIN objects_add a
    ON m.mview_name         LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON m.mview_name         LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like       IS NULL
    AND (m.mview_name       LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
GROUP BY m.mview_name
ORDER BY m.mview_name
""".strip()


# synonym health: map each local synonym to its target owner object, the
# privileges this schema holds on that target (collapsed to ALL when the set is
# complete), whether they are grantable, and the target object's validity.
# Modeled on CORE23's core_daily_synonyms_v but, like the MV report, scoped with
# the portable objects_add/objects_ignore CTE so -synonyms works in any schema.
# object_type is irrelevant here because -synonyms opts synonyms in explicitly;
# the report-only flag never compiles, so there is no :force bind either.
SYNONYMS_QUERY = """
WITH objects_add AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:objects_prefix, '%')), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
)
SELECT
    s.synonym_name                                                       AS synonym_name,
    g.type                                                               AS object_type,
    s.table_owner                                                        AS owner,
    s.table_name                                                         AS object_name,
    REPLACE(
        LISTAGG(g.privilege, ', ') WITHIN GROUP (ORDER BY g.privilege),
        'ALTER, DEBUG, DELETE, FLASHBACK, INDEX, INSERT, ON COMMIT REFRESH, '
            || 'QUERY REWRITE, READ, REFERENCES, SELECT, UPDATE',
        'ALL'
    )                                                                    AS privileges,
    CASE WHEN g.grantable = 'YES' THEN 'Y' END                           AS is_grantable,
    NVL(o.status, 'UNKNOWN')                                             AS status
FROM user_synonyms s
LEFT JOIN user_tab_privs_recd g
    ON  g.owner             = s.table_owner
    AND g.table_name        = s.table_name
LEFT JOIN all_objects o
    ON  o.owner             = s.table_owner
    AND o.object_name       = s.table_name
    AND o.object_type       = g.type
JOIN objects_add a
    ON s.synonym_name       LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore x
    ON s.synonym_name       LIKE x.object_like ESCAPE '\\'
WHERE 1 = 1
    AND x.object_like       IS NULL
    AND (s.synonym_name     LIKE :object_name ESCAPE '\\' OR :object_name IS NULL)
GROUP BY s.synonym_name, s.table_owner, s.table_name, g.type, g.grantable, o.status
ORDER BY s.synonym_name
""".strip()


def build_compile_statement(
    object_type: str,
    object_name: str,
    *,
    native: bool = False,
    optimize_level: int | None = None,
    scope: list[str] | None = None,
    warnings: list[str] | None = None,
) -> str:
    """Build the ALTER ... COMPILE statement for one object.

    Faithful port of old ADT ``Recompile.build_query`` — including the
    ``'PERFORMANE'`` spelling accepted for the PERFORMANCE warning.
    """
    safe_object_type(object_type, role="object type")
    safe_identifier(object_name, role="object name")
    type_body   = " BODY" if "BODY" in object_type else ""
    type_family = object_type.replace(" BODY", "")
    extras      = ""

    # extra stuff for code objects
    if object_type in PLSQL_OBJECT_TYPES:
        extras += " PLSQL_CODE_TYPE = " + ("NATIVE" if native else "INTERPRETED")

        # setup optimize level
        if optimize_level is not None and 1 <= optimize_level <= 3:
            extras += " PLSQL_OPTIMIZE_LEVEL = " + str(optimize_level)

        # setup scope
        if isinstance(scope, list):
            scope_value = ""
            scope_value += "IDENTIFIERS:ALL," if ("IDENTIFIERS" in scope or "ALL" in scope) else ""
            scope_value += "STATEMENTS:ALL," if ("STATEMENTS" in scope or "ALL" in scope) else ""
            extras += " PLSCOPE_SETTINGS = '" + scope_value.rstrip(",") + "'"

        # setup warnings
        if isinstance(warnings, list):
            warnings_value = ""
            warnings_value += "ENABLE:SEVERE," if ("SEVERE" in warnings) else ""
            warnings_value += (
                "ENABLE:PERFORMANCE," if ("PERF" in warnings or "PERFORMANE" in warnings) else ""
            )
            warnings_value += (
                "ENABLE:INFORMATIONAL,"
                if ("INFO" in warnings or "INFORMATIONAL" in warnings)
                else ""
            )
            extras += " PLSQL_WARNINGS = '" + warnings_value.strip(",").replace(",", "','") + "'"

        extras += " REUSE SETTINGS"

    return f"ALTER {type_family} {object_name} COMPILE{type_body} {extras}"


def _refresh_method_code(refresh_method: str | None) -> str:
    """Map an MV's configured refresh_method to a DBMS_MVIEW.REFRESH method char.

    COMPLETE → 'C', FAST → 'F'. FORCE, NEVER, anything unknown, and a missing
    method all fall back to '?' (let Oracle decide). The point is to refresh a
    view with the method already attached to it, never silently re-picking and
    flipping a COMPLETE view to FAST.
    """
    method = (refresh_method or "").strip().upper()
    if method == "COMPLETE":
        return "C"
    if method == "FAST":
        return "F"
    return "?"


def mview_type_code(refresh_method: str | None, has_log: bool = False) -> str:
    """Map an MV's configured refresh_method to the F/C TYPE shown in the report.

    Unlike :func:`_refresh_method_code` (which feeds DBMS_MVIEW.REFRESH and leaves
    FORCE as '?' so Oracle decides at runtime), the *display* always resolves to a
    clean letter: COMPLETE → 'C', FAST → 'F'. FORCE resolves to what Oracle would
    actually do — 'F' when a usable MV log exists, 'C' otherwise. NEVER → 'N', and a
    missing method → '' (nothing to show).
    """
    method = (refresh_method or "").strip().upper()
    if method == "COMPLETE":
        return "C"
    if method == "FAST":
        return "F"
    if method == "FORCE":
        return "F" if has_log else "C"
    return method[:1]


def build_refresh_statement(object_name: str, refresh_method: str | None = None) -> str:
    """Build the DBMS_MVIEW.REFRESH call that refreshes one materialized view.

    Staleness is fixed by refreshing (not compiling), using the method the MV is
    configured with (``refresh_method``) so the tool never changes a view's
    refresh type. Unknown/missing methods fall back to '?' (Oracle decides).
    """
    safe_identifier(object_name, role="object name")
    method = _refresh_method_code(refresh_method)
    return f"BEGIN DBMS_MVIEW.REFRESH('{object_name}', '{method}'); END;"
