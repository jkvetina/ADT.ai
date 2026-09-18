"""The -vpd report's SQL: enabled policies, their tables' columns and each
table's coverage (ADT #839). The functions' source is the export_db file (#884).

Modeled on the CORE framework's ``core_daily_missing_vpd_policies_v``, which reads
``all_policies``, ``all_tab_cols`` and ``all_tables`` and hard-codes ``TENANT_ID``.
Here the column arrives as ``:vpd_column`` instead, NULL for a bare ``-vpd``.

``-type`` says what ``-name`` matches, the three words being ``TABLE``, ``POLICY``
and ``FUNCTION``; the default ``%`` matches all three, so a row qualifies when the
pattern hits any of them. A disabled policy protects nothing, so both statements
read only ``enable = 'YES'``.
"""

from __future__ import annotations

_VPD_SCOPE_CTES = """
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
""".strip()


# One row per enabled policy on an object this schema owns. The statement flags and
# the policy type come back raw; the report folds them into SEL / DML / DYNAMIC.
# The function's source object's LAST_DDL_TIME and the database clock's offset say
# whether its exported file is older than the object (#884).
VPD_ASSIGNMENTS_QUERY = (
    _VPD_SCOPE_CTES
    + """
SELECT
    p.object_owner      AS table_owner,
    p.object_name       AS table_name,
    p.policy_name       AS policy_name,
    p.pf_owner          AS function_owner,
    p.package           AS package,
    p.function          AS function,
    p.sel               AS sel,
    p.ins               AS ins,
    p.upd               AS upd,
    p.del               AS del,
    p.policy_type       AS policy_type,
    TO_CHAR(o.last_ddl_time, 'YYYY-MM-DD HH24:MI:SS') AS function_ddl_time,
    TO_CHAR(SYSTIMESTAMP, 'TZH:TZM') AS db_utc_offset
FROM all_policies p
LEFT JOIN all_objects o
    ON o.owner          = p.pf_owner
    AND o.object_name   = NVL(p.package, p.function)
    AND o.object_type   = CASE WHEN p.package IS NULL THEN 'FUNCTION' ELSE 'PACKAGE BODY' END
JOIN objects_add a
    ON p.object_name    LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON p.object_name    LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND p.object_owner  = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
    AND p.enable        = 'YES'
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        CROSS JOIN object_types n_t
        WHERE 1 = 0
            OR ('TABLE'     LIKE n_t.object_like ESCAPE '\\'
                AND p.object_name LIKE n_n.object_like ESCAPE '\\')
            OR ('POLICY'    LIKE n_t.object_like ESCAPE '\\'
                AND p.policy_name LIKE n_n.object_like ESCAPE '\\')
            OR ('FUNCTION'  LIKE n_t.object_like ESCAPE '\\'
                AND (p.function LIKE n_n.object_like ESCAPE '\\'
                    OR p.package || '.' || p.function LIKE n_n.object_like ESCAPE '\\'))
    )
ORDER BY table_name, policy_name
"""
).strip()


# Every column of every table an enabled policy protects, so a predicate word can
# be checked against the table it was returned for, never guessed.
VPD_POLICY_COLUMNS_QUERY = """
SELECT DISTINCT
    c.table_name        AS table_name,
    c.column_name       AS column_name
FROM all_tab_columns c
JOIN all_policies p
    ON p.object_owner   = c.owner
    AND p.object_name   = c.table_name
WHERE 1 = 1
    AND c.owner         = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
    AND p.enable        = 'YES'
ORDER BY table_name, column_name
""".strip()


# One row per table in scope: does an enabled policy protect it, and does it carry
# the -vpd column. -name narrows the tables only when -type lets it name a TABLE;
# a POLICY or FUNCTION pattern cannot match a table that has no policy, so under
# those the count covers the whole schema.
VPD_TABLES_QUERY = (
    _VPD_SCOPE_CTES
    + """
SELECT
    t.table_name        AS table_name,
    CASE WHEN EXISTS (
        SELECT 1
        FROM all_policies p
        WHERE p.object_owner    = t.owner
            AND p.object_name   = t.table_name
            AND p.enable        = 'YES'
    ) THEN 'Y' ELSE 'N' END AS has_policy,
    CASE WHEN :vpd_column IS NOT NULL AND EXISTS (
        SELECT 1
        FROM all_tab_cols c
        WHERE c.owner           = t.owner
            AND c.table_name    = t.table_name
            AND c.column_name   LIKE :vpd_column ESCAPE '\\'
    ) THEN 'Y' ELSE 'N' END AS has_column
FROM all_tables t
JOIN objects_add a
    ON t.table_name     LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON t.table_name     LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND t.owner         = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
    AND t.dropped       = 'NO'
    AND t.nested        = 'NO'
    AND t.secondary     = 'N'
    AND (t.iot_type     IS NULL OR t.iot_type != 'IOT_OVERFLOW')
    -- Oracle's own segments behind a materialized view and its log hold no rows
    -- anybody queries directly, so they are nobody's candidate for a policy.
    AND NOT EXISTS (
        SELECT 1
        FROM all_mviews m
        WHERE m.owner           = t.owner
            AND m.container_name = t.table_name
    )
    AND NOT EXISTS (
        SELECT 1
        FROM all_mview_logs l
        WHERE l.log_owner       = t.owner
            AND l.log_table     = t.table_name
    )
    AND (
        NOT EXISTS (
            SELECT 1
            FROM object_types n_t
            WHERE 'TABLE' LIKE n_t.object_like ESCAPE '\\'
        )
        OR EXISTS (
            SELECT 1
            FROM object_names n_n
            WHERE t.table_name LIKE n_n.object_like ESCAPE '\\'
        )
    )
ORDER BY table_name
"""
).strip()
