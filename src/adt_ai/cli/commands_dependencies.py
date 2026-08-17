from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from adt_ai.cli.constants import (
    ConfigLoader,
    DependencyIndexRequest,
    DependencyIndexRunner,
    DependencyStore,
    GatewayFactory,
    QueryGateway,
    print_adt_header,
    print_module_banner,
)
from adt_ai.cli.context import (
    ApexAppSelection,
    DebugQueryGateway,
    _app_in_selection,
    _config_search_paths,
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
    _print_connection_block,
    _repo_root,
)
from adt_ai.cli.dependencies_reporters import (
    _print_dependency_age,
    _print_dependency_impact,
    _print_dependency_list,
    _print_foreign_key_tree,
)
from adt_ai.cli.export_apex_owners import listed_applications, resolve_apex_owner_routes
from adt_ai.cli.export_reporters import ConsoleApexRevealReporter
from adt_ai.cli.gateways import build_gateway
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.dependencies.store import DEFAULT_MAX_DEPTH
from adt_ai.export_apex.inventory import ApexApplication, ApexDiscovery
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.progress import FixedWidthProgressPrinter, schema_label

_NO_DEPENDENCY_INDEX_MESSAGE = (
    "No dependency database found. Run 'adt dependencies -refresh' to build it."
)


def _query_requested(args: argparse.Namespace) -> bool:
    """True when the invocation names a query mode, so it reads the mirror offline.

    The one question the command asks of its own arguments, and the reason it is
    a named helper rather than an inline chain: the dispatcher's argument check
    and the command body both decide refresh against query, and a second spelling
    of the same list is how two such decisions drift apart.
    """
    return bool(
        args.uses
        or args.used_by
        or args.impact
        or args.tree
        or getattr(args, "age", False)
    )


def _dependencies_argument_error(args: argparse.Namespace) -> str | None:
    """Reject the refresh options beside a query, and reject bad ``-app`` ids.

    Returned (non-``None``) by the dispatcher before the command runs, so misuse
    surfaces as a parser-style error screen, never a silently-accepted flag.
    ``-app``/``-force``/``-recent`` steer the refresh, so they are refused only
    when the invocation also asked a question; on their own they now describe a
    refresh, which is what a query-less invocation means. ``-schema`` is accepted
    either way and reads as whichever mode it landed in: an offline owner
    disambiguator beside a query (see ``_resolve_query_schemas``), the refresh
    scope without one.
    """
    offenders = [
        flag
        for flag, present in (
            ("-app", bool(args.app)),
            ("-force", bool(getattr(args, "force", False))),
            ("-recent", getattr(args, "recent", None) is not None),
        )
        if present
    ]
    if offenders and _query_requested(args):
        return f"{' / '.join(offenders)} steers -refresh and cannot be combined with a query"
    # Delegate -app validation to the shared APEX selection parser so ranges
    # (MIN-MAX / MIN+) are accepted exactly as export_apex/flow accept them; a
    # malformed range surfaces as the parser-style error screen. Explicit ids
    # must still be numeric.
    try:
        selection = _parse_apex_app_selection(_flatten_arg_groups(args.app))
    except ValueError as exc:
        return str(exc)
    if selection is not None:
        for app_id in selection.explicit_ids:
            if not app_id.isdigit():
                return f"invalid APP_ID: {app_id}"
    return None


def _resolve_refresh_app_ids(
    selection: ApexAppSelection | None,
    connections,
    environment: str | None,
    gateway_factory: GatewayFactory,
) -> list[int]:
    """Resolve the ``-app`` selection into a unique, ordered list of app ids.

    No selection → no apps. Explicit ids (no ranges) pass straight through. A
    range (MIN-MAX / MIN+) is resolved against apps discovered across the
    configured schemas and filtered with ``_app_in_selection``, the same shape
    ``_refresh_flow`` uses (cli_commands_flow.py) so the two commands agree on
    range semantics.
    """
    if selection is None:
        return []
    if not selection.has_ranges:
        return [int(app_id) for app_id in selection.explicit_ids]

    configured_schemas = connections.schema_names(environment)
    defaults = connections.default_schemas(environment)
    lookup_schema = (
        defaults[0]
        if defaults
        else (configured_schemas[0] if configured_schemas else None)
    )
    if lookup_schema is None:
        return []
    discovery = ApexDiscovery(gateway_factory(lookup_schema))
    seen: set[int] = set()
    app_ids: list[int] = []
    for app in listed_applications(discovery, configured_schemas):
        if _app_in_selection(app.app_id, selection) and app.app_id not in seen:
            seen.add(app.app_id)
            app_ids.append(app.app_id)
    return app_ids


