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
from adt_ai.shared.queries.scan_helpers import DROP_SCAN_HELPERS_STATEMENT

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
SELECT object_name, object_type, name, type,
       usage_id, usage_context_id
FROM user_identifiers
""".strip()

USER_IDENTIFIERS_SCOPED_QUERY = f"""
{_OBJECT_NAME_FILTER_CTE}
SELECT i.object_name, i.object_type, i.name, i.type,
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
#
# **The CLEAR_CACHE is not optional and never was** (ADT #751, measured). Every
# scan re-inserts the application-level component rows, and the dictionary holds
# a unique key over `(component_type_id, flow_id, component_id, property_id)`,
# so a scan of an application that already carries rows collides with itself. On
# SANDBOX (APEX 26.1, 2026-09-09) that surfaced as `ORA-00001` from the
# page-scoped call and as `ORA-00600 [updolev_21]` -- which also drops the
# session -- from the application-wide one. Both callers were therefore
# reporting `FAILED` on every run after the first against a given application,
# and that second run is the ordinary case rather than an edge: the scan itself
# is what left the rows behind.
#
# The clear is scoped to the application because `CLEAR_CACHE` takes no page.
# That is the whole reason a page-scoped scan cannot accumulate: clearing for
# page 101 takes page 100's rows with it, so `-page 100 101` reads each page
# back before scanning the next rather than scanning both and reading once.
APEX_SCAN_STATEMENT = """
BEGIN
    APEX_APP_OBJECT_DEPENDENCY.CLEAR_CACHE(p_application_id => :app_id);
    APEX_APP_OBJECT_DEPENDENCY.SCAN(p_application_id => :app_id);
END;
""".strip()

# The same call narrowed to one page (ADT #751). `p_page_id` is a defaulted
# parameter of the public `APEX_APP_OBJECT_DEPENDENCY.SCAN` synonym, read off
# `all_arguments` on the 26.1 container rather than inferred: the scan genuinely
# compiles one page's fragments instead of the whole application's, which is what
# makes changing one page cheap. Measured on a 42-page application, 1.8s against
# 4.0s for the whole of it, and the gap widens with the page count because the
# application-level components are the fixed part of either run.
APEX_SCAN_PAGE_STATEMENT = """
BEGIN
    APEX_APP_OBJECT_DEPENDENCY.CLEAR_CACHE(p_application_id => :app_id);
    APEX_APP_OBJECT_DEPENDENCY.SCAN(p_application_id => :app_id, p_page_id => :page_id);
END;
""".strip()

# The helper pattern and its drop live in `shared/scan_helpers.py` (ADT #888),
# which `export_db` and `recompile` read too; the name stays for this module's callers.
DEPSCAN_CLEANUP_STATEMENT = DROP_SCAN_HELPERS_STATEMENT

# The repair that was measured and REJECTED (ADT #921), kept because
# `tests/tools/scan_session_reset_probe.py` is what measured it and a candidate
# with no name is one the next card re-proposes. A page whose data breaks
# `APEX_APP_OBJECT_DEPENDENCY.SCAN` leaves the session unable to scan the next
# page, and a rollback clears only half of that: measured live, it cleared the
# derivative `ORA-06510` crash and left the genuine `ORA-01427` one standing,
# so a healthy page was still named for its neighbour's defect. A rollback cannot
# reach PL/SQL package state. `component_scan.reset_session` opens a new session
# instead.
SESSION_ROLLBACK_STATEMENT = "ROLLBACK"

# What the scan above concluded about each fragment it could NOT compile, which
# is the half `APEX_USED_DB_OBJECTS` cannot report: a fragment that fails to
# parse resolves to no object at all, so it leaves no dependency row and
# disappears from every other APEX_USED_* read. ADT #676 deploys against this.
#
# `ERROR_MESSAGE` arrived with the 24.2 view shape, so the caller gates on
# `supports_apex_used_views` exactly as the dependency reads do. Ordered so a
# diff between two scans of the same application is stable.
APEX_COMPONENT_ERRORS_QUERY = """
SELECT page_id,
       component_type_name AS component_type,
       component_display_name AS component_name,
       property_name,
       error_message
FROM apex_used_db_object_comp_props
WHERE application_id = :app_id
  AND error_message IS NOT NULL
ORDER BY page_id, component_display_name, property_name
""".strip()

# The denominator beside it: how many fragments the scan actually looked at. A
# zero here is a scan that did not run, which reads very differently from a scan
# that ran and found nothing, and the log has to be able to tell them apart.
APEX_COMPONENT_SCAN_COUNT_QUERY = """
SELECT COUNT(*) AS analyzed
FROM apex_used_db_object_comp_props
WHERE application_id = :app_id
""".strip()

# What settles a zero above (ADT #701). A scan that analyzed nothing is either an
# application with nothing to analyze or a verification that did not happen, and
# the count alone cannot tell those apart, so the second reading is the default
# and this is the read that can overturn it. `apex_application_pages` is written
# by the import rather than by the scan, so a zero here is evidence from outside
# the thing being questioned: an application holding no page holds no component,
# and zero analyzed fragments is then the whole of its scope.
APEX_APPLICATION_PAGE_COUNT_QUERY = """
SELECT COUNT(*) AS pages
FROM apex_application_pages
WHERE application_id = :app_id
""".strip()

