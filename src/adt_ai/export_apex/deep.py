from __future__ import annotations

from pathlib import Path
from typing import Any

from adt_ai.dependencies.store import DependencyStore
from adt_ai.export_apex.filters import ApexComponentFilter, ApexPageSelection


class ApexDeepFilterError(ValueError):
    pass


def deep_component_filters(
    root: Path,
    app_id: int,
    page_selection: ApexPageSelection | None,
    component_filters: tuple[ApexComponentFilter, ...],
) -> tuple[ApexComponentFilter, ...]:
    if page_selection is None:
        raise ApexDeepFilterError("-deep requires -page")
    db_path = root / "config" / "dependencies.db"
    if not db_path.exists():
        raise ApexDeepFilterError(
            f"-deep requires dependency database at {db_path}"
        )
    store = DependencyStore.open(db_path)
    try:
        rows = store.apex_page_components(
            app_id,
            explicit_ids=page_selection.explicit_ids,
            ranges=page_selection.ranges,
        )
    finally:
        store.close()

    filters = list(component_filters)
    seen = {
        (item.component_type.strip().upper(), item.name_pattern.strip().upper())
        for item in filters
    }
    for row in rows:
        component_type = str(row["component_type"]).strip()
        component_name = str(row["component_name"]).strip()
        key = (component_type.upper(), component_name.upper())
        if key in seen:
            continue
        filters.append(ApexComponentFilter(component_type, component_name))
        seen.add(key)
    return tuple(filters)


def deep_db_object_rows(
    root: Path,
    app_id: int,
    page_selection: ApexPageSelection | None,
) -> list[dict[str, Any]]:
    if page_selection is None:
        raise ApexDeepFilterError("-deep requires -page")
    db_path = root / "config" / "dependencies.db"
    if not db_path.exists():
        raise ApexDeepFilterError(
            f"-deep requires dependency database at {db_path}"
        )
    store = DependencyStore.open(db_path)
    try:
        rows = store.apex_page_db_objects(
            app_id,
            explicit_ids=page_selection.explicit_ids,
            ranges=page_selection.ranges,
        )
    finally:
        store.close()

    collapsed = _collapse_column_duplicates(rows)
    return [
        {
            "TYPE_NAME": "DB OBJECTS",
            "ID": _db_object_id(row),
            "NAME": _db_object_label(row),
            "USED_ON_PAGES": [],
        }
        for row in collapsed
    ]


def _collapse_column_duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    non_column_keys = {
        (str(row.get("object_owner") or ""), str(row.get("object_name") or ""))
        for row in rows
        if str(row.get("object_type") or "").upper() != "COLUMN"
    }
    result = []
    seen = set()
    for row in rows:
        owner = str(row.get("object_owner") or "")
        name = str(row.get("object_name") or "")
        type_ = str(row.get("object_type") or "")
        if type_.upper() == "COLUMN" and (owner, name) in non_column_keys:
            continue
        key = (owner.upper(), type_.upper(), name.upper())
        if key in seen:
            continue
        result.append(row)
        seen.add(key)
    return result


def _db_object_id(row: dict[str, Any]) -> str:
    return "|".join(
        str(row.get(key) or "")
        for key in ("object_owner", "object_type", "object_name")
    )


def _db_object_label(row: dict[str, Any]) -> str:
    parts = [
        str(row.get("object_owner") or "").strip(),
        str(row.get("object_type") or "").strip(),
        str(row.get("object_name") or "").strip(),
    ]
    return " ".join(part for part in parts if part)
