"""Local-SQLite statements for the flow store (``flow.db``).

Mirrors the ``dependencies/queries/objects.py`` ``META_*`` pattern: every
statement the store executes lives here, so ``flow/store.py`` holds behavior
and the SQL home stays the ``queries/`` package.
"""

from __future__ import annotations

EDGE_COLUMNS = (
    "app_id, workspace, src_type, src_page, component_id, component, raw_target, "
    "target_app, target_app_id, target_page, flag, working_copy_id"
)

STORE_COMPONENT_ID_TYPE_QUERY = (
    "SELECT type FROM pragma_table_info('apex_nav_edge') WHERE name = 'component_id'"
)

STORE_SEED_LINK_SOURCE_TYPE = (
    "INSERT OR IGNORE INTO apex_link_source_type (src_type, description) VALUES (?, ?)"
)

STORE_DELETE_APP = "DELETE FROM apex_app WHERE app_id = ?"

STORE_INSERT_APP = (
    "INSERT INTO apex_app (app_id, workspace, app_name, app_alias, loaded_at)"
    " VALUES (?, ?, ?, ?, ?)"
)

STORE_INSERT_PAGE = (
    "INSERT INTO apex_page (app_id, page_id, page_name, page_alias)"
    " VALUES (?, ?, ?, ?)"
)

STORE_INSERT_EDGE = (
    f"INSERT INTO apex_nav_edge ({EDGE_COLUMNS}, loaded_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

STORE_APP_IDS_QUERY = "SELECT app_id FROM apex_app ORDER BY app_id"

STORE_APP_EXISTS_QUERY = "SELECT 1 FROM apex_app WHERE app_id = ?"

STORE_APP_QUERY = (
    "SELECT app_id, workspace, app_name, app_alias FROM apex_app WHERE app_id = ?"
)

STORE_PAGES_QUERY = (
    "SELECT app_id, page_id, page_name, page_alias FROM apex_page"
    " WHERE app_id = ? ORDER BY page_id"
)

STORE_EDGES_QUERY = (
    "SELECT * FROM apex_nav_edge WHERE app_id = ?"
    " ORDER BY src_type, src_page, component_id"
)

STORE_INCOMING_QUERY = (
    "SELECT * FROM apex_nav_edge"
    " WHERE target_app_id = ? AND target_page = ?"
    "   AND flag IN ('PAGE','CROSS_APP')"
    " ORDER BY app_id, src_type, src_page"
)

STORE_OUTGOING_QUERY = (
    "SELECT * FROM apex_nav_edge"
    " WHERE app_id = ? AND src_page = ?"
    "   AND flag IN ('PAGE','CROSS_APP')"
    " ORDER BY target_app_id, target_page, src_type"
)
