from __future__ import annotations

import re

from adt_ai.shared.queries.scan_helpers import not_a_scan_helper

# Audit source/columns are SQL identifiers interpolated into the query, not binds
# (Oracle cannot bind a table or column name), so each is validated against this
# pattern before interpolation to keep the configured audit view from becoming an
# injection vector. Allows schema-qualified names and the usual identifier chars.
_AUDIT_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_$#.]*$")


def audit_authors_query(
    source: str,
    object_name_column: str,
    changed_by_column: str,
    changed_at_column: str | None = None,
) -> str:
    """Build the object-name query against a project's configured audit view.

    ``source``/``object_name_column``/``changed_by_column`` come from config and are
    interpolated as identifiers (validated first); the author list is bound via
    ``:authors`` using the same APEX_STRING.SPLIT pattern as the other list binds.

    ``changed_at_column`` is the optional 4th ``audit:`` key. Without it a DDL log
    carries no ordering, so the query can only answer *ever touched*, every object
    the author appears on, in any row. With it the query also reports each object's
    **latest** author (``LAST_CHANGED_BY``), which is what lets the caller mark an
    object someone else changed after you, and it accepts the same ``:recent_days``
    / ``:changed_since`` window the object listing uses, so ``-my -recent 1`` asks
    one coherent question instead of ANDing two unrelated sources.
    """
    identifiers = [source, object_name_column, changed_by_column]
    if changed_at_column is not None:
        identifiers.append(changed_at_column)
    for identifier in identifiers:
        if not _AUDIT_IDENTIFIER_RE.match(identifier):
            raise ValueError(f"invalid audit identifier: {identifier!r}")
    if changed_at_column is None:
        return f"""
SELECT DISTINCT UPPER({object_name_column}) AS object_name
FROM {source}
WHERE UPPER({changed_by_column}) IN (
    SELECT UPPER(TRIM(column_value))
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :authors), ','))
)
""".strip()
    # The window is applied in the inner query, before the ranking. That is safe
    # rather than distorting: the window is a floor (``>= cutoff``), so no row can
    # exist after it and be excluded, the last row inside the window is the last
    # row, full stop. The author tiebreak keeps the ranking deterministic when two
    # rows share a timestamp, which a per-second DDL log routinely produces.
    return f"""
SELECT object_name, MAX(last_changed_by) AS last_changed_by
FROM (
    SELECT
        UPPER({object_name_column}) AS object_name,
        UPPER({changed_by_column}) AS changed_by,
        LAST_VALUE(UPPER({changed_by_column})) OVER (
            PARTITION BY UPPER({object_name_column})
            ORDER BY {changed_at_column}, UPPER({changed_by_column})
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS last_changed_by
    FROM {source}
    WHERE (:recent_days IS NULL OR {changed_at_column} >= SYSDATE - :recent_days)
    AND (
        :changed_since IS NULL
        OR {changed_at_column} >= TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS')
    )
)
WHERE changed_by IN (
    SELECT UPPER(TRIM(column_value))
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :authors), ','))
)
GROUP BY object_name
""".strip()


OBJECTS_QUERY = f"""
WITH requested_types AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :object_type_filter), ',')) t
)
SELECT object_type, object_name
FROM user_objects
JOIN requested_types typ
    ON object_type LIKE typ.object_like ESCAPE '\\'
WHERE (:schema IS NOT NULL)
AND (:recent_days IS NULL OR last_ddl_time >= SYSDATE - :recent_days)
AND (
    :changed_since IS NULL
    OR last_ddl_time >= TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS')
)
AND object_name NOT LIKE 'SYS\\_%' ESCAPE '\\'
AND object_name NOT LIKE 'ISEQ$$_%'
AND object_name NOT LIKE 'ST%='
AND object_name NOT LIKE 'BIN$%'
AND object_name NOT LIKE 'MLOG$%'
AND NOT (object_type = 'TABLE' AND object_name IN (SELECT mview_name FROM user_mviews))
AND {not_a_scan_helper("object_name")}
AND NOT (
    object_type = 'SYNONYM'
    AND object_name != UPPER(object_name)
    AND EXISTS (
        SELECT 1
        FROM   user_synonyms s
        WHERE  s.synonym_name = object_name
        AND    s.table_name = UPPER(object_name)
        AND    UPPER(s.table_owner) = UPPER(:schema)
        AND    s.db_link IS NULL
    )
)
ORDER BY object_type, object_name
""".strip()

