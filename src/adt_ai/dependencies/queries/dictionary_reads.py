"""Raw Oracle dictionary reads for the ``dependencies`` raw-mirror database.

The live-Oracle half of the module's SQL: these SELECTs *populate* the SQLite raw
mirror that the sibling :mod:`adt_ai.dependencies.queries.objects` then reads
offline. Both are re-exported by the package ``__init__``.

Each ``USER_*`` SELECT pulls one dictionary view verbatim for the *connected*
schema, the views scope implicitly to the session user, so there is no bind and
no ``OWNER`` column; the runner stamps ``OWNER`` per scope when it writes the row
(see :meth:`adt_ai.dependencies.store.DependencyStore.refresh_schema`). The
``APEX_*`` SELECTs are filtered by ``:app_id`` and the runner stamps
``APPLICATION_ID``. Projections include only columns consumed by query modes or
generated artifacts; the store drops any column it does not know and NULL-fills
any it expects but does not receive, so the projection and the table schema
cannot silently drift.
"""

from __future__ import annotations

from adt_ai.shared.apex_version import apex_version_tuple

# ------------------------------------------------------------------- USER_* axis

USER_OBJECTS_QUERY = """
SELECT object_name, object_type, last_ddl_time
FROM user_objects
WHERE oracle_maintained = 'N'
  AND object_type != 'LOB'
""".strip()

# The DATABASE server's UTC offset, recorded once per refreshed scope.
#
# `LAST_DDL_TIME` is a DATE, a naive wall-clock reading taken on the database
# host, and `patch -create` compares it against a repo file's mtime, which is
# an absolute epoch taken on THIS host. Resolving the first in the second's
# zone compares two readings that were never on the same clock, and the error
# is exactly the offset between them (ADT #394).
#
# `SYSTIMESTAMP` is the reading to take, not `DBTIMEZONE` and not
# `SESSIONTIMEZONE`. `SYSDATE` and `LAST_DDL_TIME` come from the database
# host's own clock, which is the clock `SYSTIMESTAMP` carries the offset of;
# `SESSIONTIMEZONE` is whatever python-oracledb set from THIS host, so reading
# it would hand back the same bug wearing a database-side spelling.
DB_UTC_OFFSET_QUERY = """
SELECT TO_CHAR(SYSTIMESTAMP, 'TZH:TZM') AS DB_UTC_OFFSET
FROM dual
""".strip()

# `-refresh -recent` narrowing: exactly one of the two binds is non-NULL, the
# scope's stored `refreshes` stamp (bare -recent) or an N-day window
# (-recent N). COALESCE picks whichever mode is active.
USER_OBJECTS_RECENT_QUERY = """
SELECT object_name, object_type, last_ddl_time
FROM user_objects
WHERE oracle_maintained = 'N'
  AND object_type != 'LOB'
  AND last_ddl_time >= COALESCE(
    TO_DATE(:changed_since, 'YYYY-MM-DD HH24:MI:SS'),
    SYSDATE - :recent_days
  )
""".strip()

_OBJECT_NAME_FILTER_CTE = """
WITH object_names AS (
    SELECT /*+ MATERIALIZE */ UPPER(TRIM(t.column_value)) AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :object_name_filter), ',')) t
    WHERE TRIM(t.column_value) IS NOT NULL
)
""".strip()

