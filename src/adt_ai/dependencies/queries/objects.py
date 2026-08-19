"""SQLite-mirror reads for the ``dependencies`` raw-mirror database.

The offline query layer: every SELECT here runs against the committed SQLite raw
mirror (the ``USER_*`` / ``APEX_*`` tables stamped per scope during refresh), so
query modes (``-from`` / ``-to`` / ``-impact`` / ``-tree``)
need no live DB connection. The Oracle dictionary-read SELECTs that *populate*
that mirror are the sibling topic module
:mod:`adt_ai.dependencies.queries.dictionary_reads`; both are re-exported by the
package ``__init__``, so the refresh runner's ``queries.<NAME>`` access keeps
resolving.
"""

from __future__ import annotations

# ------------------------------------------------------------- schema meta queries

META_TABLE_EXISTS_QUERY = (
    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_meta'"
)

META_SCHEMA_VERSION_QUERY = "SELECT value FROM _meta WHERE key = 'schema_version'"

META_SCHEMA_VERSION_NULL_QUERY = "SELECT NULL as value WHERE 0"

META_UPSERT_SCHEMA_VERSION = (
    "INSERT OR REPLACE INTO _meta (key, value) VALUES ('schema_version', ?)"
)

# Per-scope last-refresh stamps share the same key/value ``_meta`` table under a
# ``last_refresh:<kind>:<name>`` key, so refresh can record, and ``-age`` can
# read, staleness offline without a dedicated table or a SCHEMA_VERSION bump.
META_LAST_REFRESH_PREFIX = "last_refresh:"

META_UPSERT_QUERY = "INSERT OR REPLACE INTO _meta (key, value) VALUES (?, ?)"

META_LAST_REFRESH_QUERY = (
    "SELECT key, value FROM _meta WHERE key LIKE 'last_refresh:%' ORDER BY key"
)

# The refreshed scope's DATABASE UTC offset, stored beside its last-refresh
# stamp under the same key/value `_meta` table and the same `<kind>:<name>`
# key shape. `patch -create` resolves the mirrored LAST_DDL_TIME through it, so
# the comparison against a repo file's mtime is made on one clock (ADT #394).
# A scope with no row here was mirrored before that fix and cannot answer the
# question, which the gate reports rather than guessing at.
META_DB_OFFSET_PREFIX = "db_utc_offset:"

META_DB_OFFSET_QUERY = (
    "SELECT key, value FROM _meta WHERE key LIKE 'db_utc_offset:%' ORDER BY key"
)

# The dictionary's own LAST_DDL_TIME per object, mirrored on every
# `dependencies -refresh`. `patch -create` reads it to prove the exported files
# still match the schema, so a stale `export_db` cannot ship a previous package
# body (ADT #261), and reads it OFFLINE, because the mirror already carries it.
LAST_DDL_TIMES_QUERY = (
    "SELECT OWNER, OBJECT_TYPE, OBJECT_NAME, LAST_DDL_TIME FROM USER_OBJECTS"
)

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


def owner_in_clause(column: str, count: int) -> str:
    """A parameterized ``<column> IN (?, ?, ...)`` fragment for the query-mode
    ``-schema`` owner filter. Caller supplies ``count`` uppercased owner params."""
    placeholders = ", ".join("?" for _ in range(count))
    return f"{column} IN ({placeholders})"


DEPENDENCY_USES_QUERY = f"""
SELECT DISTINCT d.REFERENCED_TYPE AS t, d.REFERENCED_NAME AS n
FROM USER_DEPENDENCIES d
WHERE d.TYPE = ? AND d.NAME = ?
  AND d.REFERENCED_NAME IS NOT NULL
  AND {tracked_owner_predicate("d.REFERENCED_OWNER")}
""".strip()


def dependency_uses_query(owner_count: int = 0) -> str:
    """Forward-dependency query; ``-schema`` narrows the dependent side (d.OWNER)."""
    sql = DEPENDENCY_USES_QUERY
    if owner_count:
        sql += f"\n  AND {owner_in_clause('d.OWNER', owner_count)}"
    return sql


DEPENDENCY_USED_BY_QUERY = f"""
SELECT DISTINCT d.TYPE AS t, d.NAME AS n
FROM USER_DEPENDENCIES d
WHERE d.REFERENCED_TYPE = ? AND d.REFERENCED_NAME = ?
  AND {tracked_owner_predicate("d.OWNER")}
""".strip()


