from __future__ import annotations

import re
from typing import Any

from adt_ai.export_apex.recent import (
    _page_id_from_export_path,
    _print_recent_components,
)

_COMPONENT_TYPES = {
    "authorization_schemes": "AUTHORIZATION",
    "build_options": "BUILD OPTION",
    "breadcrumbs": "BREADCRUMB",
    "lists": "LIST",
    "lovs": "LOV",
    "plugins": "PLUGIN",
    "security": "SECURITY",
    "shortcuts": "SHORTCUT",
    "templates": "TEMPLATE",
}


def _is_partial(request: object) -> bool:
    return bool(
        getattr(request, "page_selection", None)
        or getattr(request, "component_filters", ())
        or getattr(request, "recent_days", None)
        or getattr(request, "changed_by", None)
        or getattr(request, "my_changes", False)
    )


def _has_explicit(request: object) -> bool:
    return bool(
        getattr(request, "page_selection", None)
        or getattr(request, "component_filters", ())
    )


def _component_row(
    action: str,
    relative: str,
    page_names: dict[int, str],
) -> dict[str, Any] | None:
    if action not in {"split", "readable", "embedded"}:
        return None
    page_id = _page_id_from_export_path(relative)
    if page_id is not None:
        name = page_names.get(page_id) or f"Page {page_id}"
        return {
            "TYPE_NAME": "PAGE",
            "ID": page_id,
            "NAME": f"{page_id}. {name}",
            "USED_ON_PAGES": [],
        }
    parts = [part for part in relative.split("/") if part]
    for index, part in enumerate(parts):
        if part not in _COMPONENT_TYPES:
            continue
        filename = parts[index + 1] if index + 1 < len(parts) else ""
        name = _component_name(filename, part)
        if not name:
            return None
        return {
            "TYPE_NAME": _COMPONENT_TYPES[part],
            "ID": relative,
            "NAME": name,
            "USED_ON_PAGES": [],
        }
    return None


def _print_components(rows: list[dict[str, Any]]) -> None:
    if rows:
        _print_recent_components(rows)


def _component_name(filename: str, component_folder: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", filename)
    prefixes = [component_folder]
    if component_folder.endswith("s"):
        prefixes.append(component_folder[:-1])
    for prefix in prefixes:
        marker = f"{prefix}_"
        if stem.startswith(marker):
            stem = stem[len(marker):]
            break
    return re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").upper()