EXACT_OBJECTS_QUERY = f"""
SELECT object_type, object_name
FROM user_objects
WHERE (:schema IS NOT NULL)
AND (:recent_days IS NULL OR last_ddl_time >= SYSDATE - :recent_days)
AND (
    :changed_since IS NULL
    OR last_ddl_time >= TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS')
)
AND object_name = :object_name
AND object_name NOT LIKE 'SYS\\_%' ESCAPE '\\'
AND object_name NOT LIKE 'ISEQ$$_%'
AND object_name NOT LIKE 'ST%='
AND object_name NOT LIKE 'BIN$%'
AND object_name NOT LIKE 'MLOG$%'
AND NOT (object_type = 'TABLE' AND object_name IN (SELECT mview_name FROM user_mviews))
AND {not_a_scan_helper("object_name")}
AND NOT (
    object_type = 'SYNONYM'
    AND object_name != UPPER(object_name)
    AND EXISTS (
        SELECT 1
        FROM   user_synonyms s
        WHERE  s.synonym_name = object_name
        AND    s.table_name = UPPER(object_name)
        AND    UPPER(s.table_owner) = UPPER(:schema)
        AND    s.db_link IS NULL
    )
)
ORDER BY object_type, object_name
""".strip()

# An index dates its own changes through `user_objects`, exactly like the mview log
# below and every type in `OBJECTS_QUERY`. It used to be narrowed by
# `user_indexes.LAST_ANALYZED` instead, which records when statistics were gathered,
# and a statistics timestamp answers a different question in both directions
# (ADT #414's sweep subtask). Measured read-only on 2026-08-20: 4 of the local container's 7
# exportable indexes carry a `LAST_ANALYZED` 13 hours to a day after their
# `LAST_DDL_TIME`, moved by the maintenance window's stats job with no DDL at all,
# so a window between the two readings offered unchanged indexes as changed; and
# `LAST_ANALYZED` is NULL for 70 of DA's 646 indexes, where the comparison drops the
# row outright. The join loses nothing, an index always has its `user_objects` row:
# counted on both schemas the same day, 307 of 307 on DA and 7 of 7 on APPS.
#
# The day-aligned window went with it. That alignment existed because a statistics
# timestamp has day granularity; `LAST_DDL_TIME` is an instant, so the window is
# `SYSDATE - :recent_days` here as everywhere else, and a sub-day window needs no
# special case to stay out of the future.
INDEXES_QUERY = """
SELECT 'INDEX' AS object_type, t.index_name AS object_name, t.table_name,
       t.generated, t.constraint_index, c.constraint_name
FROM user_indexes t
JOIN user_objects o
    ON o.object_name = t.index_name
    AND o.object_type = 'INDEX'
LEFT JOIN user_constraints c
    ON c.table_name = t.table_name
    AND c.constraint_name = t.index_name
    AND c.constraint_type IN ('P', 'U')
WHERE (:schema IS NOT NULL)
AND (:recent_days IS NULL OR o.last_ddl_time >= SYSDATE - :recent_days)
AND (
    :changed_since IS NULL
    OR o.last_ddl_time >= TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS')
)
AND t.index_name NOT LIKE 'BIN$%'
AND t.index_name NOT LIKE 'SYS%$$'
-- The index Oracle builds for a materialized view log or an mview container.
-- `generated` is 'N' on both, because a user statement did create them, just
-- not one naming an index, so the filter below never reaches them and the
-- export wrote two files nothing in the repository can install.
AND t.index_name NOT LIKE 'I\\_MLOG$%' ESCAPE '\\'
AND t.index_name NOT LIKE 'I\\_SNAP$%' ESCAPE '\\'
AND t.generated = 'N'
AND t.constraint_index = 'NO'
AND c.constraint_name IS NULL
ORDER BY t.index_name
""".strip()

DDL_QUERY = """
SELECT DBMS_METADATA.GET_DDL(REPLACE(o.object_type, ' ', '_'), o.object_name) AS ddl
FROM user_objects o
WHERE o.object_type = :object_type
AND o.object_name = :object_name
""".strip()

# Unlike a JOB (`scheduler.py`), an mview log needs no invented signal: its LOG_TABLE
# is an ordinary TABLE in `user_objects`, and a table's LAST_DDL_TIME is a true DDL timestamp that
# DML does not move (measured on a client database 2026-08-20: 123 tables took DML at 05:07 with
# LAST_DDL_TIME still reading 08-11 / 08-13). So the window binds here exactly as it
# does for every other type, and this type never needed the widening ADT #414 gave it.
MVIEW_LOGS_QUERY = """
SELECT 'MVIEW LOG' AS object_type, l.master AS object_name
FROM user_mview_logs l
JOIN user_objects o
    ON o.object_name = l.log_table
    AND o.object_type = 'TABLE'
WHERE (:schema IS NOT NULL)
AND (:recent_days IS NULL OR o.last_ddl_time >= SYSDATE - :recent_days)
AND (
    :changed_since IS NULL
    OR o.last_ddl_time >= TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS')
)
ORDER BY l.master
""".strip()

