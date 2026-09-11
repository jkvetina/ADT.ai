"""``dependencies -scan``: compile an application's components and report the errors.

The third mode beside query and refresh (`#751`). It connects the way a refresh
connects and reads what `APEX_APP_OBJECT_DEPENDENCY.SCAN` could not compile,
then writes nothing at all -- no mirror row, no log, no receipt. Until this
existed, the only route to that answer was `patch -deploy`, which is a strange
thing to have to build in order to ask whether an application is broken.

`scan_application` is `patch`'s, unchanged. Its five outcomes already encode
what a verification proved, including the two that look quiet and are not
(`FAILED`, `EMPTY`), so a second implementation here would be a second opinion
about the same evidence.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    ConnectionConfigError,
    ConnectionResult,
    GatewayFactory,
    QueryGateway,
    print_adt_header,
)
from adt_ai.cli.context import (
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
    _parse_apex_page_selection,
    _print_connection_block,
)
from adt_ai.cli.dependencies_modes import (
    _connecting_mode_gateways,
    _query_requested,
    _refresh_chrome_stream,
    _refresh_lookup_schema,
    _resolve_refresh_app_ids,
)
from adt_ai.cli.dependencies_reporters import _print_component_scans
from adt_ai.cli.export_apex_owners import resolve_apex_owner_routes
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.dependencies import queries as dependency_queries
from adt_ai.export_apex.filters import ApexPageSelection
from adt_ai.patch.apex_scan import ApexScanReport, resolve_apex_version, scan_application
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.internal_paths import internal_path


def _scan_argument_error(args: argparse.Namespace) -> str | None:
    """What `-scan` and `-page` refuse, in the order a reader meets them.

    `-scan` is the third mode beside query and refresh: it connects like a
    refresh, reads what the APEX component scan could not compile, and writes
    nothing. So it combines with neither of the other two, and it cannot work
    out on its own which application the question is about -- an application
    scan with no application is not a wider scan, it is no scan at all.

    `-page` narrows the scan itself (ADT #751): `APEX_APP_OBJECT_DEPENDENCY.SCAN`
    takes `p_page_id`, so a named page is compiled instead of the application.
    Which is exactly why it still needs `-scan`. Jan, asked with chips before
    this was built: *"-scan should be required (if we want a scan), with scan you
    also have to provide -app, and you MIGHT provide -page. Later we might add
    other features, no -scan imply!"* So `-page` never turns a run into a scan;
    it says how narrow a scan already asked for should be. Without `-scan`, an
    invocation carrying `-app` is a refresh, and a refresh has no page scope.
    """
    scanning = bool(getattr(args, "scan", False))
    if scanning:
        if _query_requested(args):
            return "-scan cannot be combined with a query"
        if args.refresh is not None:
            return "-scan cannot be combined with -refresh"
        if not args.app:
            return "-scan needs -app to say which application to scan"
    if getattr(args, "page", None) and not scanning:
        return "-page narrows a -scan and needs it"
    return None


def _scan_applications(
    args: argparse.Namespace,
    root: Path,
    gateway_factory: GatewayFactory | None,
) -> int:
    """Compile every component of the named applications and report the errors.

    The connecting half is the refresh's, deliberately: `-app` ids and ranges,
    the owner routing that picks the one schema to connect through, and the APEX
    security context the scan needs are all already solved there, and a second
    spelling of any of them is how two commands come to disagree about what
    `-app 100-200` means. What is NOT reused is everything after the read: no
    store is opened, no row is written, no log is filed.
    """
    handler_started_at = time.monotonic()
    startup = _load_startup_context(args)
    connections = startup.connections
    environment = args.env or connections.default_environment
    debug = getattr(args, "debug", False)
    connection_for, selected_gateway_factory = _connecting_mode_gateways(
        startup, environment, gateway_factory, debug=debug
    )

    selection = _parse_apex_app_selection(_flatten_arg_groups(args.app))
    apps = _resolve_refresh_app_ids(
        selection, connections, environment, selected_gateway_factory
    )
    if selection is not None and selection.has_ranges and not apps:
        print_adt_error("INPUT NOT FOUND", "-app range matched no applications.")
        return exit_code_for("INPUT NOT FOUND")

    schemas = (
        connections.expand_schemas(
            _flatten_arg_groups(args.schema), environment=environment
        )
        if args.schema
        else []
    )
    schema = schemas[0] if schemas else _scan_schema(root, connections, environment, apps)
    if schema is None:
        print_adt_error("INPUT NOT FOUND", "No schema to scan through.")
        return exit_code_for("INPUT NOT FOUND")

    pages = _parse_apex_page_selection(_flatten_arg_groups(args.page))
    machine_format = getattr(args, "format", "table") != "table"
    timer_stdout = sys.stderr if machine_format else None

    def scan_segment(segment_schema: str) -> int:
        gateway = selected_gateway_factory(segment_schema)
        with _refresh_chrome_stream(machine_format):
            _print_connection_block(
                gateway, connection_for(segment_schema), debug=debug
            )
            # The header announces every read under it (`shared/announce.py`), so
            # it is printed BEFORE the scan rather than above its results, and on
            # whichever stream carries this run's chrome: the guard reads
            # `sys.stdout`, and under a machine format that is the redirected one.
            print_adt_header("SCANNING APPLICATIONS:")
            apex_version = resolve_apex_version(gateway)
            reports = _scan_reports(gateway, apps, pages, apex_version)
            if not machine_format:
                return _print_component_scans(reports, args.format)
        # A machine format's document is the one thing that stays on real stdout.
        return _print_component_scans(reports, args.format)

    return run_schema_sections(
        [schema], scan_segment, first_started_at=handler_started_at, timer_stdout=timer_stdout
    )


def _scan_reports(
    gateway: QueryGateway,
    apps: list[int],
    pages: ApexPageSelection | None,
    apex_version: str,
) -> list[ApexScanReport]:
    """One report per thing actually scanned: an application, or one of its pages.

    Without `-page` this is the application list unchanged. With it, every
    application is scanned once per named page, and each scan is its own report,
    because it IS its own scan (ADT #751). The alternative shape, one report
    per application carrying several pages' findings, cannot be built: APEX has
    no page-scoped `CLEAR_CACHE`, so scanning page 101 destroys the rows page
    100 just wrote, and the answer for each page has to be read back before the
    next one runs.

    An application whose `-page` selection resolves to nothing is reported
    rather than skipped, by scanning the pages as named. A page that turns out
    not to exist is the answer somebody typing `-page 101` wants to see.
    """
    if pages is None:
        return [
            scan_application(gateway, app_id, apex_version=apex_version)
            for app_id in apps
        ]
    return [
        scan_application(gateway, app_id, page_id=page_id, apex_version=apex_version)
        for app_id in apps
        for page_id in _selected_pages(gateway, app_id, pages)
    ]


def _selected_pages(
    gateway: QueryGateway,
    app_id: int,
    pages: ApexPageSelection,
) -> list[int]:
    """The concrete page ids a `-page` selection names for one application.

    Explicit ids pass through as typed, deliberately: `-page 101` on an
    application with no page 101 has to reach the scan so the report can say so,
    and resolving it away would answer a question nobody asked by staying quiet.
    A RANGE has no such reading (`-page 1-50` describes pages that exist, it
    does not assert that fifty of them do), so it is resolved against the
    application's own page list, one read, only when a range is present.
    """
    selected = list(pages.explicit_ids)
    if pages.ranges:
        rows = gateway.fetch_all(
            dependency_queries.APEX_APPLICATION_PAGE_IDS_QUERY, {"app_id": app_id}
        )
        for row in rows:
            page_id = _optional_page_id(row)
            if page_id is not None and pages.matches(page_id) and page_id not in selected:
                selected.append(page_id)
    return sorted(set(selected))


def _optional_page_id(row: dict[str, Any]) -> int | None:
    """`PAGE_ID` off a page-list row, whatever the gateway named the column."""
    value = row.get("PAGE_ID", row.get("page_id"))
    return None if value is None else int(value)


def _scan_schema(
    root: Path,
    connections: ConnectionResult,
    environment: str,
    apps: list[int],
) -> str | None:
    """The one schema a scan connects through, routed exactly as a refresh routes.

    `resolve_apex_owner_routes` answers from the cached APEX inventory, so the
    common case costs no round trip; mixed owners or unknown applications fall
    back to the environment's default schema, and a connection file carrying no
    default at all falls back to its first configured schema (`#670`).

    **A project with no inventory yet is not asked.** Opening that store creates
    the file, and this mode's whole contract is that it writes nothing: a scan
    that seeded an empty `apex.db` in a project that had never exported anything
    would be a write, and a routing question nothing can answer besides.
    """
    if not internal_path(root, "apex.db").exists():
        return _refresh_lookup_schema(connections, environment)
    try:
        owner_routes = resolve_apex_owner_routes(root, connections, environment, apps)
    except ConnectionConfigError:
        return _refresh_lookup_schema(connections, environment)
    return owner_routes.sole_owner or owner_routes.default_schema


__all__ = [name for name in globals() if not name.startswith("__")]
