"""Raw Oracle dictionary reads for the ``dependencies`` raw-mirror database.

Each ``USER_*`` SELECT pulls one dictionary view verbatim for the *connected*
schema — the views scope implicitly to the session user, so there is no bind and
no ``OWNER`` column; the runner stamps ``OWNER`` per scope when it writes the row
(see :meth:`adt_ai.dependencies.store.DependencyStore.refresh_schema`). The
``APEX_*`` SELECTs are filtered by ``:app_id`` and the runner stamps
``APPLICATION_ID``. Projections include only columns consumed by query modes or
generated artifacts; the store drops any column it does not know and NULL-fills
any it expects but does not receive, so the projection and the table schema
cannot silently drift.
"""

from __future__ import annotations

# ------------------------------------------------------------------- USER_* axis

USER_OBJECTS_QUERY = """
SELECT object_name, object_type, last_ddl_time
FROM user_objects
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
WHERE EXISTS (
    SELECT 1
    FROM object_names n
    WHERE o.object_name LIKE n.object_like
)
""".strip()

USER_DEPENDENCIES_QUERY = """
SELECT name, type, referenced_owner, referenced_name, referenced_type
FROM user_dependencies
""".strip()

USER_DEPENDENCIES_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE}
SELECT d.name, d.type, d.referenced_owner, d.referenced_name, d.referenced_type
FROM user_dependencies d
WHERE EXISTS (
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

# PL/Scope identifier usages — populated only for objects compiled with
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

# PL/Scope SQL statements — shares the usage-id space with USER_IDENTIFIERS per
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
SELECT workspace, application_id, application_name,
       used_db_object_id, used_db_object_owner, used_db_object_name
FROM apex_used_db_objects
WHERE application_id = :app_id
""".strip()

APEX_USED_DB_OBJECTS_24_2_QUERY = """
SELECT workspace, application_id, application_name,
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


def _apex_version_tuple(apex_version: str | None) -> tuple[int, ...]:
    if not apex_version:
        return ()
    parts: list[int] = []
    for raw in str(apex_version).split(".")[:2]:
        digits = ""
        for char in raw:
            if char.isdigit():
                digits += char
            elif digits:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


# ------------------------------------------------------------- SQLite mirror reads

CONSTRAINT_COLUMNS = (
    "OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME, "
    "R_OWNER, R_CONSTRAINT_NAME"
)

FK_CONSTRAINT_BY_NAME_QUERY = f"""
SELECT {CONSTRAINT_COLUMNS}
FROM USER_CONSTRAINTS
WHERE UPPER(CONSTRAINT_NAME) = UPPER(?)
ORDER BY OWNER, TABLE_NAME, CONSTRAINT_NAME
LIMIT 1
""".strip()

FK_CONSTRAINT_BY_KEY_QUERY = f"""
SELECT {CONSTRAINT_COLUMNS}
FROM USER_CONSTRAINTS
WHERE OWNER = ?
  AND CONSTRAINT_NAME = ?
""".strip()

FK_TABLE_FOREIGN_KEYS_QUERY = f"""
SELECT {CONSTRAINT_COLUMNS}
FROM USER_CONSTRAINTS
WHERE OWNER = ?
  AND TABLE_NAME = ?
  AND CONSTRAINT_TYPE = 'R'
ORDER BY CONSTRAINT_NAME
""".strip()

FK_TABLE_KEY_CONSTRAINTS_QUERY = f"""
SELECT {CONSTRAINT_COLUMNS}
FROM USER_CONSTRAINTS
WHERE OWNER = ?
  AND TABLE_NAME = ?
  AND CONSTRAINT_TYPE IN ('P', 'U')
ORDER BY CONSTRAINT_TYPE, CONSTRAINT_NAME
""".strip()

FK_REFERENCING_FOREIGN_KEYS_QUERY = f"""
SELECT {CONSTRAINT_COLUMNS}
FROM USER_CONSTRAINTS
WHERE R_OWNER = ?
  AND R_CONSTRAINT_NAME = ?
  AND CONSTRAINT_TYPE = 'R'
ORDER BY TABLE_NAME, CONSTRAINT_NAME
""".strip()

FK_CONSTRAINT_COLUMN_NAMES_QUERY = """
SELECT COLUMN_NAME
FROM USER_CONS_COLUMNS
WHERE OWNER = ?
  AND CONSTRAINT_NAME = ?
ORDER BY POSITION
""".strip()

APEX_PAGE_COMPONENTS_QUERY_TEMPLATE = """
SELECT MIN(PAGE_ID) AS page_id,
       COMPONENT_TYPE AS component_type,
       COMPONENT_NAME AS component_name
FROM APEX_USED_DB_OBJECT_COMP_PROPS
WHERE APPLICATION_ID = ?
  AND PAGE_ID IS NOT NULL
  AND COMPONENT_TYPE IS NOT NULL
  AND TRIM(COMPONENT_TYPE) <> ''
  AND COMPONENT_NAME IS NOT NULL
  AND TRIM(COMPONENT_NAME) <> ''
  AND ({page_filter})
GROUP BY COMPONENT_TYPE, COMPONENT_NAME
ORDER BY MIN(PAGE_ID), UPPER(COMPONENT_TYPE), UPPER(COMPONENT_NAME)
""".strip()


def apex_page_components_query(page_filter: str) -> str:
    return APEX_PAGE_COMPONENTS_QUERY_TEMPLATE.format(page_filter=page_filter)


APEX_PAGE_DB_OBJECTS_QUERY_TEMPLATE = """
SELECT MIN(p.PAGE_ID) AS page_id,
       COALESCE(o.USED_DB_OBJECT_OWNER, '') AS object_owner,
       COALESCE(o.USED_DB_OBJECT_TYPE, '') AS object_type,
       p.USED_DB_OBJECT_NAME AS object_name
FROM APEX_USED_DB_OBJECT_COMP_PROPS p
LEFT JOIN APEX_USED_DB_OBJECTS o
  ON o.APPLICATION_ID = p.APPLICATION_ID
 AND o.USED_DB_OBJECT_ID = p.USED_DB_OBJECT_ID
WHERE p.APPLICATION_ID = ?
  AND p.PAGE_ID IS NOT NULL
  AND p.USED_DB_OBJECT_NAME IS NOT NULL
  AND TRIM(p.USED_DB_OBJECT_NAME) <> ''
  AND ({page_filter})
GROUP BY object_owner, object_type, p.USED_DB_OBJECT_NAME
ORDER BY MIN(p.PAGE_ID), UPPER(object_owner), UPPER(object_type), UPPER(p.USED_DB_OBJECT_NAME)
""".strip()


def apex_page_db_objects_query(page_filter: str) -> str:
    return APEX_PAGE_DB_OBJECTS_QUERY_TEMPLATE.format(page_filter=page_filter)


def tracked_owner_predicate(owner_sql: str) -> str:
    return (
        "(EXISTS (SELECT 1 FROM USER_OBJECTS tracked_object_owner "
        f"WHERE tracked_object_owner.OWNER = {owner_sql}) "
        "OR EXISTS (SELECT 1 FROM USER_DEPENDENCIES tracked_dependency_owner "
        f"WHERE tracked_dependency_owner.OWNER = {owner_sql}))"
    )


DEPENDENCY_USES_QUERY = f"""
SELECT DISTINCT d.REFERENCED_TYPE AS t, d.REFERENCED_NAME AS n
FROM USER_DEPENDENCIES d
WHERE d.TYPE = ? AND d.NAME = ?
  AND d.REFERENCED_NAME IS NOT NULL
  AND {tracked_owner_predicate("d.REFERENCED_OWNER")}
""".strip()

DEPENDENCY_USED_BY_QUERY = f"""
SELECT DISTINCT d.TYPE AS t, d.NAME AS n
FROM USER_DEPENDENCIES d
WHERE d.REFERENCED_TYPE = ? AND d.REFERENCED_NAME = ?
  AND {tracked_owner_predicate("d.OWNER")}
""".strip()

DEPENDENCY_IMPACT_QUERY = f"""
WITH RECURSIVE imp(t, n, depth) AS (
    SELECT ?, ?, 0
    UNION
    SELECT d.TYPE, d.NAME, imp.depth + 1
    FROM USER_DEPENDENCIES d
    JOIN imp ON d.REFERENCED_TYPE = imp.t AND d.REFERENCED_NAME = imp.n
    WHERE imp.depth < ?
      AND {tracked_owner_predicate("d.OWNER")}
)
SELECT t, n, MIN(depth) AS d
FROM imp
WHERE NOT (t = ? AND n = ?)
GROUP BY t, n
""".strip()

UNUSED_OBJECTS_QUERY = """
SELECT o.OBJECT_TYPE AS t, o.OBJECT_NAME AS n
FROM USER_OBJECTS o
WHERE 1 = 1
  AND NOT EXISTS (
      SELECT 1 FROM USER_DEPENDENCIES d
      WHERE d.REFERENCED_OWNER = o.OWNER
        AND d.REFERENCED_TYPE = o.OBJECT_TYPE
        AND d.REFERENCED_NAME = o.OBJECT_NAME
  )
  AND NOT EXISTS (
      SELECT 1 FROM APEX_USED_DB_OBJECTS a
      WHERE a.USED_DB_OBJECT_OWNER = o.OWNER
        AND a.USED_DB_OBJECT_NAME = o.OBJECT_NAME
        AND (
            a.USED_DB_OBJECT_TYPE = o.OBJECT_TYPE
            OR a.USED_DB_OBJECT_TYPE IS NULL
            OR a.USED_DB_OBJECT_TYPE = ''
        )
  )
""".strip()
UNUSED_OBJECTS_TYPE_FILTER_CLAUSE = " AND o.OBJECT_TYPE = ?"

CONSTRAINT_COLUMNS_QUERY = """
SELECT OWNER, CONSTRAINT_NAME, COLUMN_NAME
FROM USER_CONS_COLUMNS
ORDER BY OWNER, CONSTRAINT_NAME, POSITION
""".strip()

CONSTRAINT_TABLES_QUERY = """
SELECT OWNER, CONSTRAINT_NAME, TABLE_NAME
FROM USER_CONSTRAINTS
""".strip()

CONSTRAINTS_QUERY = """
SELECT OWNER, CONSTRAINT_NAME, CONSTRAINT_TYPE, TABLE_NAME, R_OWNER, R_CONSTRAINT_NAME
FROM USER_CONSTRAINTS
WHERE CONSTRAINT_TYPE IN ('P', 'U', 'R')
""".strip()
CONSTRAINTS_TABLE_FILTER_CLAUSE = " AND TABLE_NAME = ?"
CONSTRAINTS_ORDER_CLAUSE = " ORDER BY TABLE_NAME, CONSTRAINT_TYPE, CONSTRAINT_NAME"

USER_IDENTIFIERS_ALL_QUERY = "SELECT * FROM USER_IDENTIFIERS"
USER_STATEMENTS_ALL_QUERY = "SELECT * FROM USER_STATEMENTS"

APEX_CALLERS_QUERY = """
SELECT o.WORKSPACE,
       o.APPLICATION_ID,
       o.APPLICATION_NAME,
       o.USED_DB_OBJECT_OWNER,
       o.USED_DB_OBJECT_TYPE,
       o.USED_DB_OBJECT_NAME,
       p.PAGE_ID,
       p.COMPONENT_ID,
       p.COMPONENT_NAME,
       p.COMPONENT_TYPE,
       p.PROPERTY_NAME,
       p.PROPERTY_VALUE
FROM APEX_USED_DB_OBJECTS o
LEFT JOIN APEX_USED_DB_OBJECT_COMP_PROPS p
  ON p.APPLICATION_ID = o.APPLICATION_ID
 AND p.USED_DB_OBJECT_ID = o.USED_DB_OBJECT_ID
ORDER BY o.APPLICATION_ID,
         COALESCE(p.PAGE_ID, -1),
         COALESCE(p.COMPONENT_ID, -1),
         COALESCE(p.PROPERTY_ID, -1),
         o.USED_DB_OBJECT_TYPE,
         o.USED_DB_OBJECT_NAME
""".strip()

DEPENDENCY_ALIAS_OBJECTS_QUERY = """
SELECT o.OBJECT_TYPE AS t, o.OBJECT_NAME AS n
FROM USER_OBJECTS o
""".strip()

USES_EDGES_QUERY = f"""
SELECT DISTINCT d.TYPE AS t, d.NAME AS n,
       d.REFERENCED_TYPE AS rt, d.REFERENCED_NAME AS rn
FROM USER_DEPENDENCIES d
WHERE d.REFERENCED_NAME IS NOT NULL
  AND {tracked_owner_predicate("d.REFERENCED_OWNER")}
""".strip()

USER_OBJECTS_BY_OWNER_QUERY = "SELECT * FROM USER_OBJECTS WHERE OWNER = ?"


def delete_owner_rows_query(table: str) -> str:
    return f"DELETE FROM {table} WHERE OWNER = ?"


def delete_app_rows_query(table: str) -> str:
    return f"DELETE FROM {table} WHERE APPLICATION_ID = ?"


def select_app_rows_query(table: str) -> str:
    return f"SELECT * FROM {table} WHERE APPLICATION_ID = ?"


def like_clause(column: str, count: int) -> str:
    return " OR ".join(f"{column} LIKE ?" for _ in range(count))


def delete_like_query(table: str, column: str, count: int) -> str:
    return f"DELETE FROM {table} WHERE OWNER = ? AND ({like_clause(column, count)})"


def delete_named_dependency_scope_query(count: int) -> str:
    dependency_clause = (
        f"(OWNER = ? AND ({like_clause('NAME', count)})) "
        f"OR (REFERENCED_OWNER = ? AND ({like_clause('REFERENCED_NAME', count)}))"
    )
    return f"DELETE FROM USER_DEPENDENCIES WHERE {dependency_clause}"


def matching_table_constraints_query(count: int) -> str:
    return f"""
SELECT CONSTRAINT_NAME
FROM USER_CONSTRAINTS
WHERE OWNER = ? AND ({like_clause('TABLE_NAME', count)})
""".strip()

TABLE_CONSTRAINTS_QUERY = """
SELECT CONSTRAINT_NAME
FROM USER_CONSTRAINTS
WHERE OWNER = ? AND TABLE_NAME = ?
""".strip()


def referenced_constraints_query(count: int) -> str:
    placeholders = ",".join("?" for _ in range(count))
    return f"""
SELECT CONSTRAINT_NAME
FROM USER_CONSTRAINTS
WHERE OWNER = ?
  AND R_OWNER = ?
  AND R_CONSTRAINT_NAME IN ({placeholders})
""".strip()


# ------------------------------------------------------------------- PL/Scope

# Session prerequisite — turn full PL/Scope on so a subsequent recompile of
# missing-scope objects populates USER_IDENTIFIERS / USER_STATEMENTS. Issued by
# adt_ai.dependencies.plscope on the same connection (no new connection).
PLSCOPE_SESSION_STATEMENT = "ALTER SESSION SET PLSCOPE_SETTINGS = 'IDENTIFIERS:ALL,STATEMENTS:ALL'"
