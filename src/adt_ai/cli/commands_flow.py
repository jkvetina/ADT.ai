from __future__ import annotations

import argparse
import re
from collections.abc import Iterable
from pathlib import Path

from adt_ai.cli.constants import (
    ApexDiscovery,
    ApexFlowError,
    ApexFlowRefreshRequest,
    ApexFlowRefreshRunner,
    ApexFlowStore,
    ApexOwnerResolutionError,
    FlowEdge,
    GatewayFactory,
    QueryGateway,
    print_adt_header,
    print_adt_table,
    print_module_banner,
    resolve_configured_apex_owner_schema,
    write_all_dumps,
)
from adt_ai.cli.context import (
    ApexAppSelection,
    _app_in_selection,
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
    _print_connection_block,
)
from adt_ai.cli.export_apex_messages import (
    APP_NOT_FOUND_HEADER,
    SCHEMA_NOT_CONFIGURED_HEADER,
    print_apex_app_not_found,
)
from adt_ai.cli.export_apex_owners import apex_lookup_schema, listed_applications
from adt_ai.cli.gateways import build_gateway, cached_schema_gateway_factory
from adt_ai.shared.connections import Connection
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.progress import schema_label

#: `#861`, spelled by Jan picking it over reusing `APP NOT FOUND`.
APP_NOT_LOADED_HEADER = "WARNING - APP NOT LOADED:"

_NO_FLOW_DB_MESSAGE = "No APEX flow database found."
_NO_FLOW_DB_REMEDY = "Run `adtai flow -app N -refresh` to build it."
_APP_REQUIRED_MESSAGE = "An application id is required: pass -app N."
_COMPONENT_DISPLAY_LIMIT = 30
_REPORT_COLUMN_LINK_TYPES = {"IR_COL_LINK", "RPT_COL_LINK"}


def _flow_argument_error(args: argparse.Namespace) -> str | None:
    """Refuse two actions in one invocation instead of picking a winner.

    `flow` has four of them and used to rank them: `-refresh` returned before
    `-delete` was read, `-delete` before `-to`/`-from`, and `-to` before
    `-from`. So `flow -app 100 -refresh -delete` deleted nothing and said
    nothing, and `-to 5 -from 5` answered half the question asked. Silent
    precedence is the shape ADT.ai rejects everywhere else -- `console.md`
    §Which command takes which puts it as "A flag a command does not take is a
    parser error, not a flag it ignores", and the same holds for a flag it takes
    but cannot honour here (`#656`).

    Returned (non-``None``) by the dispatcher before the command runs, exactly
    like ``_dependencies_argument_error``, so misuse lands on the parser-style
    error screen with exit 2 rather than inside a handler that already printed
    its banner.
    """
    actions = [
        flag
        for flag, present in (
            ("-refresh", bool(getattr(args, "refresh", False))),
            ("-delete", bool(getattr(args, "delete", False))),
            ("-to", getattr(args, "to_page", None) is not None),
            ("-from", getattr(args, "from_page", None) is not None),
        )
        if present
    ]
    if len(actions) > 1:
        return f"{' / '.join(actions)} are separate actions; pass one per run"
    return None


