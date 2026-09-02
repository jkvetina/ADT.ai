from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass

from adt_ai.export_apex.recent import WHOLE_APP_ACTIONS, _page_id_from_export_path, _slug

_COMPONENT_TYPE_ALIASES = {
    "authorization_scheme": "authorization_scheme",
    "authorization_schemes": "authorization_scheme",
    "list_of_values": "lov",
    "lov": "lov",
}


@dataclass(frozen=True)
class ApexPageSelection:
    explicit_ids: tuple[int, ...] = ()
    ranges: tuple[tuple[int, int | None], ...] = ()

    def matches(self, page_id: int) -> bool:
        return page_id in self.explicit_ids or any(
            page_id >= low and (high is None or page_id <= high)
            for low, high in self.ranges
        )


@dataclass(frozen=True)
class ApexComponentFilter:
    component_type: str
    name_pattern: str

    def matches(self, relative: str) -> bool:
        normalized_type = _component_type_key(self.component_type)
        parts = {_slug(part) for part in relative.split("/")}
        type_names = {normalized_type, f"{normalized_type}s"}
        if not parts.intersection(type_names):
            return False
        name = _component_name(relative, normalized_type)
        pattern = _slug_pattern(self.name_pattern)
        return fnmatch.fnmatchcase(name, pattern)


@dataclass(frozen=True)
class ApexExplicitFilter:
    page_selection: ApexPageSelection | None = None
    component_filters: tuple[ApexComponentFilter, ...] = ()

    def selects_whole_app(self) -> bool:
        """True when no `-page` or `-component` narrows the export (ADT #655)."""
        return not (self.page_selection or self.component_filters)

    def matches(self, action: str, relative: str) -> bool:
        if action in WHOLE_APP_ACTIONS or not (self.page_selection or self.component_filters):
            return True
        page_id = _page_id_from_export_path(relative)
        if page_id is not None:
            return bool(self.page_selection and self.page_selection.matches(page_id))
        return any(
            component_filter.matches(relative)
            for component_filter in self.component_filters
        )


def _component_name(relative: str, component_type: str) -> str:
    stem = re.sub(r"\.[^.]+$", "", relative.rsplit("/", 1)[-1])
    name = _slug(stem)
    for prefix in (component_type, f"{component_type}s"):
        marker = f"{prefix}_"
        if name.startswith(marker):
            return name[len(marker):]
    return name


def _component_type_key(value: str) -> str:
    slug = _slug(value)
    return _COMPONENT_TYPE_ALIASES.get(slug, slug)


def _slug_pattern(value: str) -> str:
    marker = "__adt_wildcard__"
    protected = value.replace("%", marker).replace("*", marker)
    return _slug(protected).replace(_slug(marker), "*")
