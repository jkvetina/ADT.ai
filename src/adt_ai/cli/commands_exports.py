from __future__ import annotations

import argparse
import sys
import time

from adt_ai.cli.commands_export_apex import ApexRun, run_apex_export, run_apex_reveal
from adt_ai.cli.commands_export_db_groups import run_groups_move
from adt_ai.cli.constants import (
    APEX_EXPORT_ACTIONS,
    ConsoleExportDbReporter,
    ExportDbRequest,
    ExportDbRunner,
    GatewayFactory,
    QueryGateway,
    print_module_banner,
)
from adt_ai.cli.context import (
    _apex_actions,
    _apex_explicit_actions,
    _apex_recent_report_only,
    _apex_scope,
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
    _parse_apex_export_filter_groups,
    _print_connection_block,
    _print_startup_debug,
)
from adt_ai.cli.export_apex_owners import (
    apex_lookup_schema,
    resolve_apex_owner_routes,
)
from adt_ai.cli.export_db_baseline import (
    measured_hashes,
    narrowing_flags,
    refusal,
    write_measured_baseline,
)
from adt_ai.cli.gateways import build_gateway, cached_schema_gateway_factory
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.export_db.config import AuthorFilterError, resolve_author_filter
from adt_ai.export_db.groups import resolve_group_inputs
from adt_ai.shared import identity
from adt_ai.shared.object_types import normalize_object_type_patterns


def _run_export_db(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    handler_started_at = time.monotonic()
    print_module_banner("EXPORT_DB")
    measuring = args.baseline is not None
    if measuring:
        refused = narrowing_flags(args)
        if refused:
            # Refused BY NAME and decided from the arguments alone, before any
            # config or connection is read, so the refusal cannot fail for a
            # second reason. A narrowed run would record a PARTIAL baseline that
            # reads on disk exactly like a complete one, and `patch -hash` would
            # then treat every object it never looked at as absent from the
            # target (`#452`).
            print(refusal(refused), file=sys.stderr)
            print(file=sys.stderr)
            return 2
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
    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, schema_connections[schema])

    # Per-schema cache and `-debug` wrap in one shared helper, so the console
    # guard keeps the nesting `build_gateway` documents (`#670`).
    cached_gateway_factory = cached_schema_gateway_factory(
        gateway_factory or default_gateway_factory, debug=args.debug
    )

    group_rules = resolve_group_inputs(startup.config_search_paths)

    try:
        changed_by, my_changes, authors = resolve_author_filter(
            args.by, args.my, config, startup.config_search_paths
        )
    except AuthorFilterError as error:
        print(str(error), file=sys.stderr)
        return 2

    runner = ExportDbRunner(cached_gateway_factory)
    measured: dict[str, str] = {}

    def run_one(schema: str) -> int:
        _print_connection_block(
            cached_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )
        plans = runner.run(
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
                baseline      = measuring,
            )
        )
        if measuring:
            measured.update(measured_hashes(plans, root))
        return 0

    exit_code = run_schema_sections(schemas, run_one, first_started_at=handler_started_at)
    if measuring and exit_code == 0:
        write_measured_baseline(
            root, config, environment, schemas, measured, override=args.baseline
        )
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
        connection_schema = apex_lookup_schema(connections, environment, schemas)
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

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, schema_connections[schema], project_root=root)

    # Per-schema cache and `-debug` wrap in one shared helper, so the console
    # guard keeps the nesting `build_gateway` documents (`#670`).
    schema_gateway = cached_schema_gateway_factory(
        gateway_factory or default_gateway_factory, debug=args.debug
    )

    def export_apex_gateway_factory(schema: str) -> QueryGateway:
        # `-reveal` is one cross-schema screen read over one connection, so every
        # schema it lists resolves to the same gateway.
        return schema_gateway(connection_schema if args.reveal else schema)

    # Everything above resolved ONE set of inputs; the two things this command
    # does with them share nothing else, so each is its own function and the
    # `-reveal` early return that used to sit here is the seam (`#670`).
    run = ApexRun(
        args               = args,
        root               = root,
        config             = config,
        connections        = connections,
        environment        = environment,
        schemas            = schemas,
        schema_connections = schema_connections,
        schema_scope       = schema_scope,
        gateway_factory    = export_apex_gateway_factory,
        connection_schema  = connection_schema,
        app_selection      = app_selection,
        actions            = actions,
        explicit_actions   = explicit_actions,
        recent_days        = recent_days,
        recent_report_only = recent_report_only,
        page_selection     = page_selection,
        component_filters  = component_filters,
        my_name            = my_name,
        my_email           = my_email,
        started_at         = handler_started_at,
    )
    return run_apex_reveal(run) if args.reveal else run_apex_export(run)


def _print_missing_apex_format_guidance() -> None:
    formats = ", ".join(["-all", *(f"-{action}" for action in APEX_EXPORT_ACTIONS)])
    print("Use -reveal to list workspaces and applications without exporting.")
    print()
    print("To export app(s) pass application number(s) and format.")
    print(f"Available formats: {formats}")
    print("Example: adtai export_apex -app 1000 -readable")
    print()


__all__ = [name for name in globals() if not name.startswith("__")]
