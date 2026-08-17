from __future__ import annotations

import argparse
import re
import sys
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
    DebugQueryGateway,
    _app_in_selection,
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
    _print_connection_block,
)
from adt_ai.cli.export_apex_owners import listed_applications
from adt_ai.cli.gateways import build_gateway
from adt_ai.shared.internal_paths import internal_path

_NO_FLOW_DB_MESSAGE = (
    "No APEX flow database found. Run 'adt flow -app N -refresh' to build it."
)
_APP_REQUIRED_MESSAGE = "An application id is required: pass -app N."
_COMPONENT_DISPLAY_LIMIT = 30
_REPORT_COLUMN_LINK_TYPES = {"IR_COL_LINK", "RPT_COL_LINK"}


def _run_flow(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_module_banner("FLOW")
    try:
        selection = _parse_apex_app_selection(_flatten_arg_groups(args.app))
    except ValueError as exc:
        print(f"flow: -app {exc}", file=sys.stderr)
        return 2
    if selection is None:
        print(_APP_REQUIRED_MESSAGE, file=sys.stderr)
        return 2

    root    = Path(args.root).expanduser().resolve()
    db_path = internal_path(root, "flow.db")

    if args.refresh:
        return _refresh_flow(args, root, db_path, gateway_factory, selection)

    # Every other action reads the persistent store; opening it would create an
    # empty database, so a missing file is reported instead of silently seeded.
    if not db_path.exists():
        print(_NO_FLOW_DB_MESSAGE, file=sys.stderr)
        return 1

    with ApexFlowStore.open(db_path) as store:
        app_ids = _selection_to_store_ids(store, selection)
        if args.delete:
            return _delete_flow_apps(store, app_ids)
        if args.to_page is None and args.from_page is None:
            _print_flow_hint()
            return 2
        any_error = False
        for app_id in app_ids:
            if not store.has_app(app_id):
                print(_app_not_loaded_message(app_id), file=sys.stderr)
                any_error = True
                continue
            if args.to_page is not None:
                _print_flow_incoming(store, app_id, args.to_page)
            elif args.from_page is not None:
                _print_flow_outgoing(store, app_id, args.from_page)
        return 1 if any_error else 0


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
    default_schemas = connections.default_schemas(environment, kind="apex")
    lookup_schema = default_schemas[0] if default_schemas else configured_schemas[0]
    connection_cache: dict[str, object] = {}
    gateway_cache: dict[str, QueryGateway] = {}

    def connection_for(schema_name: str) -> object:
        if schema_name not in connection_cache:
            connection_cache[schema_name] = connections.resolve(
                environment=environment,
                schema=schema_name,
                kind="apex",
            )
        return connection_cache[schema_name]

    def default_gateway_factory(schema_name: str) -> QueryGateway:
        return build_gateway(startup, connection_for(schema_name), project_root=root)

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def flow_gateway_factory(schema_name: str) -> QueryGateway:
        if schema_name not in gateway_cache:
            gateway = selected_gateway_factory(schema_name)
            gateway_cache[schema_name] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[schema_name]

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
            print("flow: -app range matched no applications.", file=sys.stderr)
            return 1
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
    for app_id in app_ids:
        try:
            owner_schemas[app_id] = resolve_configured_apex_owner_schema(
                ApexDiscovery(flow_gateway_factory(lookup_schema)),
                app_id=app_id,
                configured_schemas=configured_schemas,
            ).schema
        except ApexOwnerResolutionError as error:
            print(str(error), file=sys.stderr)
            any_error = True

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
            except ApexFlowError as error:
                print(str(error), file=sys.stderr)
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
    any_error = False
    for app_id in app_ids:
        if store.remove_app(app_id):
            print_adt_header(f"DELETED APP {app_id}:")
        else:
            print(f"Application {app_id} was not loaded; nothing to delete.", file=sys.stderr)
            any_error = True
    return 1 if any_error else 0


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
    # Keys are the column labels: print_adt_table renders each as UPPERCASE and
    # the flow tables use the PLURAL form (matching the refresh summary's
    # PAGES/EDGES/DIAGRAMS headers).
    return {
        "from_apps":  edge.app_id,
        "from_pages": _src_page_label(edge),
        "src_types":  edge.src_type,
        "components": _component_label(edge),
        "flags":      edge.flag,
    }


def _outgoing_row(edge: FlowEdge) -> dict[str, object]:
    return {
        "to_apps":    edge.target_app_id,
        "to_pages":   edge.target_page,
        "src_types":  edge.src_type,
        "components": _component_label(edge),
        "flags":      edge.flag,
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
    return bool(component) and (component.startswith("<") or re.search(r"\s", component))


def _report_column_fallback(edge: FlowEdge) -> str:
    return f"COL_{edge.component_id}" if edge.component_id else ""


def _app_not_loaded_message(app_id: int) -> str:
    return f"Application {app_id} is not loaded. Run 'adt flow -app {app_id} -refresh' first."


def _print_flow_hint() -> None:
    print("Specify an action: -to PAGE, -from PAGE, -refresh, or -delete.")
    print("  -to PAGE    pages that link INTO this page")
    print("  -from PAGE  pages reachable FROM this page")
    print("  -refresh    rebuild the store and write Mermaid, DOT, and JSON diagrams")

__all__ = [name for name in globals() if not name.startswith("__")]
