from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FlowApp:
    app_id: int
    workspace: str
    app_name: str | None = None
    app_alias: str | None = None


@dataclass(frozen=True)
class FlowPage:
    app_id: int
    page_id: int
    page_name: str | None = None
    page_alias: str | None = None


@dataclass(frozen=True)
class FlowEdge:
    app_id: int
    src_type: str
    src_page: int | None
    component_id: str | None
    component: str | None
    raw_target: str | None
    target_app_id: int | None
    target_page: int | None
    flag: str
    # The link's own application token, read by the JSON dump of a refresh.
    # Not stored (ADT #873), so an edge read back from `flow.db` carries None.
    target_app: str | None = None
