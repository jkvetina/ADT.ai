from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from typing import Any

from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.shared.row_values import row_value


def _print_application_export_header(application: ApexApplication) -> None:
    message = f"APP {application.app_id}/{application.app_alias}, EXPORTING:"
    print()
    print(message)
    print("-" * len(message))

def _print_recent_changes_header(
    application: ApexApplication,
    changed_since: str,
    author: str,
) -> None:
    suffix = f" BY {author}" if author else ""
    message = (
        f"APP {application.app_id}/{application.app_alias}, "
        f"CHANGES SINCE {changed_since}{suffix}:"
    )
    print()
    print(message)
    print("-" * len(message))

def _recent_since(recent_days: int) -> str:
    return str(datetime.date.today() - datetime.timedelta(days=recent_days - 1))

def _print_recent_components(rows: list[dict[str, Any]]) -> None:
    grouped: dict[str, dict[object, dict[str, object]]] = {}
    for row in rows:
        group = str(row_value(row, "TYPE_NAME") or "")
        component_id = row_value(row, "ID")
        grouped.setdefault(group, {})[component_id] = {
            "name": row_value(row, "NAME"),
            "pages": _used_on_pages(row_value(row, "USED_ON_PAGES")),
        }
    for group in sorted(grouped):
        print(f"  {group}:")
        page_width = 0
        if group == "PAGE":
            page_width = max((len(str(component_id)) for component_id in grouped[group]), default=0)
        for component_id, info in grouped[group].items():
            name = str(info["name"] or "")
            pages = info["pages"]
            if group == "PAGE":
                page_id, _, page_name = name.partition(".")
                page_id = page_id or str(component_id)
                name = f"{page_id:>{page_width}}) {page_name.strip()}"
            elif pages:
                name += f" {pages}"
            print(f"    {'- ' if group != 'PAGE' else ''}{name}")
        print()

def _used_on_pages(value: object) -> list[object]:
    if value is None:
        return []
    if hasattr(value, "aslist"):
        return list(value.aslist())
    if isinstance(value, list | tuple):
        return list(value)
    return [value]

# Formats that describe the whole application in one artefact. A component-level
# cutoff (`-recent`, `-page`, `-component`) has nothing to select inside them, so
# filtering one out would silently drop the format from a filtered run.
WHOLE_APP_ACTIONS = frozenset({"full", "checksum"})


@dataclass(frozen=True)
class RecentComponentFilter:
    page_ids: frozenset[int] | None = None
    component_slugs: frozenset[str] = frozenset()

    def matches(self, action: str, relative: str) -> bool:
        if action in WHOLE_APP_ACTIONS or self.page_ids is None:
            return True
        page_id = _page_id_from_export_path(relative)
        if page_id is not None:
            return page_id in self.page_ids
        normalized_path = _slug(relative)
        return any(slug in normalized_path for slug in self.component_slugs)

def _recent_component_filter(rows: list[dict[str, Any]] | None) -> RecentComponentFilter:
    if rows is None:
        return RecentComponentFilter(page_ids=None)
    page_ids = {
        page_id
        for row in rows
        if str(row_value(row, "TYPE_NAME") or "").upper() == "PAGE"
        for page_id in [_page_id_from_recent_page_row(row)]
        if page_id is not None
    }
    component_slugs = {
        slug
        for row in rows
        if str(row_value(row, "TYPE_NAME") or "").upper() != "PAGE"
        for slug in [_slug(str(row_value(row, "NAME") or ""))]
        if slug
    }
    return RecentComponentFilter(
        page_ids        = frozenset(page_ids),
        component_slugs = frozenset(component_slugs),
    )

def _page_id_from_export_path(relative: str) -> int | None:
    match = re.search(r"(?:^|/)pages/(?:page_|p)(\d+)\.", relative)
    return int(match.group(1)) if match else None

def _page_id_from_recent_page_row(row: dict[str, Any]) -> int | None:
    name_match = re.match(r"\s*(\d+)\s*\.", str(row_value(row, "NAME") or ""))
    if name_match:
        return int(name_match.group(1))
    component_id = row_value(row, "ID")
    return int(component_id) if component_id is not None else None

def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
