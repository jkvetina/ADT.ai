from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from adt_ai.flow.model import FlowApp, FlowEdge, FlowPage

# Persistent store: the .db IS the source of truth (populated from live Oracle on
# refresh), so the schema is created with IF NOT EXISTS and never dropped.
SCHEMA = """
CREATE TABLE IF NOT EXISTS apex_app (
    app_id     INTEGER PRIMARY KEY,
    workspace  TEXT NOT NULL,
    app_name   TEXT,
    app_alias  TEXT,
    loaded_at  TEXT
);

CREATE TABLE IF NOT EXISTS apex_page (
    app_id     INTEGER NOT NULL REFERENCES apex_app(app_id) ON DELETE CASCADE,
    page_id    INTEGER NOT NULL,
    page_name  TEXT,
    page_alias TEXT,
    PRIMARY KEY (app_id, page_id)
);

CREATE TABLE IF NOT EXISTS apex_link_source_type (
    src_type    TEXT PRIMARY KEY,
    description TEXT
);

CREATE TABLE IF NOT EXISTS apex_nav_edge (
    edge_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace       TEXT NOT NULL,
    app_id          INTEGER NOT NULL REFERENCES apex_app(app_id) ON DELETE CASCADE,
    src_type        TEXT NOT NULL REFERENCES apex_link_source_type(src_type),
    src_page        INTEGER,
    component_id    TEXT,
    component       TEXT,
    raw_target      TEXT,
    target_app      TEXT,
    target_app_id   INTEGER,
    target_page     INTEGER,
    flag            TEXT NOT NULL CHECK (flag IN ('PAGE','CROSS_APP','DYNAMIC','OTHER','NONE')),
    working_copy_id INTEGER NOT NULL DEFAULT 0,
    loaded_at       TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_nav_edge
    ON apex_nav_edge(app_id, src_type, component_id, working_copy_id);
CREATE INDEX IF NOT EXISTS ix_nav_edge_in
    ON apex_nav_edge(target_app_id, target_page, flag);
CREATE INDEX IF NOT EXISTS ix_nav_edge_out
    ON apex_nav_edge(app_id, src_page);
CREATE INDEX IF NOT EXISTS ix_nav_edge_ws
    ON apex_nav_edge(workspace, app_id);
"""

# Seeded catalog of the 12 statically resolvable link-source views.
LINK_SOURCE_TYPES: tuple[tuple[str, str], ...] = (
    ("BRANCH", "Page branch (Branch to Page)"),
    ("BUTTON", "Button redirect to page / app"),
    ("TAB", "Standard tab target page"),
    ("PARENT_TAB", "Parent tab target"),
    ("LIST_ENTRY", "List / navigation menu entry"),
    ("BREADCRUMB", "Breadcrumb entry"),
    ("NAV_BAR", "Navigation bar entry"),
    ("IR_COL_LINK", "Interactive report column link"),
    ("RPT_COL_LINK", "Classic report column link"),
    ("CHART_SERIES", "Chart series drill link"),
    ("REGION_LINK", "Region source / more link"),
    ("PAGE_DUP_GOTO", "Page duplicate-submission redirect"),
)

RESOLVABLE_FLAGS = ("PAGE", "CROSS_APP")

_EDGE_COLUMNS = (
    "app_id, workspace, src_type, src_page, component_id, component, raw_target, "
    "target_app, target_app_id, target_page, flag, working_copy_id"
)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _build_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)


def _migrate_schema(connection: sqlite3.Connection) -> None:
    # component_id was originally INTEGER; Oracle internal sequence IDs can exceed
    # SQLite's signed 64-bit max. flow.db is a rebuild-on-demand cache so we just
    # drop and recreate when we detect the old column type.
    row = connection.execute(
        "SELECT type FROM pragma_table_info('apex_nav_edge') WHERE name = 'component_id'"
    ).fetchone()
    if row and row[0].upper() == "INTEGER":
        connection.executescript(
            "DROP TABLE IF EXISTS apex_nav_edge;"
            "DROP TABLE IF EXISTS apex_page;"
            "DROP TABLE IF EXISTS apex_app;"
            "DROP TABLE IF EXISTS apex_link_source_type;"
        )
        connection.executescript(SCHEMA)
        _seed_link_source_types(connection)
        connection.commit()


def _seed_link_source_types(connection: sqlite3.Connection) -> None:
    connection.executemany(
        "INSERT OR IGNORE INTO apex_link_source_type (src_type, description) VALUES (?, ?)",
        LINK_SOURCE_TYPES,
    )


def _row_to_edge(row: sqlite3.Row) -> FlowEdge:
    return FlowEdge(
        app_id          = row["app_id"],
        workspace       = row["workspace"],
        src_type        = row["src_type"],
        src_page        = row["src_page"],
        component_id    = row["component_id"],
        component       = row["component"],
        raw_target      = row["raw_target"],
        target_app      = row["target_app"],
        target_app_id   = row["target_app_id"],
        target_page     = row["target_page"],
        flag            = row["flag"],
        working_copy_id = row["working_copy_id"],
    )


