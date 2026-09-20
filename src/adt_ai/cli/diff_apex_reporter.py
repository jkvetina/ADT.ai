"""The `diff -apex` screen (ADT #892, reshaped by #893, #896, #898 and #899).

Jan, 2026-09-19, on the first cut, which listed one `APEX APPLICATION ...
CHANGED` row and stopped: -verbose must say which page, which components, and
what changed. Asked how, he answered *"Each changed component = 1 line!"*.

`#893` settled what a line holds: *"you will list component type, name,
status, property"*, so a line has four columns, `TYPE`, `NAME`, `STATUS` and
`PROPERTY`, and no values. `#898` split it by property: *"Multiple properties
= multiple lines, 1 prop per 1 line"*. A component that changed in three
properties is three lines, each carrying the component's `TYPE`, `NAME` and
`STATUS` (`#899`, Jan: *"Not the empty lines!"*).

Every application that differs opens on a `CHANGED COMPONENTS - <app>:`
summary (`#896`, Jan: *"I want to see summary table with PAGES + count, non
page tight changes (per type) + count"*): one row per page and one per
component type outside a page, each counting its components by status, the
columns `diff -data`'s `CHANGED TABLES:` counts rows in. A zero is left blank
(`#898`: *"Dont show "0" values! It is just noise."*).

`-verbose` adds one `CHANGED PAGE <app>.<page> - <name>:` section per page,
the way `diff -data` prints one `CHANGED ROWS - <TABLE>:` per table (`#896`).
Nothing outside a page gets a section: the summary's type rows already are
that listing (`#898`, Jan: *"Why I have both sections? It does not make
sense. Remove the bottom table"*).

`CHANGED WORKSPACE FILES:` closes the listings, as `#893` placed it. Its size
reads in one unit, named once in the header (Jan: *"KB, but you put that in
header as SIZE [KB]"*), so every row carries the same kind of number.
"""
from __future__ import annotations

from collections.abc import Sequence

from adt_ai.cli.constants import print_adt_header
from adt_ai.cli.diff_reporters import _ELLIPSIS, MAX_LINE, print_listing
from adt_ai.diff.apex import ApexDiff, ApplicationChanges, FileChange
from adt_ai.diff.apex_components import (
    APPLICATION_COMPONENT,
    PAGE_KIND,
    ComponentChange,
    PropertyChange,
    split_property,
    sub_component,
)
from adt_ai.diff.inventory import CHANGED, EXTRA, MISSING

SUMMARY_HEADER = "CHANGED COMPONENTS - {application}:"
PAGE_HEADER = "CHANGED PAGE {page}:"
WORKSPACE_FILES_HEADER = "CHANGED WORKSPACE FILES:"

#: The type an application's own static file is listed as, in its group.
APP_FILE_KIND = "APP FILE"

#: The workspace files' size column, its unit in the header (#896).
SIZE_COLUMN = "size [kb]"

#: How far a shared component's sub-component sits in from the component.
_INDENT = "  "

#: Which columns give way when a line will not fit: the name first, then the
#: property, the type only as a last resort. `STATUS` and the counts never do.
_COMPONENT_TIERS = (("name",), ("property",), ("type",))
_FILE_TIERS = (("name",),)

#: The summary's count columns, in the order `diff -data`'s `CHANGED TABLES:` has them.
_COUNTS = (MISSING, EXTRA, CHANGED)

Row = dict[str, object]

#: A component as the screen lists it: type, name, status, changed properties.
Line = tuple[str, str, str, list[str]]


def report_apex(
    result: ApexDiff, *, verbose: bool = False, limit: int | None = None
) -> tuple[str, ...]:
    """The summaries, the groups under `-verbose`, then the workspace's files.

    Returns the statuses it printed, so the legend under it defines them too.
    `limit` caps each listing at that many lines.
    """
    printed: list[str] = []
    for application in result.applications:
        rows = application_summary(application)
        if not rows:
            continue
        print_adt_header(SUMMARY_HEADER.format(application=block_title(application)))
        print_listing(rows, limit=limit, tiers=_COMPONENT_TIERS, numeric=_counts_columns())
        printed.extend(
            status for status in _COUNTS if any(row[status.lower()] for row in rows)
        )
    if verbose:
        for application in result.applications:
            for title, rows in page_groups(application):
                print_adt_header(title)
                printed.extend(print_listing(rows, limit=limit, tiers=_COMPONENT_TIERS))
    if result.workspace_files:
        print_adt_header(WORKSPACE_FILES_HEADER)
        printed.extend(
            print_listing(
                [_file_row(file) for file in result.workspace_files],
                limit   = limit,
                tiers   = _FILE_TIERS,
                numeric = (SIZE_COLUMN,),
            )
        )
    return tuple(printed)


def page_groups(application: ApplicationChanges) -> list[tuple[str, list[Row]]]:
    """One group per page that differs, titled `CHANGED PAGE <app>.<page> - <name>:`."""
    if application.status != CHANGED:
        return []
    return [
        (_page_title(application, component), _rows(_page_lines(component)))
        for component in application.components
        if component.kind == PAGE_KIND
    ]