MVIEW_LOG_DDL_QUERY = """
SELECT DBMS_METADATA.GET_DDL('MATERIALIZED_VIEW_LOG', l.log_table) AS ddl
FROM user_mview_logs l
WHERE l.master = :object_name
""".strip()

DBMS_METADATA_SETUP_QUERY = """
BEGIN
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'PARTITIONING', TRUE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'CONSTRAINTS', TRUE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'REF_CONSTRAINTS', TRUE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(
        DBMS_METADATA.SESSION_TRANSFORM, 'CONSTRAINTS_AS_ALTER', FALSE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(
        DBMS_METADATA.SESSION_TRANSFORM, 'PHYSICAL_PROPERTIES', FALSE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SEGMENT_ATTRIBUTES', FALSE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'STORAGE', FALSE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'TABLESPACE', FALSE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'INMEMORY', TRUE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'SQLTERMINATOR', FALSE);
    DBMS_METADATA.SET_TRANSFORM_PARAM(DBMS_METADATA.SESSION_TRANSFORM, 'PRETTY', TRUE);
END;
""".strip()

# One row per grantee (`#923`). Old ADT listed every grantee of an object in one
# statement next to every privilege any of them held, so SELECT to REPORTING
# beside SELECT and DELETE to APP_USER replayed as a DELETE for REPORTING too.
# It also wrote the statement here, where `keep_owner` never arrived, so the query
# now returns the parts and `content._render_grants_made` writes every line.
GRANTS_MADE_QUERY = """
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
    t.type AS object_type,
    t.table_name AS object_name,
    t.grantee,
    t.privs AS privileges,
    t.grantable
FROM (
    SELECT
        t.type,
        t.table_name,
        t.grantee,
        LISTAGG(DISTINCT t.privilege, ', ') WITHIN GROUP (ORDER BY t.privilege) AS privs,
        t.grantable
    FROM user_tab_privs_made t
    JOIN objects_add a
        ON t.table_name LIKE a.object_like ESCAPE '\\'
    LEFT JOIN objects_ignore g
        ON t.table_name LIKE g.object_like ESCAPE '\\'
    WHERE g.object_like IS NULL
    AND t.table_name NOT LIKE 'ST%='
    AND t.table_name NOT LIKE 'BIN$%'
    AND t.grantor = USER
    AND t.type NOT IN ('USER')
    GROUP BY
        t.type,
        t.table_name,
        t.grantee,
        t.grantable
) t
ORDER BY t.type, t.table_name, t.grantee, t.grantable
""".strip()

GRANTS_RECEIVED_QUERY = """
SELECT owner, table_name AS object_name, type AS object_type,
       grantor, privilege, grantable
FROM user_tab_privs_recd
ORDER BY owner, table_name, privilege
""".strip()

USER_PRIVILEGES_QUERY = """
SELECT 'ROLE' AS privilege_kind, granted_role AS name, admin_option
FROM user_role_privs
UNION ALL
SELECT 'SYSTEM' AS privilege_kind, privilege AS name, admin_option
FROM user_sys_privs
ORDER BY privilege_kind, name
""".strip()

# 23ai schema privileges: one grant covering every object of one schema, present
# and future (`GRANT SELECT ANY TABLE ON SCHEMA hr TO scott`). Its own read rather
# than a third branch of USER_PRIVILEGES_QUERY because `user_schema_privs` arrived
# in 23ai and ADT supports older databases, where a union would fail the whole
# privilege read instead of returning the two kinds that do exist (`#740`).
SCHEMA_PRIVILEGES_QUERY = """
SELECT privilege, schema, admin_option
FROM   user_schema_privs
ORDER  BY schema, privilege
""".strip()

# A 23ai assertion, whose `user_objects` row is typed `UNDEFINED` rather than
# `ASSERTION` (measured on the live 26ai fixture 2026-09-08), so `OBJECTS_QUERY`
# can never name it and `UNDEFINED` is too shared a bucket to export wholesale.
# The name is the identity, and `user_assertions` is where it lives.
#
# The join to `user_objects` on OBJECT_ID is what makes the window bind here the
# way it binds for an ordinary type: `user_assertions` carries no timestamp of its
# own, but the `UNDEFINED` row does, so an assertion needs none of the invented
# change signal a JOB does.
ASSERTIONS_QUERY = """
SELECT 'ASSERTION' AS object_type, a.assertion_name AS object_name
FROM   user_assertions a
JOIN   user_objects o
    ON o.object_id = a.object_id
WHERE  (:schema IS NOT NULL)
AND    (:recent_days IS NULL OR o.last_ddl_time >= SYSDATE - :recent_days)
AND    (
    :changed_since IS NULL
    OR o.last_ddl_time >= TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS')
)
ORDER  BY a.assertion_name
""".strip()