class ApexFlowStore:
    """Persistent SQLite store for APEX page-navigation edges."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._con = connection
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA foreign_keys = ON")
        _build_schema(self._con)
        _migrate_schema(self._con)
        _seed_link_source_types(self._con)
        self._con.commit()

    @classmethod
    def open(cls, db_path: str | Path) -> ApexFlowStore:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(str(path)))

    def refresh_app(
        self,
        app: FlowApp,
        pages: Iterable[FlowPage],
        edges: Iterable[FlowEdge],
        *,
        loaded_at: str | None = None,
    ) -> None:
        # Add and update are one code path: delete-by-scope then re-insert in a
        # single transaction. Deleting the apex_app row cascades to its pages and
        # edges, so a rewrite is idempotent.
        stamp = loaded_at or _now()
        con = self._con
        try:
            con.execute("DELETE FROM apex_app WHERE app_id = ?", (app.app_id,))
            con.execute(
                "INSERT INTO apex_app (app_id, workspace, app_name, app_alias, loaded_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (app.app_id, app.workspace, app.app_name, app.app_alias, stamp),
            )
            con.executemany(
                "INSERT INTO apex_page (app_id, page_id, page_name, page_alias)"
                " VALUES (?, ?, ?, ?)",
                [(p.app_id, p.page_id, p.page_name, p.page_alias) for p in pages],
            )
            con.executemany(
                f"INSERT INTO apex_nav_edge ({_EDGE_COLUMNS}, loaded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [self._edge_row(edge, stamp) for edge in edges],
            )
            con.commit()
        except Exception:
            con.rollback()
            raise

    @staticmethod
    def _edge_row(edge: FlowEdge, stamp: str) -> Sequence[object]:
        return (
            edge.app_id, edge.workspace, edge.src_type, edge.src_page, edge.component_id,
            edge.component, edge.raw_target, edge.target_app, edge.target_app_id,
            edge.target_page, edge.flag, edge.working_copy_id, stamp,
        )

    def remove_app(self, app_id: int) -> bool:
        cursor = self._con.execute("DELETE FROM apex_app WHERE app_id = ?", (app_id,))
        self._con.commit()
        return cursor.rowcount > 0

    def all_app_ids(self) -> list[int]:
        rows = self._con.execute("SELECT app_id FROM apex_app ORDER BY app_id")
        return [row[0] for row in rows]

    def has_app(self, app_id: int) -> bool:
        row = self._con.execute(
            "SELECT 1 FROM apex_app WHERE app_id = ?", (app_id,)
        ).fetchone()
        return row is not None

    def app(self, app_id: int) -> FlowApp | None:
        row = self._con.execute(
            "SELECT app_id, workspace, app_name, app_alias FROM apex_app WHERE app_id = ?",
            (app_id,),
        ).fetchone()
        if row is None:
            return None
        return FlowApp(
            app_id    = row["app_id"],
            workspace = row["workspace"],
            app_name  = row["app_name"],
            app_alias = row["app_alias"],
        )

    def pages(self, app_id: int) -> list[FlowPage]:
        rows = self._con.execute(
            "SELECT app_id, page_id, page_name, page_alias FROM apex_page"
            " WHERE app_id = ? ORDER BY page_id",
            (app_id,),
        ).fetchall()
        return [
            FlowPage(
                app_id     = row["app_id"],
                page_id    = row["page_id"],
                page_name  = row["page_name"],
                page_alias = row["page_alias"],
            )
            for row in rows
        ]

    def edges(self, app_id: int) -> list[FlowEdge]:
        rows = self._con.execute(
            "SELECT * FROM apex_nav_edge WHERE app_id = ?"
            " ORDER BY src_type, src_page, component_id",
            (app_id,),
        ).fetchall()
        return [_row_to_edge(row) for row in rows]

    def incoming(self, app_id: int, page: int) -> list[FlowEdge]:
        rows = self._con.execute(
            "SELECT * FROM apex_nav_edge"
            " WHERE target_app_id = ? AND target_page = ?"
            "   AND flag IN ('PAGE','CROSS_APP')"
            " ORDER BY app_id, src_type, src_page",
            (app_id, page),
        ).fetchall()
        return [_row_to_edge(row) for row in rows]

    def outgoing(self, app_id: int, page: int) -> list[FlowEdge]:
        rows = self._con.execute(
            "SELECT * FROM apex_nav_edge"
            " WHERE app_id = ? AND src_page = ?"
            "   AND flag IN ('PAGE','CROSS_APP')"
            " ORDER BY target_app_id, target_page, src_type",
            (app_id, page),
        ).fetchall()
        return [_row_to_edge(row) for row in rows]

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> ApexFlowStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
