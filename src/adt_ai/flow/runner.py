from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from adt_ai.flow.model import FlowApp, FlowEdge, FlowPage
from adt_ai.flow.queries import APP_METADATA_QUERY, APP_PAGES_QUERY, NAV_EDGES_QUERY
from adt_ai.flow.store import ApexFlowStore
from adt_ai.shared.db import QueryGateway

GatewayFactory = Callable[[str], QueryGateway]


class ApexFlowError(Exception):
    """Raised when an application cannot be loaded from the target schema."""


@dataclass(frozen=True)
class ApexFlowRefreshRequest:
    app_id: int
    schema: str
    store: ApexFlowStore


@dataclass(frozen=True)
class ApexFlowRefreshResult:
    app: FlowApp
    pages: list[FlowPage]
    edges: list[FlowEdge]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


def _resolve_target_app_id(flag: str, app_id: int, target_app: str | None) -> int | None:
    if flag == "PAGE":
        return app_id
    if flag == "CROSS_APP":
        try:
            return int(target_app)
        except (TypeError, ValueError):
            return None
    return None


class ApexFlowRefreshRunner:
    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self.gateway_factory = gateway_factory

    def refresh(self, request: ApexFlowRefreshRequest) -> ApexFlowRefreshResult:
        gateway = self.gateway_factory(request.schema)
        app = self._load_app(gateway, request.app_id)
        pages = self._load_pages(gateway, request.app_id)
        edges = self._load_edges(gateway, app)
        request.store.refresh_app(app, pages, edges)
        return ApexFlowRefreshResult(app=app, pages=pages, edges=edges)

    @staticmethod
    def _load_app(gateway: QueryGateway, app_id: int) -> FlowApp:
        rows = gateway.fetch_all(APP_METADATA_QUERY, {"app_id": app_id})
        if not rows:
            raise ApexFlowError(
                f"application {app_id} not found in the resolved APEX owner schema"
            )
        row = rows[0]
        return FlowApp(
            app_id    = app_id,
            workspace = row["WORKSPACE"],
            app_name  = row["APP_NAME"],
            app_alias = row["APP_ALIAS"],
        )

    @staticmethod
    def _load_pages(gateway: QueryGateway, app_id: int) -> list[FlowPage]:
        rows = gateway.fetch_all(APP_PAGES_QUERY, {"app_id": app_id})
        return [
            FlowPage(
                app_id     = app_id,
                page_id    = row["PAGE_ID"],
                page_name  = row["PAGE_NAME"],
                page_alias = row["PAGE_ALIAS"],
            )
            for row in rows
        ]

    @staticmethod
    def _load_edges(gateway: QueryGateway, app: FlowApp) -> list[FlowEdge]:
        rows = gateway.fetch_all(NAV_EDGES_QUERY, {"app_id": app.app_id})
        edges: list[FlowEdge] = []
        for row in rows:
            flag = row["FLAG"]
            target_app = row["TARGET_APP"]
            edges.append(
                FlowEdge(
                    app_id        = app.app_id,
                    workspace     = app.workspace,
                    src_type      = row["SRC_TYPE"],
                    src_page      = row["SRC_PAGE"],
                    component_id  = (
                        str(row["COMPONENT_ID"]) if row["COMPONENT_ID"] is not None else None
                    ),
                    component     = row["COMPONENT"],
                    raw_target    = row["RAW_TARGET"],
                    target_app    = target_app,
                    target_app_id = _resolve_target_app_id(flag, app.app_id, target_app),
                    target_page   = row["TARGET_PAGE"],
                    flag          = flag,
                )
            )
        return edges
