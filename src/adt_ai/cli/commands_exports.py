from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Mapping

from adt_ai.cli.constants import (
    APEX_EXPORT_ACTIONS,
    ApexApplication,
    ApexDiscovery,
    ApexExportRequest,
    ApexExportRunner,
    ConsoleExportDbReporter,
    ExportDbRequest,
    ExportDbRunner,
    GatewayFactory,
    QueryGateway,
    print_module_banner,
)
from adt_ai.cli.context import (
    DebugQueryGateway,
    _apex_actions,
    _apex_explicit_actions,
    _apex_recent_report_only,
    _apex_scope,
    _app_in_selection,
    _flatten_arg_groups,
    _has_job_recent_conflict,
    _load_startup_context,
    _parse_apex_app_selection,
    _parse_apex_export_filter_groups,
    _print_connection_block,
    _print_startup_debug,
)
from adt_ai.cli.export_apex_messages import (
    print_apex_app_not_found,
    print_apex_owner_not_configured,
)
from adt_ai.cli.export_apex_owners import (
    _apex_reveal_connection_schema,
    _resolve_apex_app_owners,
    _resolve_apex_metadata_owners,
)
from adt_ai.cli.export_reporters import ConsoleApexRevealReporter
from adt_ai.cli.gateways import build_gateway
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.export_apex.deep import ApexDeepFilterError
from adt_ai.export_db.config import AuthorFilterError, resolve_author_filter
from adt_ai.export_db.files import ObjectFileResolver
from adt_ai.export_db.groups import (
    GroupRules,
    build_prefix_rules,
    detect_groups_by_prefix,
    execute_group_move,
    parse_group_prefixes,
    plan_group_moves,
    resolve_group_inputs,
)
from adt_ai.shared import git_identity
from adt_ai.shared.object_types import normalize_object_type_patterns


def _run_export_db(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    handler_started_at = time.monotonic()
    print_module_banner("EXPORT_DB")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    schemas = (
        connections.expand_schemas(_flatten_arg_groups(args.schema), environment=environment)
        if args.schema
        else connections.default_schemas(environment)
    )
    schema_connections = {
        schema: connections.resolve(environment=environment, schema=schema)
        for schema in schemas
    }
    schema_export = {
        schema: schema_connections[schema].export
        for schema in schemas
    }
    if args.groups is not None:
        # -groups is a MOVE action: reorganize already-exported files into
        # <object_type>/<group>/ subfolders. It never connects or exports.
        if args.debug:
            _print_startup_debug(startup)
        return _run_groups_move(args, root, config, schemas)
    # -type resolves onto Oracle's vocabulary at the edge, as recompile does. -name is
    # an identifier pattern: its underscores are real wildcards, so it is left alone.
    flattened_types = _flatten_arg_groups(args.type)
    object_types = (
        normalize_object_type_patterns(flattened_types) if flattened_types else flattened_types
    )
    object_names = _flatten_arg_groups(args.name)
    if args.debug:
        _print_startup_debug(startup)
    if _has_job_recent_conflict(args.recent, object_types or []):
        print(
            "export_db: JOB objects cannot be exported with -recent; "
            "export jobs separately with -type JOB.",
            file=sys.stderr,
        )
        return 2
    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, schema_connections[schema])

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def cached_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = selected_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[schema]

    group_rules = resolve_group_inputs(startup.config_search_paths)

    try:
        changed_by, my_changes, authors = resolve_author_filter(
            args.by, args.my, config, startup.config_search_paths
        )
    except AuthorFilterError as error:
        print(str(error), file=sys.stderr)
        return 2

    runner = ExportDbRunner(cached_gateway_factory)

    def run_one(schema: str) -> int:
        _print_connection_block(
            cached_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )
        runner.run(
            ExportDbRequest(
                root          = root,
                schemas       = [schema],
                config        = config,
                schema_export = {schema: schema_export[schema]},
                object_types  = object_types,
                names         = object_names,
                recent        = args.recent,
                environment   = environment,
                clean         = args.delete,
                dry_run       = args.dry_run,
                reporter      = ConsoleExportDbReporter(silent=args.silent),
                group_rules   = group_rules,
                changed_by    = changed_by,
                my_changes    = my_changes,
                authors       = authors,
            )
        )
        return 0

    return run_schema_sections(schemas, run_one, first_started_at=handler_started_at)