def _resolve_refresh_names(raw: list[str] | None) -> list[str]:
    """Flatten repeated/comma-joined refresh names into unique uppercase values."""
    names: list[str] = []
    for value in raw or []:
        for part in str(value).split(","):
            part = part.strip().upper()
            if part and part not in names:
                names.append(part)
    return names


def _resolve_query_schemas(raw: list[str] | None) -> list[str]:
    """Flatten ``-schema`` into a unique uppercase owner list for query modes.

    Reached only from a query mode, so it is offline by construction: a literal,
    case-insensitive, comma-separated list of owner names parsed locally with no
    DB connection. Empty/whitespace values are dropped, so an all-blank
    ``-schema`` behaves as if it were absent. The same flag with no query beside
    it is the refresh scope instead and never arrives here.
    """
    return _resolve_refresh_names(raw)


def _run_dependencies(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    # Human (table) output carries the generic banner/footer on stdout like every
    # other command; machine output (-format yaml/md) keeps stdout pure data and
    # sends the chrome to stderr so it stays pipeable.
    chrome = sys.stdout if args.format == "table" else sys.stderr
    print_module_banner("DEPENDENCIES", file=chrome)

    root    = Path(args.root).expanduser().resolve()
    db_path = internal_path(root, "dependencies.db")

    # Refresh is the default, and a query is the thing you opt into. Rebuilding
    # the mirror is the one job here that needs no argument to describe it, so an
    # invocation carrying no question is that job; -refresh stays as its explicit
    # spelling. What used to sit here was a four line usage hint, which made the
    # zero-argument run the only run that did nothing.
    if args.refresh is not None or not _query_requested(args):
        return _refresh_dependency_index(args, root, gateway_factory)

    if not db_path.exists():
        print(_NO_DEPENDENCY_INDEX_MESSAGE, file=sys.stderr)
        return 1

    # -schema in a query mode is an offline owner disambiguator: parse it
    # locally and narrow the matched owner column. Empty/absent → all tracked
    # owners (unchanged). -tree and impact's column/apex lineage are not filtered.
    query_schemas = _resolve_query_schemas(_flatten_arg_groups(args.schema))

    with DependencyStore.open(db_path) as store:
        if args.age:
            # Offline staleness report: read the per-scope last-refresh stamps
            # from _meta; no query object, no connection.
            exit_code = _print_dependency_age(store.last_refreshes(), args.format)
        elif args.uses:
            exit_code = _print_dependency_list(
                args.uses, store.uses(args.uses, owners=query_schemas), "uses", args.format
            )
        elif args.used_by:
            exit_code = _print_dependency_list(
                args.used_by,
                store.used_by(args.used_by, owners=query_schemas),
                "used_by",
                args.format,
            )
        elif args.impact:
            # Impact is the only query mode with a config knob, so the config
            # load stays inside this branch instead of taxing every query.
            config = ConfigLoader(
                _config_search_paths(args.config_dir, root, _repo_root())
            ).load().data
            max_depth = int(config.get("dependencies_max_depth") or DEFAULT_MAX_DEPTH)
            exit_code = _print_dependency_impact(
                args.impact,
                store.impact(args.impact, max_depth=max_depth, owners=query_schemas),
                args.format,
                store.affected_columns(args.impact),
                store.apex_callers(args.impact),
            )
        else:
            # -tree is the last query mode, and the only one left: a query-less
            # invocation refreshed above and never reaches this block.
            exit_code = _print_foreign_key_tree(
                args.tree,
                store.foreign_key_tree(args.tree),
                args.format,
            )

    return exit_code


def _refresh_dependency_index(
    args: argparse.Namespace,
    root: Path,
    gateway_factory: GatewayFactory | None,
) -> int:
    handler_started_at = time.monotonic()
    # One shared context, like every other connecting command. Assembling config,
    # connections and session SQL by hand here is what made this the one command
    # that connected with no STARTUP.sql at all (ADT #177): a hand-rolled context
    # only holds the pieces whoever wrote it happened to remember.
    startup = _load_startup_context(args)
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    # Two independent refresh axes: -schema drives the USER_* mirror, -app the
    # APEX_* mirror. A refresh naming neither keeps the old default (every
    # default schema); -app alone refreshes only the APEX axis.
    refresh_names = _resolve_refresh_names(args.refresh)
    if args.schema:
        schemas = connections.expand_schemas(
            _flatten_arg_groups(args.schema), environment=environment
        )
    elif args.app and not refresh_names:
        schemas = []
    else:
        schemas = connections.default_schemas(environment)

    debug = getattr(args, "debug", False)
    connection_cache: dict[str, object] = {}

    def connection_for(schema: str):
        if schema not in connection_cache:
            connection_cache[schema] = connections.resolve(
                environment=environment, schema=schema
            )
        return connection_cache[schema]

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, connection_for(schema))

    base_gateway_factory = gateway_factory or default_gateway_factory

    def selected_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = base_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if debug else gateway
        return gateway_cache[schema]

    # -app reuses the shared APEX selection parser: explicit ids flow through
    # unchanged; ranges (MIN-MAX / MIN+) are resolved against discovered apps.
    selection = _parse_apex_app_selection(_flatten_arg_groups(args.app))
    apps = _resolve_refresh_app_ids(
        selection, connections, environment, selected_gateway_factory
    )
    if selection is not None and selection.has_ranges and not apps:
        print("dependencies: -app range matched no applications.", file=sys.stderr)
        return 1

    # APEX_* views are pulled over one schema's connection; default to the first
    # refreshed schema, else the environment's first default schema. When -app is
    # given without -schema, prefer the app's recorded owner schema from the
    # cached config/internal/apex_apps.yaml so we connect straight to it and skip the
    # wasted default-schema connection. The routing itself is export_apex's own
    # resolver, one derivation, two callers; `sole_owner` is where this command's
    # single-connection constraint is applied, and mixed owners or unknown apps
    # fall back to the default schema.
    app_schema: str | None = schemas[0] if schemas else None
    if apps and app_schema is None:
        owner_routes = resolve_apex_owner_routes(root, connections, environment, apps)
        app_schema = owner_routes.sole_owner or owner_routes.default_schema

    silent = getattr(args, "silent", False)
    runner = DependencyIndexRunner(selected_gateway_factory)
    # -format yaml/md keeps stdout pure data even for -refresh chrome, matching
    # the runtime's own _command_timer_stdout routing for this command.
    timer_stdout = sys.stderr if getattr(args, "format", "table") != "table" else None

    # Segments: one per -schema axis schema, plus (when the app axis targets a
    # schema not already in that list) one final app-only segment. When
    # app_schema coincides with a schema axis schema, the app axis is folded
    # into that schema's own segment instead of getting a separate one.
    segments = list(schemas)
    if app_schema and apps and app_schema not in segments:
        segments.append(app_schema)

    def run_one(schema: str) -> int:
        is_schema_segment = schema in schemas
        is_app_segment = schema == app_schema and bool(apps)
        # **The application rows are read before the connection block, not
        # after it** (`#372`). Both headers they feed name them, `REFRESHING
        # <SCHEMA> SCHEMA AND APEX APP <label>:` and the workspace in
        # `APEX APPLICATIONS: <workspace> | <SCHEMA>`, so neither can lead the
        # read; up here the module banner is still the newest thing on screen
        # and the read is accounted for. Jan, 2026-08-16: *"You can resolve the
        # schema/workspace from apex.db, if it is missing, you can do extra
        # query."* The store answers first and costs no round trip at all.
        discovered_apps: list[ApexApplication] = []
        if is_app_segment:
            discovered_apps = _stored_apex_applications(root, apps)
            if not discovered_apps:
                discovered_apps = _discover_apex_applications(
                    selected_gateway_factory(app_schema), app_schema, apps
                )

        versions = _print_connection_block(
            selected_gateway_factory(schema), connection_for(schema), debug=debug
        )
        segment_apex_versions = {schema: versions["APEX"]} if versions.get("APEX") else {}

        segment_app_labels: dict[int, str] = {}

        if is_app_segment:
            labels = _apex_app_labels(apps, discovered_apps)
            segment_app_labels = dict(zip(apps, labels, strict=True))
            if is_schema_segment:
                apex_apps = ", ".join(f"APEX APP {label}" for label in labels)
                print_adt_header(
                    f"REFRESHING {schema_label(schema)} SCHEMA AND {apex_apps}:"
                )
            else:
                # Same shape export_apex prints before its own per-app export loop:
                # one APEX APPLICATIONS: table instead of a flat comma-joined banner.
                ConsoleApexRevealReporter().applications(schema, discovered_apps)
        elif is_schema_segment:
            # The schema is uppercased into the sentence rather than trailing a
            # colon: `REFRESHING: ict_owner` left the dashed rule stopping at the
            # colon, one word short of the line it was underlining (ADT #237).
            # Uppercasing is `schema_label`'s job, not an inline `.upper()`, the
            # inline call is what let the other headers drift (ADT #240).
            print_adt_header(f"REFRESHING {schema_label(schema)} SCHEMA:")

        runner.refresh(
            DependencyIndexRequest(
                root       = root,
                schemas    = [schema] if is_schema_segment else [],
                config     = config,
                apps       = apps if is_app_segment else [],
                app_schema = schema if is_app_segment else None,
                force      = getattr(args, "force", False),
                recent     = getattr(args, "recent", None),
                progress   = None if silent else FixedWidthProgressPrinter(),
                apex_versions = segment_apex_versions,
                refresh_names = refresh_names,
                app_labels = segment_app_labels if is_app_segment else None,
            )
        )
        return 0

    return run_schema_sections(
        segments, run_one, first_started_at=handler_started_at, timer_stdout=timer_stdout
    )


