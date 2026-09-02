from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from adt_ai.flow import queries
from adt_ai.flow.model import FlowApp, FlowEdge, FlowPage
from adt_ai.shared.sqlite_store import Migration, open_store

# Persistent store: the .db IS the source of truth (populated from live Oracle on
# refresh), so the schema is created with IF NOT EXISTS and never dropped by a
# refresh. The one drop is the lift from the pre-version shape (ADT #642), a
# cache the next refresh refills. The SQL lives in `flow/queries/store.py`.
SCHEMA_VERSION = "1"

MIGRATIONS: tuple[Migration, ...] = (
    Migration(None, "1", lambda connection: connection.executescript(queries.STORE_LIFT_LEGACY)),
)

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


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _seed_link_sources(connection: sqlite3.Connection) -> None:
    connection.executemany(queries.STORE_SEED_LINK_SOURCE, LINK_SOURCE_TYPES)


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

    @classmethod
    def open(cls, db_path: str | Path) -> ApexFlowStore:
        """Open the file through the shared opener and reseed the link catalog."""
        connection = open_store(
            db_path,
            schema     = queries.STORE_SCHEMA,
            version    = SCHEMA_VERSION,
            migrations = MIGRATIONS,
        )
        try:
            _seed_link_sources(connection)
            connection.commit()
        except BaseException:
            connection.close()
            raise
        return cls(connection)

    def refresh_app(
        self,
        app: FlowApp,
        pages: Iterable[FlowPage],
        edges: Iterable[FlowEdge],
        *,
        loaded_at: str | None = None,
    ) -> None:
        # Add and update are one code path: delete-by-scope then re-insert in a
        # single transaction. Deleting the application row cascades to its pages
        # and edges, so a rewrite is idempotent.
        stamp = loaded_at or _now()
        con = self._con
        try:
            con.execute(queries.STORE_DELETE_APP, (app.app_id,))
            con.execute(
                queries.STORE_INSERT_APP,
                (app.app_id, app.workspace, app.app_name, app.app_alias, stamp),
            )
            con.executemany(
                queries.STORE_INSERT_PAGE,
                [(p.app_id, p.page_id, p.page_name, p.page_alias) for p in pages],
            )
            con.executemany(
                queries.STORE_INSERT_EDGE,
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
        cursor = self._con.execute(queries.STORE_DELETE_APP, (app_id,))
        self._con.commit()
        return cursor.rowcount > 0

    def all_app_ids(self) -> list[int]:
        rows = self._con.execute(queries.STORE_APP_IDS_QUERY)
        return [row[0] for row in rows]

    def has_app(self, app_id: int) -> bool:
        row = self._con.execute(queries.STORE_APP_EXISTS_QUERY, (app_id,)).fetchone()
        return row is not None

    def app(self, app_id: int) -> FlowApp | None:
        row = self._con.execute(queries.STORE_APP_QUERY, (app_id,)).fetchone()
        if row is None:
            return None
        return FlowApp(
            app_id    = row["app_id"],
            workspace = row["workspace"],
            app_name  = row["app_name"],
            app_alias = row["app_alias"],
        )

    def pages(self, app_id: int) -> list[FlowPage]:
        rows = self._con.execute(queries.STORE_PAGES_QUERY, (app_id,)).fetchall()
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
        rows = self._con.execute(queries.STORE_EDGES_QUERY, (app_id,)).fetchall()
        return [_row_to_edge(row) for row in rows]

    def incoming(self, app_id: int, page: int) -> list[FlowEdge]:
        rows = self._con.execute(queries.STORE_INCOMING_QUERY, (app_id, page)).fetchall()
        return [_row_to_edge(row) for row in rows]

    def outgoing(self, app_id: int, page: int) -> list[FlowEdge]:
        rows = self._con.execute(queries.STORE_OUTGOING_QUERY, (app_id, page)).fetchall()
        return [_row_to_edge(row) for row in rows]

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> ApexFlowStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