def dependency_used_by_query(owner_count: int = 0) -> str:
    """Reverse-dependency query; ``-schema`` narrows the referenced side
    (d.REFERENCED_OWNER), the owner of the queried object."""
    sql = DEPENDENCY_USED_BY_QUERY
    if owner_count:
        sql += f"\n  AND {owner_in_clause('d.REFERENCED_OWNER', owner_count)}"
    return sql


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


def dependency_impact_query(owner_count: int = 0) -> str:
    """Transitive reverse-closure query. ``-schema`` constrains only the *seed*
    (the first hop off the queried object, REFERENCED_OWNER) and leaves the
    transitive walk unconstrained, so it disambiguates which owner's object is
    the impact root without truncating its downstream reach."""
    if not owner_count:
        return DEPENDENCY_IMPACT_QUERY
    seed_owner_filter = (
        f"\n      AND (imp.depth > 0 OR {owner_in_clause('d.REFERENCED_OWNER', owner_count)})"
    )
    return f"""
WITH RECURSIVE imp(t, n, depth) AS (
    SELECT ?, ?, 0
    UNION
    SELECT d.TYPE, d.NAME, imp.depth + 1
    FROM USER_DEPENDENCIES d
    JOIN imp ON d.REFERENCED_TYPE = imp.t AND d.REFERENCED_NAME = imp.n
    WHERE imp.depth < ?{seed_owner_filter}
      AND {tracked_owner_predicate("d.OWNER")}
)
SELECT t, n, MIN(depth) AS d
FROM imp
WHERE NOT (t = ? AND n = ?)
GROUP BY t, n
""".strip()

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

USES_EDGES_QUERY = f"""
SELECT DISTINCT d.TYPE AS t, d.NAME AS n,
       d.REFERENCED_TYPE AS rt, d.REFERENCED_NAME AS rn
FROM USER_DEPENDENCIES d
WHERE d.REFERENCED_NAME IS NOT NULL
  AND {tracked_owner_predicate("d.REFERENCED_OWNER")}
""".strip()

# Foreign keys are the table-to-table half of the dependency graph, and
# USER_DEPENDENCIES does not carry it, Oracle records only PL/SQL and view
# dependencies there. A child table must be created after the table its FK
# references, so the edge set is reconstructed from the constraint mirror:
# resolve R_CONSTRAINT_NAME to the parent's P/U constraint, same owner only,
# self-references excluded (a hierarchy FK is not an ordering constraint).
# Ported from old ADT's `object_dependencies` UNION branch; its
# `status = 'ENABLED'` filter is dropped because the mirror carries no STATUS
# column, which only ever orders more conservatively.
FOREIGN_KEY_EDGES_QUERY = """
SELECT DISTINCT c.TABLE_NAME AS child, r.TABLE_NAME AS parent
FROM USER_CONSTRAINTS c
JOIN USER_CONSTRAINTS r
  ON r.CONSTRAINT_NAME = c.R_CONSTRAINT_NAME
 AND r.OWNER = c.OWNER
WHERE c.CONSTRAINT_TYPE = 'R'
  AND c.OWNER = c.R_OWNER
  AND c.TABLE_NAME != r.TABLE_NAME
""".strip()

USER_OBJECTS_BY_OWNER_QUERY = "SELECT * FROM USER_OBJECTS WHERE OWNER = ?"


def resolve_object_types_query(owner_count: int = 0) -> str:
    """Return distinct OBJECT_TYPEs for a bare object name, optionally filtered by owner."""
    sql = "SELECT DISTINCT OBJECT_TYPE FROM USER_OBJECTS WHERE OBJECT_NAME = ?"
    if owner_count:
        sql += f" AND {owner_in_clause('OWNER', owner_count)}"
    return sql


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


DELETE_STALE_EXTERNAL_DEPS_QUERY = (
    "DELETE FROM USER_DEPENDENCIES WHERE OWNER = ? "
    "AND REFERENCED_OWNER IS NOT NULL "
    "AND REFERENCED_OWNER NOT IN (SELECT DISTINCT OWNER FROM USER_OBJECTS)"
)


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
