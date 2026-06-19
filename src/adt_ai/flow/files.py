from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path

from adt_ai.flow.model import FlowApp, FlowEdge, FlowPage

EXTENSIONS = {"mermaid": "mmd", "dot": "dot", "json": "json"}
GRAPH_FLAGS = ("PAGE", "CROSS_APP")


def _page_label(page_id: int, page_names: dict[int, str | None]) -> str:
    name = page_names.get(page_id)
    return f"{page_id} {name}" if name else str(page_id)


def _node_id(prefix: str, *parts: object) -> str:
    return "_".join([prefix, *(str(part) for part in parts)])


def _src_node(edge: FlowEdge, page_names: dict[int, str | None]) -> tuple[str, str]:
    # Shared components (tabs, lists, nav bar) have no source page — they are
    # global entry points, drawn as a single labelled node per source type.
    if edge.src_page is None:
        return _node_id("shared", edge.src_type), f"{edge.src_type} (shared)"
    return _node_id("p", edge.app_id, edge.src_page), _page_label(edge.src_page, page_names)


def _tgt_node(edge: FlowEdge, page_names: dict[int, str | None]) -> tuple[str, str]:
    if edge.flag == "CROSS_APP":
        return (
            _node_id("x", edge.target_app_id, edge.target_page),
            f"app {edge.target_app_id} p{edge.target_page}",
        )
    return _node_id("p", edge.app_id, edge.target_page), _page_label(edge.target_page, page_names)


def _graph_edges(edges: Iterable[FlowEdge]) -> list[FlowEdge]:
    return [edge for edge in edges if edge.flag in GRAPH_FLAGS and edge.target_page is not None]


def _page_names(pages: Iterable[FlowPage]) -> dict[int, str | None]:
    return {page.page_id: page.page_name for page in pages}


def render_mermaid(app: FlowApp, pages: Iterable[FlowPage], edges: Iterable[FlowEdge]) -> str:
    page_names = _page_names(pages)
    nodes: dict[str, str] = {}
    relations: list[tuple[str, str, str]] = []
    for edge in _graph_edges(edges):
        src_id, src_label = _src_node(edge, page_names)
        tgt_id, tgt_label = _tgt_node(edge, page_names)
        nodes[src_id] = src_label
        nodes[tgt_id] = tgt_label
        relations.append((src_id, edge.src_type, tgt_id))
    lines = [f"%% APEX flow map: app {app.app_id} ({app.workspace})", "flowchart LR"]
    for node_id, label in nodes.items():
        lines.append(f'    {node_id}["{_mermaid_text(label)}"]')
    for src_id, label, tgt_id in relations:
        lines.append(f"    {src_id} -->|{_mermaid_text(label)}| {tgt_id}")
    return "\n".join(lines) + "\n"


def render_dot(app: FlowApp, pages: Iterable[FlowPage], edges: Iterable[FlowEdge]) -> str:
    page_names = _page_names(pages)
    nodes: dict[str, str] = {}
    relations: list[tuple[str, str, str]] = []
    for edge in _graph_edges(edges):
        src_id, src_label = _src_node(edge, page_names)
        tgt_id, tgt_label = _tgt_node(edge, page_names)
        nodes[src_id] = src_label
        nodes[tgt_id] = tgt_label
        relations.append((src_id, edge.src_type, tgt_id))
    lines = [f"digraph flow_{app.app_id} {{", "    rankdir=LR;"]
    for node_id, label in nodes.items():
        lines.append(f'    "{node_id}" [label="{_dot_text(label)}"];')
    for src_id, label, tgt_id in relations:
        lines.append(f'    "{src_id}" -> "{tgt_id}" [label="{_dot_text(label)}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def render_json(app: FlowApp, pages: Iterable[FlowPage], edges: Iterable[FlowEdge]) -> str:
    # Complete dump: every page and every edge of every flag (DYNAMIC/OTHER/NONE
    # included) so the file is a full, lossless export of the relations.
    payload = {
        "app": {
            "app_id": app.app_id,
            "workspace": app.workspace,
            "app_name": app.app_name,
            "app_alias": app.app_alias,
        },
        "pages": [
            {
                "page_id": page.page_id,
                "page_name": page.page_name,
                "page_alias": page.page_alias,
            }
            for page in pages
        ],
        "edges": [_edge_dict(edge) for edge in edges],
    }
    return json.dumps(payload, indent=2) + "\n"


def _edge_dict(edge: FlowEdge) -> dict[str, object]:
    return {
        "src_type": edge.src_type,
        "src_page": edge.src_page,
        "component_id": edge.component_id,
        "component": edge.component,
        "raw_target": edge.raw_target,
        "target_app": edge.target_app,
        "target_app_id": edge.target_app_id,
        "target_page": edge.target_page,
        "flag": edge.flag,
    }


_RENDERERS = {
    "mermaid": render_mermaid,
    "dot": render_dot,
    "json": render_json,
}


def render(
    fmt: str,
    app: FlowApp,
    pages: Sequence[FlowPage],
    edges: Sequence[FlowEdge],
) -> str:
    return _RENDERERS[fmt](app, pages, edges)


def dump_path(app_id: int, fmt: str, root: str | Path = ".", out: str | Path | None = None) -> Path:
    if out is not None:
        return Path(out)
    return Path(root) / "config" / "flow" / f"app_{app_id}.{EXTENSIONS[fmt]}"


def write_dump(
    app: FlowApp,
    pages: Sequence[FlowPage],
    edges: Sequence[FlowEdge],
    *,
    fmt: str = "mermaid",
    root: str | Path = ".",
    out: str | Path | None = None,
) -> Path:
    path = dump_path(app.app_id, fmt, root=root, out=out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(fmt, app, pages, edges))
    return path


def write_all_dumps(
    app: FlowApp,
    pages: Sequence[FlowPage],
    edges: Sequence[FlowEdge],
    *,
    root: str | Path = ".",
) -> list[Path]:
    return [
        write_dump(app, pages, edges, fmt=fmt, root=root)
        for fmt in ("mermaid", "dot", "json")
    ]


def _mermaid_text(value: str) -> str:
    return value.replace('"', "'").replace("\n", " ").strip()


def _dot_text(value: str) -> str:
    return value.replace('"', '\\"').replace("\n", " ").strip()