# The page-scoped halves of the three reads above (ADT #751). A page-scoped scan
# leaves the application's rows holding that page plus the application-level
# components it re-compiles every time, and those carry no page at all -- so
# reading the whole application back would answer about components the caller
# did not name. `page_id = :page_id` is what keeps the answer to the question.
APEX_COMPONENT_ERRORS_PAGE_QUERY = """
SELECT page_id,
       component_type_name AS component_type,
       component_display_name AS component_name,
       property_name,
       error_message
FROM apex_used_db_object_comp_props
WHERE application_id = :app_id
  AND page_id = :page_id
  AND error_message IS NOT NULL
ORDER BY component_display_name, property_name
""".strip()

APEX_COMPONENT_SCAN_COUNT_PAGE_QUERY = """
SELECT COUNT(*) AS analyzed
FROM apex_used_db_object_comp_props
WHERE application_id = :app_id
  AND page_id = :page_id
""".strip()

# What settles a page-scoped zero, the way the page count settles an
# application-wide one. The distinction it draws is the one a reader cares about:
# a page that holds no compilable fragment is a real answer, and a page the
# application does not hold at all is a question about nothing. APEX itself does
# not separate them -- `SCAN` on a page id no application carries returns
# quietly, measured on SANDBOX -- so nothing but this read can.
APEX_APPLICATION_PAGE_EXISTS_QUERY = """
SELECT COUNT(*) AS pages
FROM apex_application_pages
WHERE application_id = :app_id
  AND page_id = :page_id
""".strip()

# Which pages an application actually holds, for resolving a `-page` RANGE into
# the concrete ids a page-scoped scan needs. An explicit `-page 100` is scanned
# as given -- a page that turns out not to exist is a finding the caller wants,
# not one to swallow -- but `-page 1-50` cannot be turned into fifty scans of
# pages that were never there.
#
# The name rides along because the page-by-page fallback walks this same list
# and the warning it prints names each broken page (ADT #929). It is the one
# read that already has every page of the application in hand, so the name costs
# a column rather than a second query; a caller wanting ids alone ignores it.
APEX_APPLICATION_PAGE_IDS_QUERY = """
SELECT page_id, page_name
FROM apex_application_pages
WHERE application_id = :app_id
ORDER BY page_id
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

# Store table name -> dictionary SELECT, in refresh order; each filtered by
# ``:app_id`` and handed to ``store.refresh_app``.
APEX_TABLE_QUERIES: dict[str, str] = {
    "APEX_USED_DB_OBJECTS": APEX_USED_DB_OBJECTS_QUERY,
    "APEX_USED_DB_OBJECT_COMP_PROPS": APEX_USED_DB_OBJECT_COMP_PROPS_QUERY,
}


def apex_table_queries(apex_version: str | None = None) -> dict[str, str]:
    table_queries = dict(APEX_TABLE_QUERIES)
    if _uses_apex_24_2_used_objects_query(apex_version):
        table_queries["APEX_USED_DB_OBJECTS"] = APEX_USED_DB_OBJECTS_24_2_QUERY
        table_queries["APEX_USED_DB_OBJECT_COMP_PROPS"] = (
            APEX_USED_DB_OBJECT_COMP_PROPS_24_2_QUERY
        )
    return table_queries


def supports_apex_used_views(apex_version: str | None) -> bool:
    parsed = _apex_version_tuple(apex_version)
    return not parsed or parsed >= (24, 2)


def _uses_apex_24_2_used_objects_query(apex_version: str | None) -> bool:
    """Whether this release carries the reshaped ``APEX_USED_*`` views.

    24.2 renamed the columns (`used_db_object_name` -> `referenced_name`, and so
    on) and every release since has kept the new shape, so this is a floor with
    no ceiling. It carried one until ADT #676: `< (26, 1)` was added from the
    26.1 release notes rather than from the dictionary, and 26.1 fell back to the
    pre-24.2 query and died on `ORA-00904: "USED_DB_OBJECT_NAME"` -- which is
    exactly the error a version gate written against a real instance cannot
    make. Measured on APEX 26.1: `APEX_USED_DB_OBJECTS` carries `id`,
    `referenced_owner`, `referenced_name`, `referenced_type` and carries no
    `used_db_object_name` at all, i.e. the 24.2 shape.
    """
    parsed = _apex_version_tuple(apex_version)
    return bool(parsed) and parsed >= (24, 2)


# Kept as a module-local alias: the parser itself now lives in shared/ so
# export_apex's 26.1 gates and these dependency gates cannot drift apart.
_apex_version_tuple = apex_version_tuple


# ------------------------------------------------------------------- PL/Scope

# Session prerequisite, turn full PL/Scope on so a subsequent recompile of
# missing-scope objects populates USER_IDENTIFIERS / USER_STATEMENTS. Issued by
# adt_ai.dependencies.plscope on the same connection (no new connection).
PLSCOPE_SESSION_STATEMENT = "ALTER SESSION SET PLSCOPE_SETTINGS = 'IDENTIFIERS:ALL,STATEMENTS:ALL'"
