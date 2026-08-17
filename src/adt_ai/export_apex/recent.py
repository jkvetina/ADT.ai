from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from adt_ai.export_apex import queries
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.recent_authors import (
    dedupe_recent_rows,
    recent_author_label,
    recent_authors,
)
from adt_ai.shared.dates import recent_since
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.progress import print_adt_header, schema_label
from adt_ai.shared.recent_state import parse_timestamp
from adt_ai.shared.row_values import row_value


def _print_application_export_header(application: ApexApplication) -> None:
    print_adt_header(f"APP {application.app_id}/{application.app_alias}, EXPORTING:")

def _print_schema_export_header(schema: str) -> None:
    """Header for a schema-level export slice, above the per-application ones.

    `-rest` writes workspace artifacts, so it has no `APP <id>/<alias>` to sit
    under, and on a schema with no applications there is no header at all, which
    is how a `-rest` run could finish printing nothing (ADT #190).
    """
    print_adt_header(f"SCHEMA {schema_label(schema)}, EXPORTING:")

def _print_recent_changes_header(
    application: ApexApplication,
    changed_since: str,
    author: str,
) -> None:
    suffix = f" BY {author}" if author else ""
    print_adt_header(
        f"APP {application.app_id}/{application.app_alias}, "
        f"CHANGES SINCE {changed_since}{suffix}:"
    )

def _recent_since(recent_days: int | float, db_now: str | None = None) -> str:
    # A sub-day window names the instant it starts at, so it is measured on the
    # database clock the filter uses, never the client's (ADT #340).
    return str(recent_since(recent_days, now=parse_timestamp(db_now)))

def _reports_recent_changes(request: Any, changed_since: str | None) -> bool:
    """Will this application get a `CHANGES SINCE` section at all?

    Answered from the request and the stored watermark alone, so the title can
    go up before the component listing runs rather than after it (`#372`). The
    author filter is deliberately not consulted here: it decides whether a
    section that *would* print is skipped for lack of matching rows, which is a
    question only the read can settle.
    """
    if request.recent is None:
        return False
    if changed_since is None and (not request.recent_days or request.recent_days <= 0):
        return False
    return request.recent_days is not None or changed_since is not None

def recent_components(
    gateway: QueryGateway,
    application: ApexApplication,
    request: Any,
    developers: Mapping[str, Mapping[str, str]],
    changed_since: str | None = None,
) -> list[dict[str, Any]] | None:
    """The components this window changed, or `None` when there is no window.

    Beside the predicate that decides whether the section prints and the render
    that prints it, rather than on the runner: the three are one concern, and
    the runner was over the 20 KB per-file context budget (`#372`).
    """
    if not _reports_recent_changes(request, changed_since):
        # Bare -recent with no watermark for any requested format: nothing to
        # narrow by, so the export covers the whole app (and may then seed).
        return None
    binds = {
        "app_id": application.app_id,
        "recent": request.recent_days,
        "changed_since": changed_since,
    }
    authors = recent_authors(application, developers, request)
    if not authors:
        return gateway.fetch_all(
            queries.RECENT_COMPONENTS_QUERY,
            {**binds, "author": None},
        )
    rows: list[dict[str, Any]] = []
    for author in authors:
        rows.extend(
            gateway.fetch_all(
                queries.RECENT_COMPONENTS_QUERY,
                {**binds, "author": author},
            )
        )
    return dedupe_recent_rows(rows)

def _changes_since_label(
    recent_days: int | float | None,
    changed_since: str | None,
    db_now: str | None,
) -> str:
    """The instant the `CHANGES SINCE` title names, without reading anything.

    Watermark mode shows the stored instant verbatim; `-recent N` keeps the
    day-count arithmetic it has always used, and a sub-day window reads its
    instant off the database clock.
    """
    if changed_since is not None:
        return changed_since
    return _recent_since(recent_days, db_now)

def open_application_section(
    request: Any,
    application: ApexApplication,
    changed_since: str | None,
    db_now: str | None,
    exporting_header: bool = True,
) -> str:
    """Print this application's first section title, before its first read.

    Returns which one went up, so the site that used to print it does not print
    it a second time: ``"changes"``, ``"exporting"``, or ``""`` when the run
    opens on neither.

    ``exporting_header=False`` for a `-compact` segment, where one bar under
    `SCHEMA <name>, EXPORTING:` stands in for every per-application block, so a
    title here would be the row that mode exists to remove (`#376`).

    **An author filter is not a special case.** It used to be: the section was
    skipped for an application that author had not touched, so whether to print
    the header was what the read decided, and the read ran silent. Jan,
    2026-08-16: *"If I ask for -recent you are printing the header even if you
    dont find anything, so you can without any issues print the header and then
    check/run the query for recent changes."* An unfiltered `-recent` has always
    printed its header over an empty listing, so a filtered one printing an
    empty section is the shape the command already had, and it is what lets the
    header lead the read (`#372`).
    """
    if _reports_recent_changes(request, changed_since):
        _print_recent_changes_header(
            application,
            _changes_since_label(request.recent_days, changed_since, db_now),
            recent_author_label(request) or "",
        )
        return "changes"
    if exporting_header and not request.recent_report_only and any(request.actions.values()):
        _print_application_export_header(application)
        return "exporting"
    return ""

def print_recent_changes(
    application: ApexApplication,
    recent_days: int | float | None,
    author_label: str | None,
    rows: list[dict[str, Any]],
    changed_since: str | None = None,
    db_now: str | None = None,
    header_printed: bool = False,
) -> None:
    """The `CHANGES SINCE` section: its title, unless already up, then its rows.

    An application the author has not touched gets the section anyway, empty,
    the same way an unfiltered `-recent` has always printed its header over an
    empty listing. The skip that used to live here is what kept the header
    behind the read (Jan, 2026-08-16, `#372`).
    """
    if recent_days is None and changed_since is None:
        return
    if not header_printed:
        _print_recent_changes_header(
            application,
            _changes_since_label(recent_days, changed_since, db_now),
            author_label or "",
        )
    _print_recent_components(rows)

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
# Formats that describe the entire application, so a `-recent`, `-page`, or
# `-component` narrowing must never drop their files. `apexlang` is one in v1:
# per-page APEXlang output is card `#48`, deliberately out of scope here.
WHOLE_APP_ACTIONS = frozenset({"full", "apexlang"})


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