def _confirm_groups_move() -> bool:
    """Prompt the user before any file is moved; default to no on EOF/empty."""
    try:
        answer = input("Proceed with these moves? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def _run_groups_move(
    args: argparse.Namespace,
    root: object,
    config: Mapping[str, object],
    schemas: list[str],
) -> int:
    """Reorganize already-exported object files into <object_type>/<group>/ folders.

    Explicit prefixes (`-groups ABC DEF`) route only those prefixes; bare `-groups`
    auto-detects groups per object type using ``groups_min``. The plan is previewed,
    then gated behind a confirmation; ``-dry-run`` previews without prompting or
    moving. Group folder names are always uppercased.
    """
    resolver = ObjectFileResolver.from_config(root=root, config=config)
    prefixes = parse_group_prefixes(args.groups)
    if prefixes:
        rules = build_prefix_rules(prefixes)
    else:
        groups_min = int(config.get("groups_min", 5))
        type_rules: dict[str, dict[str, str]] = {}
        for object_type, names in resolver.flat_object_names(schemas).items():
            detected = detect_groups_by_prefix(names, groups_min)
            if detected:
                type_rules[object_type.upper()] = detected
        rules = GroupRules(type_rules=type_rules)
    plan = plan_group_moves(resolver.iter_type_roots(schemas), rules)
    return execute_group_move(
        plan,
        dry_run=args.dry_run,
        confirm=_confirm_groups_move,
        emit=print,
    )


