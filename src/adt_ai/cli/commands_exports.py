from __future__ import annotations

import argparse
import sys
import time

from adt_ai.cli.commands_export_db_groups import run_groups_move
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
    resolve_apex_owner_routes,
)
from adt_ai.cli.export_apex_reveal import print_reveal_screen
from adt_ai.cli.export_reporters import ConsoleApexRevealReporter
from adt_ai.cli.gateways import build_gateway
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.export_apex.deep import ApexDeepFilterError
from adt_ai.export_apex.schema_level import schema_level_only
from adt_ai.export_db.config import AuthorFilterError, resolve_author_filter
from adt_ai.export_db.groups import resolve_group_inputs
from adt_ai.shared import identity
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
        return run_groups_move(args, root, config, schemas)
    if args.force:
        # -force applies a -groups plan and means nothing on an export. Accepting
        # it here would be the accepted-but-unused flag §Command surface bans.
        print(
            "export_db: -force applies a -groups plan; add -groups, "
            "or drop -force to export.",
            file=sys.stderr,
        )
        return 2
    # -type resolves onto Oracle's vocabulary at the edge, as recompile does. -name is
    # an identifier pattern: its underscores are real wildcards, so it is left alone.
    flattened_types = _flatten_arg_groups(args.type)
    object_types = (
        normalize_object_type_patterns(flattened_types) if flattened_types else flattened_types
    )
    object_names = _flatten_arg_groups(args.name)
    if args.debug:
        _print_startup_debug(startup)
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
                reporter      = ConsoleExportDbReporter(
                    silent  = args.silent,
                    compact = args.compact,
                ),
                group_rules   = group_rules,
                changed_by    = changed_by,
                my_changes    = my_changes,
                authors       = authors,
                baseline      = False,
            )
        )
        return 0

    exit_code = run_schema_sections(schemas, run_one, first_started_at=handler_started_at)
    return exit_code


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
        owner_routes = resolve_apex_owner_routes(
            root, connections, environment, sql_app_ids, kind="apex"
        )
        default_schema = owner_routes.default_schemas[0]
        schemas = list(owner_routes.default_schemas)
        connection_schema = default_schema
        if owner_routes.routes:
            schemas = list(owner_routes.routes)
            schema_app_ids.update(owner_routes.routes)
            if owner_routes.unrouted:
                schemas.append(default_schema)
                schema_app_ids[default_schema] = owner_routes.unrouted
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
    # `IDENTITY.yaml` first, git as the fallback (ADT #469). `apex_account` is the
    # name half here rather than `git config user.name`, and it is the key that
    # makes this work at all on a workspace whose developer logins are
    # `FIRST.LAST` rather than addresses; it had been documented and unread since
    # the file was introduced.
    my_name, my_email = (
        identity.resolve_commit_identity(startup.config_search_paths, root)
        if args.my
        else (None, None)
    )
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
        # export, one connection, one shared teardown footer, untouched by
        # the per-schema segmenting below.
        _print_connection_block(
            export_apex_gateway_factory(connection_schema),
            schema_connections[connection_schema],
            debug=args.debug,
        )
        # The first of the three tables opens its section before any of them is
        # read, so the screen names the inventory being gathered instead of
        # parking on the connection block (`#372`).
        reporter.begin_workspaces()
        # -reveal is an inventory screen, so only an explicit -ws narrows it
        # (`#564`, Jan: "-reveal should always reveal all workspaces and apps,
        # UNLESS -ws is passed"). `apex.workspace` still scopes the per-schema
        # EXPORT below; here it only marks the ACTIVE row, because a wrong value
        # in that key used to filter both reads to nothing and leave the screen
        # empty at exit 0. `-group` and `-app` keep reading the file either way.
        reveal_workspace = args.ws or None
        configured_workspace = _apex_scope(
            schema_connections[connection_schema].apex
        ).workspace
        for schema in schemas:
            discovery = ApexDiscovery(export_apex_gateway_factory(schema))
            scope = schema_scope[schema]
            applications = discovery.applications(
                owner     = schema,
                workspace = reveal_workspace,
                group     = scope.group,
                app_ids   = scope.app_ids,
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
        print_reveal_screen(
            ApexDiscovery(export_apex_gateway_factory(connection_schema)),
            reporter,
            schemas,
            applications_by_schema,
            workspace            = reveal_workspace,
            configured_workspace = configured_workspace,
            is_filtered          = bool(args.app) or bool(args.schema),
            widen_owner_counts   = bool(args.owners),
            max_app_id           = args.max_app_id,
        )
        return 0

    # Export mode: each schema is its own console segment (connection block
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
            workspace = scope.workspace,
            group     = scope.group,
            app_ids   = scope.app_ids,
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
        # Exports no application, so it lists none (`schema_level_only`).
        if not schema_level_only(actions):
            reporter.applications(schema, applications)

        # Missing-app owner routing runs once, inside the last originally
        # requested schema's segment, after its own export/before its timer.
        # A newly-routed owner schema is appended to `schemas` and gets its
        # own full segment (connection block, discovery, export, timer) the
        # next time the helper's loop reaches it, it is never spliced into
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
                        # Already probed by the connection block above, the 26.1
                        # format gates read it rather than asking the DB again.
                        apex_version=versions.get("APEX"),
                        compact=args.compact,
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
