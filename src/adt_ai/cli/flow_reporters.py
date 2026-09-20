"""The page-link answers `flow` and `search` print, one renderer for both (`#30`).

Moved out of `commands_flow` when `search -to APP.PAGE` and `-from APP.PAGE`
began asking the same two questions of the same store. Each takes the edges it
is handed and renders them; it never opens the store, which keeps it a renderer
in the `test_renderer_purity.py` sense and lets the two commands agree on every
column by construction rather than by review.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

import yaml

from adt_ai.cli.constants import print_adt_header, print_adt_table

# Annotation only (ADT #895). This module ships in every release and the page
# store ships only with `rebuild` and `search`, so its edge class is never
# imported at run time.
if TYPE_CHECKING:  # pragma: no cover - typing only
    from adt_ai.flow.model import FlowEdge

#: `#861`, spelled by Jan picking it over reusing `APP NOT FOUND`.
APP_NOT_LOADED_HEADER = "WARNING - APP NOT LOADED:"

_COMPONENT_DISPLAY_LIMIT = 30
_REPORT_COLUMN_LINK_TYPES = {"IR_COL_LINK", "RPT_COL_LINK"}


def _print_not_loaded(rows: Iterable[str]) -> None:
    """Applications the flow store does not hold, under one header (`#861`).

    Jan picked `APP NOT LOADED` over reusing `APP NOT FOUND`, which already means
    missing from APEX itself: an app named here is in APEX and simply was never
    refreshed into the local store.
    """
    _print_warning(APP_NOT_LOADED_HEADER, list(rows))


def _print_warning(header: str, rows: list[str]) -> None:
    """One warning section of sentence rows, the shape `export_apex_messages` prints."""
    if not rows:
        return
    print_adt_header(header)
    for row in rows:
        print(f"  {row}")
    print()


def _print_page_links(
    direction: str,
    app_id: int,
    page: int,
    edges: list[FlowEdge],
    output_format: str = "table",
) -> None:
    """Links `into` a page or `from` it, as a table, yaml or md.

    The yaml rows are the table rows as data, keyed by the same labels the
    table prints upper case, so a reader moving between the two formats meets
    one vocabulary. `APP.PAGE` is how `search` names a page, so the yaml and md
    forms name it that way too.
    """
    rows = [_incoming_row(edge) if direction == "into" else _outgoing_row(edge) for edge in edges]
    if output_format == "yaml":
        print(
            yaml.safe_dump(
                {"page": f"{app_id}.{page}", f"links_{direction}": rows}, sort_keys=False
            ).rstrip()
        )
        return
    if output_format == "md":
        lines = [f"## Links {direction}: {app_id}.{page} ({len(rows)})", ""]
        lines.extend(_markdown_row(direction, row) for row in rows)
        if not rows:
            lines.append("- (none)")
        print("\n".join(lines))
        return
    # Two literal headers rather than one built from `direction`, so the console
    # inventory keeps reading both strings off their own call sites.
    if direction == "into":
        print_adt_header(f"LINKS INTO APP {app_id} PAGE {page} ({len(rows)}):")
    else:
        print_adt_header(f"LINKS FROM APP {app_id} PAGE {page} ({len(rows)}):")
    if rows:
        print_adt_table(rows)
    else:
        print("  (none)")


def _markdown_row(direction: str, row: dict[str, object]) -> str:
    side = "from" if direction == "into" else "to"
    return (
        f"- APP {row[f'{side}_app']} PAGE {row[f'{side}_page']}: "
        f"{row['src_type']} {row['component']} ({row['flag']})"
    )


def _incoming_row(edge: FlowEdge) -> dict[str, object]:
    # Keys are the column labels: print_adt_table renders each as UPPERCASE.
    # One row is one link, so the labels are singular; only count columns
    # (the refresh summary's PAGES/EDGES/DIAGRAMS) are plural.
    return {
        "from_app":  edge.app_id,
        "from_page": _src_page_label(edge),
        "src_type":  edge.src_type,
        "component": _component_label(edge),
        "flag":      edge.flag,
    }


def _outgoing_row(edge: FlowEdge) -> dict[str, object]:
    return {
        "to_app":    edge.target_app_id,
        "to_page":   edge.target_page,
        "src_type":  edge.src_type,
        "component": _component_label(edge),
        "flag":      edge.flag,
    }


def _src_page_label(edge: FlowEdge) -> object:
    # Shared components (tabs, lists, nav bar) are not bound to a source page.
    return edge.src_page if edge.src_page is not None else "shared"


def _component_label(edge: FlowEdge) -> str:
    component = str(edge.component or "")
    if edge.src_type in _REPORT_COLUMN_LINK_TYPES and _invalid_report_column_component(component):
        component = _report_column_fallback(edge)
    return component[:_COMPONENT_DISPLAY_LIMIT]


def _invalid_report_column_component(component: str) -> bool:
    component = component.strip()
    return bool(component) and bool(
        component.startswith("<") or re.search(r"\s", component)
    )


def _report_column_fallback(edge: FlowEdge) -> str:
    return f"COL_{edge.component_id}" if edge.component_id else ""


__all__ = [name for name in globals() if not name.startswith("__")]