USER_OBJECTS_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE}
SELECT o.object_name, o.object_type, o.last_ddl_time
FROM user_objects o
WHERE oracle_maintained = 'N'
  AND object_type != 'LOB'
  AND EXISTS (
    SELECT 1
    FROM object_names n
    WHERE o.object_name LIKE n.object_like
)
""".strip()

USER_DEPENDENCIES_QUERY = """
SELECT d.name, d.type, d.referenced_owner, d.referenced_name, d.referenced_type
FROM user_dependencies d
WHERE NOT EXISTS (
    SELECT 1 FROM all_users u
    WHERE u.username = d.referenced_owner
    AND u.oracle_maintained = 'Y'
)
""".strip()

USER_DEPENDENCIES_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE}
SELECT d.name, d.type, d.referenced_owner, d.referenced_name, d.referenced_type
FROM user_dependencies d
WHERE NOT EXISTS (
    SELECT 1 FROM all_users u
    WHERE u.username = d.referenced_owner
    AND u.oracle_maintained = 'Y'
)
AND EXISTS (
    SELECT 1
    FROM object_names n
    WHERE d.name LIKE n.object_like
       OR d.referenced_name LIKE n.object_like
)
""".strip()

USER_CONSTRAINTS_QUERY = """
SELECT constraint_name, constraint_type, table_name, r_owner, r_constraint_name
FROM user_constraints
""".strip()

USER_CONSTRAINTS_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE},
target_constraints AS (
    SELECT /*+ MATERIALIZE */ c.constraint_name
    FROM user_constraints c
    WHERE EXISTS (
        SELECT 1
        FROM object_names n
        WHERE c.table_name LIKE n.object_like
    )
)
SELECT c.constraint_name, c.constraint_type, c.table_name,
       c.r_owner, c.r_constraint_name
FROM user_constraints c
WHERE EXISTS (
    SELECT 1
    FROM object_names n
    WHERE c.table_name LIKE n.object_like
)
OR (
    c.r_owner = USER
    AND c.r_constraint_name IN (
        SELECT constraint_name
        FROM target_constraints
    )
)
""".strip()

USER_CONS_COLUMNS_QUERY = """
SELECT constraint_name, table_name, column_name, position
FROM user_cons_columns
""".strip()

USER_CONS_COLUMNS_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE},
target_constraints AS (
    SELECT /*+ MATERIALIZE */ c.constraint_name
    FROM user_constraints c
    WHERE EXISTS (
        SELECT 1
        FROM object_names n
        WHERE c.table_name LIKE n.object_like
    )
),
scoped_constraints AS (
    SELECT /*+ MATERIALIZE */ c.constraint_name
    FROM user_constraints c
    WHERE EXISTS (
        SELECT 1
        FROM object_names n
        WHERE c.table_name LIKE n.object_like
    )
    OR (
        c.r_owner = USER
        AND c.r_constraint_name IN (
            SELECT constraint_name
            FROM target_constraints
        )
    )
)
SELECT cc.constraint_name, cc.table_name, cc.column_name, cc.position
FROM user_cons_columns cc
WHERE cc.constraint_name IN (
    SELECT constraint_name
    FROM scoped_constraints
)
""".strip()

# PL/Scope identifier usages, populated only for objects compiled with
# PLSCOPE_SETTINGS='IDENTIFIERS:ALL'. The refresh prerequisite recompiles
# VALID-but-missing-scope objects first; an empty result is still valid.
USER_IDENTIFIERS_QUERY = """
SELECT object_name, object_type, name, type, usage,
       usage_id, usage_context_id
FROM user_identifiers
""".strip()

USER_IDENTIFIERS_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE}
SELECT i.object_name, i.object_type, i.name, i.type, i.usage,
       i.usage_id, i.usage_context_id
FROM user_identifiers i
WHERE EXISTS (
    SELECT 1
    FROM object_names n
    WHERE i.object_name LIKE n.object_like
)
""".strip()

# PL/Scope SQL statements, shares the usage-id space with USER_IDENTIFIERS per
# object, so a column ref's context chain reaches its enclosing SELECT/UPDATE/...
USER_STATEMENTS_QUERY = """
SELECT object_name, object_type, type, usage_id, usage_context_id
FROM user_statements
""".strip()

USER_STATEMENTS_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE}
SELECT s.object_name, s.object_type, s.type, s.usage_id, s.usage_context_id
FROM user_statements s
WHERE EXISTS (
    SELECT 1
    FROM object_names n
    WHERE s.object_name LIKE n.object_like
)
""".strip()