def _run_export_apex(
    args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    handler_started_at = time.monotonic()
    print_module_banner("EXPORT_APEX")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    try:
        app_selection = _parse_apex_app_selection(_flatten_arg_groups(args.app))
        page_selection, component_filters = _parse_apex_export_filter_groups(
            args.page, args.component
        )
        if args.deep and page_selection is None:
            raise ValueError("-deep requires -page")
    except ValueError as exc:
        print(f"export_apex: {exc}", file=sys.stderr)
        return 2
    has_app_ranges = bool(app_selection and app_selection.has_ranges)
    sql_app_ids = None if has_app_ranges else _flatten_arg_groups(args.app)
    schema_app_ids: dict[str, list[str]] = {}
    if args.schema:
        schemas = connections.expand_schemas(
            _flatten_arg_groups(args.schema), environment=environment
        )
        connection_schema = schemas[0]
    elif args.reveal:
        schemas = connections.schema_names(environment)
        connection_schema = _apex_reveal_connection_schema(connections, environment, schemas)
    else:
        default_schemas = connections.default_schemas(environment, kind="apex")
        default_schema = default_schemas[0]
        schemas = list(default_schemas)
        connection_schema = default_schema
        owner_routes = _resolve_apex_metadata_owners(
            root,
            sql_app_ids or [],
            default_schema,
            connections.schema_names(environment),
        )
        if owner_routes:
            routed = {app_id for ids in owner_routes.values() for app_id in ids}
            remaining = [app_id for app_id in sql_app_ids if app_id not in routed]
            schemas = list(owner_routes)
            schema_app_ids.update(owner_routes)
            if remaining:
                schemas.append(default_schema)
                schema_app_ids[default_schema] = remaining
                connection_schema = default_schema
            else:
                connection_schema = schemas[0]
    schema_connections = {
        schema: connections.resolve(environment=environment, schema=schema, kind="apex")
        for schema in schemas
    }
    schema_scope = {
        schema: _apex_scope(
            schema_connections[schema].apex,
            workspace = args.ws,
            group     = args.group,
            app_ids   = schema_app_ids.get(schema, sql_app_ids),
        )
        for schema in schemas
    }
    actions = _apex_actions(args, config)
    explicit_actions = _apex_explicit_actions(args)
    recent_days = args.recent
    recent_report_only = _apex_recent_report_only(args, actions, recent_days)
    my_name, my_email = git_identity.current_git_identity() if args.my else (None, None)
    if args.debug:
        _print_startup_debug(startup)
    if not args.reveal and not any(actions.values()) and not recent_report_only:
        _print_missing_apex_format_guidance()
        return 2

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(
            startup,
            schema_connections[connection_schema if args.reveal else schema],
            project_root=root,
        )

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def export_apex_gateway_factory(schema: str) -> QueryGateway:
        gateway_schema = connection_schema if args.reveal else schema
        if gateway_schema not in gateway_cache:
            gateway = selected_gateway_factory(gateway_schema)
            gateway_cache[gateway_schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[gateway_schema]

    reporter = ConsoleApexRevealReporter()
    applications_by_schema: dict[str, list[ApexApplication]] = {}

    if args.reveal:
        # -reveal is a single cross-schema inventory screen, not a per-schema
        # export — one connection, one shared teardown footer, untouched by
        # the per-schema segmenting below.
        _print_connection_block(
            export_apex_gateway_factory(connection_schema),
            schema_connections[connection_schema],
            debug=args.debug,
        )
        for schema in schemas:
            discovery = ApexDiscovery(export_apex_gateway_factory(schema))
            scope = schema_scope[schema]
            applications = discovery.applications(
                owner     = schema,
                workspace = scope["workspace"],
                group     = scope["group"],
                app_ids   = scope["app_ids"],
                recent_days = recent_days,
                max_app_id = args.max_app_id,
            )
            if has_app_ranges:
                applications = [
                    application
                    for application in applications
                    if _app_in_selection(application.app_id, app_selection)
                ]
            applications_by_schema[schema] = applications
        discovery = ApexDiscovery(export_apex_gateway_factory(connection_schema))
        workspace = schema_scope[connection_schema]["workspace"]
        is_filtered = bool(args.app) or bool(args.schema)
        active_workspaces = {
            app.workspace
            for apps in applications_by_schema.values()
            for app in apps
        }
        schema_filter = None if is_filtered else schemas
        all_workspaces = discovery.workspaces(
            workspace=workspace, schemas=schema_filter, max_app_id=args.max_app_id
        )
        reporter.workspaces(
            [w for w in all_workspaces if w.workspace in active_workspaces]
            if (is_filtered and active_workspaces) else all_workspaces
        )
        owner_filter = None if args.owners else schemas
        all_owner_counts = discovery.owner_app_counts(owner_filter, max_app_id=args.max_app_id)
        active_owners = {s for s, apps in applications_by_schema.items() if apps}
        reporter.owner_counts(
            [oc for oc in all_owner_counts if oc.owner in active_owners]
            if (is_filtered and active_owners) else all_owner_counts
        )
        for schema in schemas:
            reporter.applications(schema, applications_by_schema[schema])
        return 0

    # -- export mode: each schema is its own console segment (connection block
    # -> discovery -> export -> TIMER), driven by the shared per-schema-section
    # helper. `schemas` is mutated in place by the missing-app owner routing
    # below, and the helper's lazy iteration picks up whatever gets appended.
    initial_schema_count = len(schemas)
    processed = 0

    def run_one(schema: str) -> int:
        nonlocal processed
        processed += 1
        versions = _print_connection_block(
            export_apex_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )
        discovery = ApexDiscovery(export_apex_gateway_factory(schema))
        scope = schema_scope[schema]
        applications = discovery.applications(
            owner     = schema,
            workspace = scope["workspace"],
            group     = scope["group"],
            app_ids   = scope["app_ids"],
            recent_days = None,
            max_app_id = args.max_app_id,
        )
        if has_app_ranges:
            applications = [
                application
                for application in applications
                if _app_in_selection(application.app_id, app_selection)
            ]
        applications_by_schema[schema] = applications
        reporter.applications(schema, applications)

        # Missing-app owner routing runs once, inside the last originally
        # requested schema's segment, after its own export/before its timer.
        # A newly-routed owner schema is appended to `schemas` and gets its
        # own full segment (connection block, discovery, export, timer) the
        # next time the helper's loop reaches it — it is never spliced into
        # an already-completed segment.
        if processed == initial_schema_count and not has_app_ranges:
            requested_app_ids = _flatten_arg_groups(args.app)
            if requested_app_ids:
                found_ids = {
                    str(application.app_id)
                    for apps in applications_by_schema.values()
                    for application in apps
                }
                missing_app_ids = [
                    app_id for app_id in requested_app_ids if str(app_id) not in found_ids
                ]
                if missing_app_ids:
                    owner_discovery = ApexDiscovery(export_apex_gateway_factory(schema))
                    owner_to_app_ids, not_configured, not_found = _resolve_apex_app_owners(
                        owner_discovery,
                        missing_app_ids,
                        connections.schema_names(environment),
                    )
                    for owner_schema, owner_app_ids in owner_to_app_ids.items():
                        connection = schema_connections.get(owner_schema)
                        if connection is None:
                            connection = connections.resolve(
                                environment=environment, schema=owner_schema, kind="apex"
                            )
                            schema_connections[owner_schema] = connection
                        schema_scope[owner_schema] = _apex_scope(
                            connection.apex,
                            workspace = args.ws,
                            group     = args.group,
                            app_ids   = owner_app_ids,
                        )
                        schemas.append(owner_schema)
                    for app_id, owner in not_configured:
                        print_apex_owner_not_configured(app_id, owner, environment)
                    for app_id in not_found:
                        print_apex_app_not_found(app_id)

        if any(actions.values()) or recent_report_only:
            try:
                ApexExportRunner(export_apex_gateway_factory).run(
                    ApexExportRequest(
                        root         = root,
                        schemas      = [schema],
                        applications = {schema: applications_by_schema[schema]},
                        actions      = actions,
                        explicit_actions = explicit_actions,
                        config       = config,
                        release      = args.release,
                        recent       = recent_days,
                        environment  = environment,
                        changed_by   = args.by or None,
                        my_changes   = args.my,
                        my_name      = my_name,
                        my_email     = my_email,
                        recent_report_only=recent_report_only,
                        page_selection=page_selection,
                        component_filters=component_filters,
                        deep=args.deep,
                        # Already probed by the connection block above — the 26.1
                        # format gates read it rather than asking the DB again.
                        apex_version=versions.get("APEX"),
                    )
                )
            except ApexDeepFilterError as exc:
                print(f"export_apex: {exc}", file=sys.stderr)
                return 2
        return 0

    return run_schema_sections(schemas, run_one, first_started_at=handler_started_at)


def _print_missing_apex_format_guidance() -> None:
    formats = ", ".join(["-all", *(f"-{action}" for action in APEX_EXPORT_ACTIONS)])
    print("Use -reveal to list workspaces and applications without exporting.")
    print()
    print("To export app(s) pass application number(s) and format.")
    print(f"Available formats: {formats}")
    print("Example: adtai export_apex -app 1000 -readable")
    print()


__all__ = [name for name in globals() if not name.startswith("__")]
