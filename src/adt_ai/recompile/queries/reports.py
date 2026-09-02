"""Report SQL constants for the recompile module."""

from __future__ import annotations

# materialized-view health (staleness, compile state, last refresh, indexes).
# Modeled on the CORE framework's core_daily_materialized_views_v but rewritten against the
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
),
object_names AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:object_name, '%')), ',')) t
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
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE m.mview_name LIKE n_n.object_like ESCAPE '\\'
    )
GROUP BY m.mview_name
ORDER BY m.mview_name
""".strip()


# synonym health: local synonyms mapped to targets, privileges, grantability, and validity.
# Modeled on the CORE framework's core_daily_synonyms_v, scoped with the portable objects_add /
# objects_ignore CTE. The report-only flag has no :object_type or :force bind.
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
),
object_names AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM NVL(:object_name, '%')), ',')) t
),
target_objects AS (
    SELECT owner, object_name, object_type, status
    FROM (
        SELECT
            o.owner,
            o.object_name,
            o.object_type,
            o.status,
            ROW_NUMBER() OVER (
                PARTITION BY o.owner, o.object_name
                ORDER BY CASE WHEN o.object_type LIKE '% BODY' THEN 2 ELSE 1 END, o.object_type
            ) AS rn
        FROM all_objects o
    )
    WHERE rn = 1
)
SELECT
    s.synonym_name                                                       AS synonym_name,
    COALESCE(g.type, o.object_type)                                      AS object_type,
    s.table_owner                                                        AS owner,
    s.table_name                                                         AS object_name,
    REPLACE(
        LISTAGG(g.privilege, ', ') WITHIN GROUP (ORDER BY g.privilege),
        'ALTER, DEBUG, DELETE, FLASHBACK, INDEX, INSERT, ON COMMIT REFRESH, '
            || 'QUERY REWRITE, READ, REFERENCES, SELECT, UPDATE',
        'ALL'
    )                                                                    AS privileges,
    MAX(CASE WHEN g.grantable = 'YES' THEN 'Y' END)                      AS is_grantable,
    NVL(o.status, 'UNKNOWN')                                             AS status
FROM user_synonyms s
LEFT JOIN user_tab_privs_recd g
    ON  g.owner             = s.table_owner
    AND g.table_name        = s.table_name
LEFT JOIN target_objects o
    ON  o.owner             = s.table_owner
    AND o.object_name       = s.table_name
JOIN objects_add a
    ON s.synonym_name       LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore x
    ON s.synonym_name       LIKE x.object_like ESCAPE '\\'
WHERE 1 = 1
    AND x.object_like       IS NULL
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE s.synonym_name LIKE n_n.object_like ESCAPE '\\'
    )
GROUP BY
    s.synonym_name,
    s.table_owner,
    s.table_name,
    COALESCE(g.type, o.object_type),
    o.status
ORDER BY owner, status, synonym_name, object_type, object_name, privileges
""".strip()


# disabled object health: disabled constraints/triggers and invalid/function-disabled indexes.
# Modeled on the CORE framework's core_daily_disabled_objects_v, scoped with the portable
# objects_add / objects_ignore CTE. Alone among the report-only flags, -disabled spans
# three object types, so it honours :object_type (-type) as well as :object_name
# (-name): each UNION branch already emits its type as a literal, so the filter just
# compares against it and `-disabled -type TRIGGER` reports only disabled triggers.
# No :force bind.
DISABLED_OBJECTS_QUERY = """
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
    t.owner             AS owner,
    'CONSTRAINT'        AS object_type,
    t.constraint_name   AS object_name,
    t.table_name        AS table_name
FROM all_constraints t
JOIN objects_add a
    ON t.constraint_name   LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON t.constraint_name   LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like      IS NULL
    AND t.owner            = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
    AND t.status           = 'DISABLED'
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE t.constraint_name LIKE n_n.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE 'CONSTRAINT' LIKE n_t.object_like ESCAPE '\\'
    )
UNION ALL
SELECT
    t.owner             AS owner,
    'INDEX'             AS object_type,
    t.index_name        AS object_name,
    t.table_name        AS table_name
FROM all_indexes t
JOIN objects_add a
    ON t.index_name        LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON t.index_name        LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like      IS NULL
    AND t.owner            = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
    AND (t.status          != 'VALID' OR t.funcidx_status != 'ENABLED')
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE t.index_name LIKE n_n.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE 'INDEX' LIKE n_t.object_like ESCAPE '\\'
    )
UNION ALL
SELECT
    t.owner             AS owner,
    'TRIGGER'           AS object_type,
    t.trigger_name      AS object_name,
    t.table_name        AS table_name
FROM all_triggers t
JOIN objects_add a
    ON t.trigger_name      LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON t.trigger_name      LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like      IS NULL
    AND t.owner            = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
    AND t.status           = 'DISABLED'
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE t.trigger_name LIKE n_n.object_like ESCAPE '\\'
    )
    AND EXISTS (
        SELECT 1
        FROM object_types n_t
        WHERE 'TRIGGER' LIKE n_t.object_like ESCAPE '\\'
    )
ORDER BY owner, object_type, object_name
""".strip()


# scheduler job health: today's run details, status, duration, count, and error text.
# Modeled on the CORE framework's core_daily_schedulers_v, scoped with the portable
# objects_add / objects_ignore CTE. The report-only flag has no :object_type or
# :force bind.
SCHEDULER_JOBS_QUERY = """
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
)
SELECT
    t.owner                                                         AS owner,
    t.job_name                                                      AS job_name,
    MAX(TO_CHAR(t.actual_start_date, 'YYYY-MM-DD HH24:MI'))         AS last_start_date,
    t.status                                                        AS status,
    MAX(t.run_duration)                                             AS run_duration,
    MAX(t.cpu_used)                                                 AS cpu_used,
    COUNT(*)                                                        AS count_,
    REGEXP_REPLACE(t.errors, '<[^>]*>', '')                         AS error
FROM all_scheduler_job_run_details t
JOIN objects_add a
    ON t.job_name       LIKE a.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON t.job_name       LIKE g.object_like ESCAPE '\\'
WHERE 1 = 1
    AND g.object_like   IS NULL
    AND t.owner         = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')
    AND t.log_date      >= TRUNC(SYSDATE)
    AND t.log_date      <  TRUNC(SYSDATE) + 1
    AND EXISTS (
        SELECT 1
        FROM object_names n_n
        WHERE t.job_name LIKE n_n.object_like ESCAPE '\\'
    )
GROUP BY
    t.owner,
    t.job_name,
    t.status,
    REGEXP_REPLACE(t.errors, '<[^>]*>', '')
ORDER BY owner, job_name, last_start_date
""".strip()