# Store table name -> dictionary SELECT, in refresh order. The runner pulls each
# in turn for the connected schema and hands it to ``store.refresh_schema``.
USER_TABLE_QUERIES: dict[str, str] = {
    "USER_OBJECTS": USER_OBJECTS_QUERY,
    "USER_DEPENDENCIES": USER_DEPENDENCIES_QUERY,
    "USER_CONSTRAINTS": USER_CONSTRAINTS_QUERY,
    "USER_CONS_COLUMNS": USER_CONS_COLUMNS_QUERY,
    "USER_IDENTIFIERS": USER_IDENTIFIERS_QUERY,
    "USER_STATEMENTS": USER_STATEMENTS_QUERY,
}

USER_TABLE_SCOPED_QUERIES: dict[str, str] = {
    "USER_OBJECTS": USER_OBJECTS_SCOPED_QUERY,
    "USER_DEPENDENCIES": USER_DEPENDENCIES_SCOPED_QUERY,
    "USER_CONSTRAINTS": USER_CONSTRAINTS_SCOPED_QUERY,
    "USER_CONS_COLUMNS": USER_CONS_COLUMNS_SCOPED_QUERY,
    "USER_IDENTIFIERS": USER_IDENTIFIERS_SCOPED_QUERY,
    "USER_STATEMENTS": USER_STATEMENTS_SCOPED_QUERY,
}


# -------------------------------------------------------------------- APEX axis

# Re-scan an application's component sources so the APEX_USED_DB_OBJECT* views
# reflect the live definition before they are pulled. Runs once per app via
# ``gateway.execute`` (autocommits, fine for a PL/SQL call). Needs PL/Scope on
# the session, which the schema prerequisite already set.
APEX_SCAN_STATEMENT = """
BEGIN
    APEX_APP_OBJECT_DEPENDENCY.SCAN(p_application_id => :app_id);
END;
""".strip()

DEPSCAN_CLEANUP_STATEMENT = """
BEGIN
    FOR r IN (
        SELECT object_name
        FROM user_objects
        WHERE object_type = 'PROCEDURE'
        AND REGEXP_LIKE(object_name, '^DEPSCAN\\$[[:digit:]]+#[[:digit:]]+$')
    ) LOOP
        EXECUTE IMMEDIATE 'DROP PROCEDURE "' || REPLACE(r.object_name, '"', '""') || '"';
    END LOOP;
END;
""".strip()

# Columns are inferred from the APEX dictionary and verified live before commit.
APEX_USED_DB_OBJECTS_QUERY = """
SELECT workspace, application_id,
       used_db_object_id, used_db_object_owner, used_db_object_name
FROM apex_used_db_objects
WHERE application_id = :app_id
""".strip()

APEX_USED_DB_OBJECTS_24_2_QUERY = """
SELECT workspace, application_id,
       id AS used_db_object_id,
       referenced_owner AS used_db_object_owner,
       referenced_name AS used_db_object_name,
       referenced_type AS used_db_object_type
FROM apex_used_db_objects
WHERE application_id = :app_id
""".strip()

APEX_USED_DB_OBJECT_COMP_PROPS_QUERY = """
SELECT application_id, used_db_object_id, used_db_object_name,
       page_id, component_id, component_name, component_type,
       property_id, property_name, property_value
FROM apex_used_db_object_comp_props
WHERE application_id = :app_id
""".strip()