# `DBMS_METADATA.GET_DDL` refuses the type outright -- `ORA-31600: invalid input
# value ASSERTION for parameter OBJECT_TYPE`, measured on the same fixture -- so the
# dictionary's own `DEFINITION_SQL` is the only source. It carries the whole
# statement, owner-qualified and with CRLF endings the normalizer undoes.
ASSERTION_DDL_QUERY = """
SELECT definition_sql AS ddl
FROM   user_assertions
WHERE  assertion_name = :object_name
""".strip()

DIRECTORIES_QUERY = """
SELECT directory_name, directory_path
FROM all_directories
ORDER BY directory_name
""".strip()

COMMENTS_QUERY = """
WITH objects_prefix AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_prefix), ',')) t
),
comment_kinds AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :object_type), ',')) t
),
object_names AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :object_name), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
)
SELECT object_type, object_name, column_name, comments
FROM (
    SELECT o.object_type, m.table_name AS object_name,
           CAST(NULL AS VARCHAR2(128)) AS column_name, comments,
           0 AS sort_order
    FROM user_tab_comments m
    JOIN user_objects o
        ON o.object_name = m.table_name
        AND o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
    JOIN objects_prefix pfx
        ON o.object_name LIKE pfx.object_like ESCAPE '\\'
    JOIN comment_kinds typ
        ON o.object_type LIKE typ.object_like ESCAPE '\\'
    JOIN object_names nam
        ON o.object_name LIKE nam.object_like ESCAPE '\\'
    LEFT JOIN objects_ignore ign
        ON o.object_name LIKE ign.object_like ESCAPE '\\'
    WHERE ign.object_like IS NULL
    AND o.object_name NOT LIKE 'BIN$%'
    UNION ALL
    SELECT o.object_type, m.table_name AS object_name, m.column_name, m.comments,
           c.column_id AS sort_order
    FROM user_col_comments m
    JOIN user_tab_cols c
        ON c.table_name = m.table_name
        AND c.column_name = m.column_name
    JOIN user_objects o
        ON o.object_name = m.table_name
        AND o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
    JOIN objects_prefix pfx
        ON o.object_name LIKE pfx.object_like ESCAPE '\\'
    JOIN comment_kinds typ
        ON o.object_type LIKE typ.object_like ESCAPE '\\'
    JOIN object_names nam
        ON o.object_name LIKE nam.object_like ESCAPE '\\'
    LEFT JOIN objects_ignore ign
        ON o.object_name LIKE ign.object_like ESCAPE '\\'
    WHERE ign.object_like IS NULL
    AND o.object_name NOT LIKE 'BIN$%'
    AND (
        m.column_name NOT IN (
            'UPDATED_BY', 'UPDATED_ON', 'UPDATED_AT',
            'CHANGED_BY', 'CHANGED_ON', 'CHANGED_AT',
            'CREATED_BY', 'CREATED_ON', 'CREATED_AT'
        )
        OR m.comments IS NOT NULL
    )
)
WHERE (:schema IS NOT NULL)
ORDER BY object_name, sort_order
""".strip()

# The retention clauses of an immutable or blockchain table (`#736`). They are
# NOT read from the DDL: `DBMS_METADATA` only emits them when
# `SEGMENT_ATTRIBUTES` is on, and ADT deliberately turns that off (see
# `DBMS_METADATA_SETUP_QUERY`) so tablespaces and PCTFREE stay out of the repo.
# Reading them here keeps that setting and still exports the clause the table is
# defined by. Both views are 21c; on an older database the query fails to parse
# and the caller treats that as "this database has no such tables".
TABLE_RETENTION_QUERY = """
SELECT table_name,
       row_retention,
       row_retention_locked,
       table_inactivity_retention,
       CAST(NULL AS VARCHAR2(128)) AS hash_algorithm,
       table_version
FROM   user_immutable_tables
WHERE  (:schema IS NOT NULL)
UNION ALL
SELECT table_name,
       row_retention,
       row_retention_locked,
       table_inactivity_retention,
       hash_algorithm,
       table_version
FROM   user_blockchain_tables
WHERE  (:schema IS NOT NULL)
ORDER  BY table_name
""".strip()