def application_summary(application: ApplicationChanges) -> list[Row]:
    """A row per page and a row per type outside a page, counting components by status."""
    if application.status != CHANGED:
        return [_count(APPLICATION_COMPONENT, application.application, [application.status])]
    rows: list[Row] = []
    types: dict[str, list[str]] = {}
    for component in application.components:
        if component.kind == PAGE_KIND:
            statuses = [status for _, _, status, _ in _page_lines(component)]
            rows.append(_count(PAGE_KIND, component.name, statuses))
            continue
        for kind, _, status, _ in _component_lines(component):
            types.setdefault(kind, []).append(status)
    for file in application.files:
        types.setdefault(APP_FILE_KIND, []).append(file.status)
    rows.extend(_count(kind, "", statuses) for kind, statuses in types.items())
    return rows


def _count(kind: str, name: str, statuses: Sequence[str]) -> Row:
    return {
        "type": kind,
        "name": name,
        **{status.lower(): statuses.count(status) or "" for status in _COUNTS},
    }


def _counts_columns() -> tuple[str, ...]:
    return tuple(status.lower() for status in _COUNTS)


def _page_title(application: ApplicationChanges, page: ComponentChange) -> str:
    """`CHANGED PAGE 122.20 - Request:`, the page as `APP.PAGE`, trimmed to the screen."""
    number, _, name = page.name.partition(" ")
    label = f"{_app_id(application)}.{number}"
    if name:
        label = f"{label} - {name}"
    room = MAX_LINE - len(PAGE_HEADER.format(page=""))
    if len(label) > room:
        label = label[: room - len(_ELLIPSIS)] + _ELLIPSIS
    return PAGE_HEADER.format(page=label)


def _app_id(application: ApplicationChanges) -> int | str:
    """The id the comparison filed the application under, else its label's first word."""
    return application.app_id or application.application.partition(" ")[0]


def block_title(application: ApplicationChanges, header: str = SUMMARY_HEADER) -> str:
    """`100 ORDERS -> 101 ORDERS_COPY` for a `-target-app` pair, the label alone otherwise.

    A name can outrun the screen, so the longer one gives a character at a time,
    ending on `...`, until the header fits `MAX_LINE` (ADT #893).
    """
    names = [application.application, *([application.target] if application.target else [])]
    joiner = " -> "
    room = MAX_LINE - len(header.format(application=joiner if len(names) > 1 else ""))
    while sum(map(len, names)) > room:
        longer = max(range(len(names)), key=lambda index: len(names[index]))
        kept = names[longer].removesuffix(_ELLIPSIS)[:-1]
        names[longer] = f"{kept}{_ELLIPSIS}"
    return joiner.join(names)


def _page_lines(page: ComponentChange) -> list[Line]:
    """The page itself only when it changed itself, then its sub-components.

    The group's header already names the page, so its components sit flush, and
    a page changed only below it needs no line of its own.
    """
    lines = _component_lines(page, indent="")
    if page.status == CHANGED and not lines[0][3]:
        lines = lines[1:]
    return lines


def _component_lines(component: ComponentChange, indent: str = _INDENT) -> list[Line]:
    """The component, then each sub-component that changed under it."""
    groups: dict[str, list[PropertyChange]] = {}
    for change in component.properties:
        groups.setdefault(split_property(change.name)[0], []).append(change)
    own = groups.pop("", [])
    lines = [_line(component.kind, component.name, component.status, own)]
    for owner, changes in groups.items():
        kind, name = sub_component(owner)
        lines.append(_line(kind, f"{indent}{name}", _sub_status(changes), changes))
    return lines


def _sub_status(changes: Sequence[PropertyChange]) -> str:
    """`MISSING` when the target lacks every property, `EXTRA` when the source does."""
    if all(change.target is None for change in changes):
        return MISSING
    if all(change.source is None for change in changes):
        return EXTRA
    return CHANGED


def _line(kind: str, name: str, status: str, changes: Sequence[PropertyChange]) -> Line:
    """A `MISSING` or `EXTRA` component names no property."""
    return kind, name, status, _property_names(changes) if status == CHANGED else []


def _rows(lines: Sequence[Line]) -> list[Row]:
    """One row per changed property, each repeating its component (#898, #899).

    A component with no property to name, `MISSING`, `EXTRA` or changed only
    below it, is one row.
    """
    return [
        _row(kind, name, status, prop)
        for kind, name, status, properties in lines
        for prop in properties or [""]
    ]


def _row(kind: str, name: str, status: str, prop: str = "") -> Row:
    return {"type": kind, "name": name, "status": status, "property": prop}


def _property_names(changes: Sequence[PropertyChange]) -> list[str]:
    names = [split_property(change.name)[1] for change in changes]
    short = _short_names(names)
    return [short[name] for name in names]


def _short_names(names: Sequence[str]) -> dict[str, str]:
    """`execution.sequence` reads `sequence` beside the process it belongs to.

    Two properties of one component sharing a last key keep their whole paths,
    so neither reads as the other.
    """
    leaves = [name.rsplit(".", 1)[-1] for name in names]
    return {
        name: leaf if leaves.count(leaf) == 1 else name
        for name, leaf in zip(names, leaves, strict=True)
    }


def _file_row(file: FileChange) -> Row:
    return {"name": file.name, SIZE_COLUMN: f"{file.size / 1024:.1f}", "status": file.status}


__all__ = [name for name in globals() if not name.startswith("_")]