def _stored_apex_applications(root: Path, apps: list[int]) -> list[ApexApplication]:
    """``apps`` as ``apex.db`` already knows them, or ``[]`` if it knows none.

    Every application ADT.ai has exported is in that store, workspace and alias
    included, which is the whole of what the two headers below need. All or
    nothing on purpose: a partial answer would label some applications and leave
    the rest as bare ids, which reads as a discovery failure rather than a cache
    miss, and the query it saves is one round trip.
    """
    try:
        with ApexStore.load(root) as store:
            rows = [store.application(app) for app in apps]
    except Exception:
        return []
    if not all(rows):
        return []
    return [
        ApexApplication(
            owner        = str(row.get("owner") or ""),
            workspace    = str(row.get("workspace") or ""),
            workspace_id = None,
            app_group    = str(row.get("app_group") or ""),
            app_id       = app,
            app_alias    = str(row.get("app_alias") or ""),
            app_name     = str(row.get("app_name") or ""),
            pages        = None,
            updated_at   = str(row.get("updated_at") or ""),
        )
        for app, row in zip(apps, rows, strict=True)
    ]


def _discover_apex_applications(
    gateway: QueryGateway | None,
    owner: str | None,
    apps: list[int],
) -> list[ApexApplication]:
    """Full discovered rows for ``apps``, in ``apps`` order; ``[]`` when offline."""
    if gateway is None or owner is None:
        return []
    # `-refresh -app` reads the application rows before it can label anything,
    # and that read sat behind the connection block (`#360`).
    discovered = listed_applications(ApexDiscovery(gateway), [owner], app_ids=apps)
    by_id = {application.app_id: application for application in discovered}
    return [by_id[app] for app in apps if app in by_id]


def _apex_app_labels(apps: list[int], applications: list[ApexApplication]) -> list[str]:
    """``id/ALIAS`` display labels, falling back to a bare id when undiscovered."""
    by_id = {application.app_id: application for application in applications}
    labels: dict[int, str] = {}
    for app in apps:
        application = by_id.get(app)
        if application is None:
            labels[app] = str(app)
        else:
            alias = application.app_alias or application.app_name or str(app)
            labels[app] = f"{app}/{alias.upper()}"
    return [labels[app] for app in apps]

__all__ = [name for name in globals() if not name.startswith("__")]
