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