def _run_flow(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_module_banner("FLOW")
    try:
        selection = _parse_apex_app_selection(_flatten_arg_groups(args.app))
    except ValueError as exc:
        print_adt_error("ARGUMENT INVALID", str(exc))
        return exit_code_for("ARGUMENT INVALID")
    if selection is None:
        print_adt_error("ARGUMENT INVALID", _APP_REQUIRED_MESSAGE)
        return exit_code_for("ARGUMENT INVALID")

    root    = Path(args.root).expanduser().resolve()
    db_path = internal_path(root, "flow.db")

    # One action per run: `_flow_argument_error` has already refused any pair on
    # the dispatcher's side, so the order these are read in is arbitrary rather
    # than a precedence rule (`#656`).
    if args.refresh:
        return _refresh_flow(args, root, db_path, gateway_factory, selection)

    # Every other action reads the persistent store; opening it would create an
    # empty database, so a missing file is reported instead of silently seeded.
    if not db_path.exists():
        print_adt_error("INPUT NOT FOUND", _NO_FLOW_DB_MESSAGE, _NO_FLOW_DB_REMEDY)
        return exit_code_for("INPUT NOT FOUND")

    with ApexFlowStore.open(db_path) as store:
        app_ids = _selection_to_store_ids(store, selection)
        if args.delete:
            return _delete_flow_apps(store, app_ids)
        if args.to_page is None and args.from_page is None:
            _print_flow_hint()
            return 2
        not_loaded = [app_id for app_id in app_ids if not store.has_app(app_id)]
        _print_not_loaded(
            f"APP {app_id} is not loaded, run adtai flow -app {app_id} -refresh first"
            for app_id in not_loaded
        )
        for app_id in app_ids:
            if app_id in not_loaded:
                continue
            if args.to_page is not None:
                _print_flow_incoming(store, app_id, args.to_page)
            elif args.from_page is not None:
                _print_flow_outgoing(store, app_id, args.from_page)
        return 1 if not_loaded else 0


def _selection_to_store_ids(store: ApexFlowStore, selection: ApexAppSelection) -> list[int]:
    """Expand a selection to app IDs present in the store.

    Explicit IDs are returned as-is (caller checks has_app).
    Ranges are filtered against loaded apps so the loop only touches what exists.
    """
    if not selection.has_ranges:
        return [int(id) for id in selection.explicit_ids]
    return [aid for aid in store.all_app_ids() if _app_in_selection(aid, selection)]


def _refresh_flow(
    args: argparse.Namespace,
    root: Path,
    db_path: Path,
    gateway_factory: GatewayFactory | None,
    selection: ApexAppSelection,
) -> int:
    startup = _load_startup_context(args)
    connections = startup.connections
    environment = args.env or connections.default_environment
    configured_schemas = connections.schema_names(environment)
    lookup_schema = apex_lookup_schema(connections, environment, configured_schemas)
    connection_cache: dict[str, Connection] = {}

    def connection_for(schema_name: str) -> Connection:
        if schema_name not in connection_cache:
            connection_cache[schema_name] = connections.resolve(
                environment=environment,
                schema=schema_name,
                kind="apex",
            )
        return connection_cache[schema_name]

    def default_gateway_factory(schema_name: str) -> QueryGateway:
        return build_gateway(startup, connection_for(schema_name), project_root=root)

    # Per-schema cache and `-debug` wrap in one shared helper, so the console
    # guard keeps the nesting `build_gateway` documents (`#670`).
    flow_gateway_factory = cached_schema_gateway_factory(
        gateway_factory or default_gateway_factory, debug=args.debug
    )

    if selection.has_ranges:
        # Discover apps across all configured schemas and filter in Python.
        # Deduplicate because an app exists in exactly one schema's owner, but
        # FakeGateway (and cautious real usage) may surface the same id twice.
        discovery = ApexDiscovery(flow_gateway_factory(lookup_schema))
        seen: set[int] = set()
        app_ids: list[int] = []
        for app in listed_applications(discovery, configured_schemas):
            if _app_in_selection(app.app_id, selection) and app.app_id not in seen:
                seen.add(app.app_id)
                app_ids.append(app.app_id)
        if not app_ids:
            print_adt_error("INPUT NOT FOUND", "-app range matched no applications.")
            return exit_code_for("INPUT NOT FOUND")
    else:
        app_ids = [int(id) for id in selection.explicit_ids]

    connection_block_printed: set[str] = set()
    any_error = False

    # **Every owner lookup happens here, before the first thing is printed**
    # (`#372`). One dictionary read per application, and interleaved with the
    # loop below the second one onwards landed under the previous application's
    # finished table with the screen saying nothing. Up here they run under the
    # module banner, which is still the newest thing on the terminal.
    owner_schemas: dict[int, str] = {}
    not_found: list[str] = []
    not_configured: list[str] = []
    for app_id in app_ids:
        try:
            owner_schemas[app_id] = resolve_configured_apex_owner_schema(
                ApexDiscovery(flow_gateway_factory(lookup_schema)),
                app_id=app_id,
                configured_schemas=configured_schemas,
            ).schema
        except ApexOwnerResolutionError as error:
            if error.owner is None:
                not_found.append(str(app_id))
            else:
                not_configured.append(
                    f"APP {app_id} is owned by {error.owner}, "
                    "add it to your connections to refresh it"
                )
            any_error = True
    # Under the two headers `export_apex` names these same cases with (`#858`),
    # rather than the resolver's bare sentences on stderr (`#861`).
    _print_warning(SCHEMA_NOT_CONFIGURED_HEADER, not_configured)
    print_apex_app_not_found(not_found)

    with ApexFlowStore.open(db_path) as store:
        for app_id in app_ids:
            schema = owner_schemas.get(app_id)
            if schema is None:
                continue

            connection = connection_for(schema)
            if schema not in connection_block_printed:
                _print_connection_block(flow_gateway_factory(schema), connection, debug=args.debug)
                connection_block_printed.add(schema)

            # The refresh reads the application, its pages and its navigation
            # edges, and prints nothing until all three are back, so it used to
            # run behind the connection block's closing blank. This is the
            # header `dependencies -refresh` already prints in front of the same
            # per-application dictionary scan, so the string is one the console
            # surface already carries and the wait now sits under its own name.
            print_adt_header(f"APP {app_id}, REFRESHING:")

            try:
                result = ApexFlowRefreshRunner(flow_gateway_factory).refresh(
                    ApexFlowRefreshRequest(app_id=app_id, schema=schema, store=store)
                )
            except ApexFlowError:
                # The owner answered and the application read came back empty.
                _print_warning(
                    APP_NOT_FOUND_HEADER,
                    [f"APP {app_id} is not in its owner schema {schema_label(schema)}"],
                )
                any_error = True
                continue

            dump_paths = write_all_dumps(result.app, result.pages, result.edges, root=root)
            alias = (result.app.app_alias or result.app.app_name or str(result.app.app_id)).upper()
            print_adt_header(f"APP {result.app.app_id}/{alias}, REFRESHED:")
            print_adt_table(
                [
                    {
                        "pages": result.page_count,
                        "edges": result.edge_count,
                        "diagrams": len(dump_paths),
                    }
                ]
            )

    return 1 if any_error else 0


def _delete_flow_apps(store: ApexFlowStore, app_ids: list[int]) -> int:
    not_loaded: list[int] = []
    for app_id in app_ids:
        if store.remove_app(app_id):
            print_adt_header(f"DELETED APP {app_id}:")
        else:
            not_loaded.append(app_id)
    _print_not_loaded(f"APP {app_id} is not loaded, nothing to delete" for app_id in not_loaded)
    return 1 if not_loaded else 0


def _print_not_loaded(rows: Iterable[str]) -> None:
    """Applications the flow store does not hold, under one header (`#861`).

    Jan picked `APP NOT LOADED` over reusing `APP NOT FOUND`, which already means
    missing from APEX itself: an app named here is in APEX and simply was never
    refreshed into the local store. These were bare stderr lines, one per app,
    and the remedy named `adt flow`, the old CLI.
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


def _print_flow_incoming(store: ApexFlowStore, app_id: int, page: int) -> int:
    edges = store.incoming(app_id, page)
    print_adt_header(f"LINKS INTO APP {app_id} PAGE {page} ({len(edges)}):")
    if edges:
        print_adt_table([_incoming_row(edge) for edge in edges])
    else:
        print("  (none)")
    return 0


def _print_flow_outgoing(store: ApexFlowStore, app_id: int, page: int) -> int:
    edges = store.outgoing(app_id, page)
    print_adt_header(f"LINKS FROM APP {app_id} PAGE {page} ({len(edges)}):")
    if edges:
        print_adt_table([_outgoing_row(edge) for edge in edges])
    else:
        print("  (none)")
    return 0


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


def _print_flow_hint() -> None:
    print("Specify an action: -to PAGE, -from PAGE, -refresh, or -delete.")
    print("  -to PAGE    pages that link INTO this page")
    print("  -from PAGE  pages reachable FROM this page")
    print("  -refresh    rebuild the store and write Mermaid, DOT, and JSON diagrams")

__all__ = [name for name in globals() if not name.startswith("__")]
