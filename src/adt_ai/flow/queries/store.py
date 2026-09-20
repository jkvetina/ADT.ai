"""Local-SQLite statements for the flow store (``flow.db``).

Mirrors the ``dependencies/queries/objects.py`` ``META_*`` pattern: every
statement the store executes lives here, so ``flow/store.py`` holds behavior
and the SQL home stays the ``queries/`` package.

Version 1 (ADT #642) is the first the file carries. Before it the tables wore
an `apex_` prefix no other store used and the opener read the declared type of
one column to decide whether to rebuild; a file in that shape is a cache the
next `rebuild -app` refills, so it is dropped rather than migrated.

Version 2 (ADT #873) drops what nothing read back: the edge's `workspace`,
`target_app`, `working_copy_id` and `loaded_at`, the link source's
`description`, the application's `loaded_at`, and the workspace index.
`working_copy_id` was always zero, so the unique key it sat in names the same
edges without it.
"""

from __future__ import annotations

from adt_ai.shared.queries.sqlite_store import META_TABLE_DDL

STORE_SCHEMA = META_TABLE_DDL + """
CREATE TABLE IF NOT EXISTS applications (
    app_id     INTEGER PRIMARY KEY,
    workspace  TEXT NOT NULL,
    app_name   TEXT,
    app_alias  TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    app_id     INTEGER NOT NULL REFERENCES applications (app_id) ON DELETE CASCADE,
    page_id    INTEGER NOT NULL,
    page_name  TEXT,
    page_alias TEXT,
    PRIMARY KEY (app_id, page_id)
);

CREATE TABLE IF NOT EXISTS link_sources (
    src_type TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS edges (
    edge_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id        INTEGER NOT NULL REFERENCES applications (app_id) ON DELETE CASCADE,
    src_type      TEXT NOT NULL REFERENCES link_sources (src_type) ON DELETE CASCADE,
    src_page      INTEGER,
    component_id  TEXT,
    component     TEXT,
    raw_target    TEXT,
    target_app_id INTEGER,
    target_page   INTEGER,
    flag          TEXT NOT NULL CHECK (flag IN ('PAGE','CROSS_APP','DYNAMIC','OTHER','NONE'))
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_edges_component
    ON edges (app_id, src_type, component_id);
CREATE INDEX IF NOT EXISTS ix_edges_target
    ON edges (target_app_id, target_page, flag);
CREATE INDEX IF NOT EXISTS ix_edges_source
    ON edges (app_id, src_page);
"""

#: The pre-version shape, dropped on the way to version 1. Children first, so
#: the foreign keys the opener switched on have nothing to object to.
STORE_LIFT_LEGACY = """
BEGIN;
DROP TABLE IF EXISTS apex_nav_edge;
DROP TABLE IF EXISTS apex_page;
DROP TABLE IF EXISTS apex_app;
DROP TABLE IF EXISTS apex_link_source_type;
COMMIT;
"""

#: The dead columns version 2 drops, and the indexes that named them.
STORE_LIFT_1_EDGE_COLUMNS = ("workspace", "target_app", "working_copy_id", "loaded_at")
STORE_LIFT_1_EDGE_INDEXES = ("ux_edges_component", "ix_edges_workspace")

EDGE_COLUMNS = (
    "app_id, src_type, src_page, component_id, component, raw_target, "
    "target_app_id, target_page, flag"
)

STORE_SEED_LINK_SOURCE = "INSERT OR IGNORE INTO link_sources (src_type) VALUES (?)"


STORE_DELETE_APP = "DELETE FROM applications WHERE app_id = ?"

STORE_INSERT_APP = (
    "INSERT INTO applications (app_id, workspace, app_name, app_alias)"
    " VALUES (?, ?, ?, ?)"
)

STORE_INSERT_PAGE = (
    "INSERT INTO pages (app_id, page_id, page_name, page_alias)"
    " VALUES (?, ?, ?, ?)"
)

STORE_INSERT_EDGE = (
    f"INSERT INTO edges ({EDGE_COLUMNS})"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)

STORE_APP_IDS_QUERY = "SELECT app_id FROM applications ORDER BY app_id"

STORE_APP_EXISTS_QUERY = "SELECT 1 FROM applications WHERE app_id = ?"

STORE_APP_QUERY = (
    "SELECT app_id, workspace, app_name, app_alias FROM applications WHERE app_id = ?"
)

STORE_PAGES_QUERY = (
    "SELECT app_id, page_id, page_name, page_alias FROM pages"
    " WHERE app_id = ? ORDER BY page_id"
)

STORE_EDGES_QUERY = (
    "SELECT * FROM edges WHERE app_id = ?"
    " ORDER BY src_type, src_page, component_id"
)

STORE_INCOMING_QUERY = (
    "SELECT * FROM edges"
    " WHERE target_app_id = ? AND target_page = ?"
    "   AND flag IN ('PAGE','CROSS_APP')"
    " ORDER BY app_id, src_type, src_page"
)

STORE_OUTGOING_QUERY = (
    "SELECT * FROM edges"
    " WHERE app_id = ? AND src_page = ?"
    "   AND flag IN ('PAGE','CROSS_APP')"
    " ORDER BY target_app_id, target_page, src_type"
)