APEX_USED_DB_OBJECT_COMP_PROPS_24_2_QUERY = """
SELECT cp.application_id,
       dep.used_db_object_id,
       obj.referenced_name AS used_db_object_name,
       cp.page_id,
       NULL AS component_id,
       cp.component_display_name AS component_name,
       cp.component_type_name AS component_type,
       cp.id AS property_id,
       cp.property_name,
       cp.code_fragment AS property_value
FROM apex_used_db_object_comp_props cp
JOIN apex_used_db_obj_dependencies dep
  ON dep.application_id = cp.application_id
 AND dep.used_db_object_comp_prop_id = cp.id
LEFT JOIN apex_used_db_objects obj
  ON obj.application_id = dep.application_id
 AND obj.id = dep.used_db_object_id
WHERE cp.application_id = :app_id
""".strip()

APEX_USED_DB_OBJ_DEPENDENCIES_24_2_QUERY = """
SELECT dep.application_id,
       dep.used_db_object_id,
       obj.referenced_owner AS used_db_object_owner,
       obj.referenced_name AS used_db_object_name,
       obj.referenced_type AS used_db_object_type,
       dep.referenced_owner AS referenced_object_owner,
       dep.referenced_name AS referenced_object_name,
       dep.referenced_type AS referenced_object_type
FROM apex_used_db_obj_dependencies dep
LEFT JOIN apex_used_db_objects obj
  ON obj.application_id = dep.application_id
 AND obj.id = dep.used_db_object_id
WHERE dep.application_id = :app_id
""".strip()

APEX_USED_DB_OBJ_DEPENDENCIES_QUERY = """
SELECT application_id, used_db_object_id, used_db_object_owner,
       used_db_object_name, used_db_object_type, referenced_object_owner,
       referenced_object_name, referenced_object_type
FROM apex_used_db_obj_dependencies
WHERE application_id = :app_id
""".strip()

# Store table name -> dictionary SELECT, in refresh order; each filtered by
# ``:app_id`` and handed to ``store.refresh_app``.
APEX_TABLE_QUERIES: dict[str, str] = {
    "APEX_USED_DB_OBJECTS": APEX_USED_DB_OBJECTS_QUERY,
    "APEX_USED_DB_OBJECT_COMP_PROPS": APEX_USED_DB_OBJECT_COMP_PROPS_QUERY,
    "APEX_USED_DB_OBJ_DEPENDENCIES": APEX_USED_DB_OBJ_DEPENDENCIES_QUERY,
}


def apex_table_queries(apex_version: str | None = None) -> dict[str, str]:
    table_queries = dict(APEX_TABLE_QUERIES)
    if _uses_apex_24_2_used_objects_query(apex_version):
        table_queries["APEX_USED_DB_OBJECTS"] = APEX_USED_DB_OBJECTS_24_2_QUERY
        table_queries["APEX_USED_DB_OBJECT_COMP_PROPS"] = (
            APEX_USED_DB_OBJECT_COMP_PROPS_24_2_QUERY
        )
        table_queries["APEX_USED_DB_OBJ_DEPENDENCIES"] = (
            APEX_USED_DB_OBJ_DEPENDENCIES_24_2_QUERY
        )
    return table_queries


def supports_apex_used_views(apex_version: str | None) -> bool:
    parsed = _apex_version_tuple(apex_version)
    return not parsed or parsed >= (24, 2)


def _uses_apex_24_2_used_objects_query(apex_version: str | None) -> bool:
    parsed = _apex_version_tuple(apex_version)
    return bool(parsed) and (24, 2) <= parsed < (26, 1)


# Kept as a module-local alias: the parser itself now lives in shared/ so
# export_apex's 26.1 gates and these dependency gates cannot drift apart.
_apex_version_tuple = apex_version_tuple


# ------------------------------------------------------------------- PL/Scope

# Session prerequisite, turn full PL/Scope on so a subsequent recompile of
# missing-scope objects populates USER_IDENTIFIERS / USER_STATEMENTS. Issued by
# adt_ai.dependencies.plscope on the same connection (no new connection).
PLSCOPE_SESSION_STATEMENT = "ALTER SESSION SET PLSCOPE_SETTINGS = 'IDENTIFIERS:ALL,STATEMENTS:ALL'"
