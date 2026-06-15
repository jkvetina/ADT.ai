from __future__ import annotations

from adt_ai.cli_context import *

def _run_export_db(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_DB")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    schemas = (
        connections.expand_schemas(args.schema, environment=environment)
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
    config = _with_schema_folders(config, schema_export)
    object_types = _flatten_arg_groups(args.type)
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
        return OracleGateway(schema_connections[schema], startup_sql=startup.startup_sql)

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def cached_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = selected_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[schema]

    for schema in schemas:
        _print_connection_block(
            cached_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )

    runner = ExportDbRunner(cached_gateway_factory)
    runner.run(
        ExportDbRequest(
            root          = root,
            schemas       = schemas,
            config        = config,
            schema_export = schema_export,
            object_types  = object_types,
            names         = object_names,
            recent_days   = args.recent,
            clean         = args.delete,
            dry_run       = args.dry_run,
            reporter      = ConsoleExportDbReporter(silent=args.silent),
        )
    )
    return 0


def _run_export_data(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_DATA")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    schemas = (
        connections.expand_schemas(args.schema, environment=environment)
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
    if args.debug:
        _print_startup_debug(startup)

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(schema_connections[schema], startup_sql=startup.startup_sql)

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def export_data_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = selected_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[schema]

    for schema in schemas:
        _print_connection_block(
            export_data_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )

    runner = ExportDataRunner(export_data_gateway_factory)
    runner.run(
        ExportDataRequest(
            root          = root,
            schemas       = schemas,
            config        = config,
            schema_export = schema_export,
            names         = _flatten_arg_groups(args.name),
            reporter      = ConsoleExportDataReporter(),
        )
    )
    return 0


def _run_export_apex(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_APEX")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    if args.schema:
        schemas = connections.expand_schemas(args.schema, environment=environment)
        connection_schema = schemas[0]
    elif args.reveal:
        schemas = connections.schema_names(environment)
        connection_schema = _apex_reveal_connection_schema(connections, environment, schemas)
    else:
        schemas = connections.default_schemas(environment, kind="apex")
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
            app_ids   = _flatten_arg_groups(args.app),
        )
        for schema in schemas
    }
    actions = _apex_actions(args, config)
    recent_days = _apex_recent_days(args.recent, config)
    if args.debug:
        _print_startup_debug(startup)
    if not args.reveal and not any(actions.values()):
        _print_missing_apex_format_guidance()
        return 2

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(
            schema_connections[connection_schema if args.reveal else schema],
            project_root=root,
            startup_sql=startup.startup_sql,
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

    # Reveal/listing reads apex_applications, which is scoped by the parsing
    # schema's workspace association (and the owner/workspace filters in SQL),
    # not by the APEX security context. The context is set per-app at export
    # time (EXPORT_START_QUERY), so listing needs no workspace switch here.
    if args.reveal:
        _print_connection_block(
            export_apex_gateway_factory(connection_schema),
            schema_connections[connection_schema],
            debug=args.debug,
        )
    for schema in schemas:
        if not args.reveal:
            _print_connection_block(
                export_apex_gateway_factory(schema), schema_connections[schema], debug=args.debug
            )
        discovery = ApexDiscovery(export_apex_gateway_factory(schema))
        scope = schema_scope[schema]
        applications = discovery.applications(
            owner     = schema,
            workspace = scope["workspace"],
            group     = scope["group"],
            app_ids   = scope["app_ids"],
            recent_days = recent_days if args.reveal else None,
            max_app_id = args.max_app_id,
        )
        applications_by_schema[schema] = applications
        if not args.reveal:
            reporter.applications(schema, applications)
    if args.reveal:
        discovery = ApexDiscovery(export_apex_gateway_factory(connection_schema))
        workspace = schema_scope[connection_schema]["workspace"]
        is_filtered = bool(args.app) or bool(args.schema)
        active_workspaces = {
            app.workspace
            for apps in applications_by_schema.values()
            for app in apps
        }
        schema_filter = None if is_filtered else schemas
        all_workspaces = discovery.workspaces(workspace=workspace, schemas=schema_filter, max_app_id=args.max_app_id)
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
    if not args.reveal:
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
                owner_discovery = ApexDiscovery(export_apex_gateway_factory(connection_schema))
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
                        _print_connection_block(
                            export_apex_gateway_factory(owner_schema),
                            connection,
                            debug=args.debug,
                        )
                        schemas.append(owner_schema)
                        applications_by_schema.setdefault(owner_schema, [])
                    scope = _apex_scope(
                        connection.apex,
                        workspace = args.ws,
                        group     = args.group,
                        app_ids   = owner_app_ids,
                    )
                    schema_scope[owner_schema] = scope
                    discovery = ApexDiscovery(export_apex_gateway_factory(owner_schema))
                    found = discovery.applications(
                        owner      = owner_schema,
                        workspace  = scope["workspace"],
                        group      = scope["group"],
                        app_ids    = scope["app_ids"],
                        max_app_id = args.max_app_id,
                    )
                    existing = {app.app_id for app in applications_by_schema[owner_schema]}
                    new_apps = [app for app in found if app.app_id not in existing]
                    applications_by_schema[owner_schema].extend(new_apps)
                    if new_apps:
                        reporter.applications(owner_schema, new_apps)
                for app_id, owner in not_configured:
                    _print_apex_owner_not_configured(app_id, owner, environment)
                for app_id in not_found:
                    _print_apex_app_not_found(app_id)
    if not args.reveal and any(actions.values()):
        ApexExportRunner(export_apex_gateway_factory).run(
            ApexExportRequest(
                root         = root,
                schemas      = schemas,
                applications = applications_by_schema,
                actions      = actions,
                config       = config,
                release      = args.release,
                recent_days  = recent_days,
                changed_by   = args.by or None,
            )
        )
    return 0


def _apex_reveal_connection_schema(
    connections: ConnectionResult,
    environment: str,
    schemas: list[str],
) -> str:
    try:
        default_schemas = connections.default_schemas(environment, kind="apex")
    except ConnectionConfigError:
        default_schemas = []
    return default_schemas[0] if default_schemas else schemas[0]


def _apex_app_id_value(app_id: str | int) -> str | int:
    try:
        return int(app_id)
    except (TypeError, ValueError):
        return app_id


def _resolve_apex_app_owners(
    discovery: ApexDiscovery,
    missing_app_ids: list[str],
    schema_names: list[str],
) -> tuple[dict[str, list[str]], list[tuple[str, str]], list[str]]:
    """Look up the owner schema for each app missing from the scanned schemas.

    Returns the apps grouped by configured owner schema, the apps whose owner is
    not a configured schema (with that owner), and the apps not found anywhere.
    """
    schema_lookup = {name.upper(): name for name in schema_names}
    owner_to_app_ids: dict[str, list[str]] = {}
    not_configured: list[tuple[str, str]] = []
    not_found: list[str] = []
    for app_id in missing_app_ids:
        owner = discovery.application_owner(_apex_app_id_value(app_id))
        if not owner:
            not_found.append(app_id)
            continue
        owner_schema = schema_lookup.get(owner.upper())
        if owner_schema is None:
            not_configured.append((app_id, owner))
            continue
        owner_to_app_ids.setdefault(owner_schema, []).append(app_id)
    return owner_to_app_ids, not_configured, not_found


def _print_apex_owner_not_configured(app_id: str, owner: str, environment: str) -> None:
    print()
    print(
        f"APP {app_id} is owned by schema {owner}, which is not configured "
        f"for environment {environment}."
    )
    print(f"Add {owner} to your connections to export it; skipping APP {app_id}.")


def _print_apex_app_not_found(app_id: str) -> None:
    print()
    print(f"APP {app_id} was not found in any configured APEX schema.")


def _apex_recent_days(argument: int | None, _config: Mapping[str, object]) -> int | None:
    return argument


def _print_missing_apex_format_guidance() -> None:
    formats = ", ".join(["-all", *(f"-{action}" for action in APEX_EXPORT_ACTIONS)])
    print("Use -reveal to list workspaces and applications without exporting.")
    print()
    print("To export app(s) pass application number(s) and format.")
    print(f"Available formats: {formats}")
    print("Example: adtai export_apex -app 1000 -readable")
    print()


class ConsoleApexRevealReporter:
    def workspaces(self, workspaces: list[ApexWorkspace]) -> None:
        print_adt_header("WORKSPACES:")
        print_adt_table(
            [
                {
                    "workspace": workspace.workspace,
                    "workspace_id": workspace.workspace_id,
                    "owners": workspace.owners,
                    "applications": workspace.applications,
                    "developers": workspace.developers,
                }
                for workspace in workspaces
            ]
        )

    def owner_counts(self, owner_counts: list[ApexOwnerCount]) -> None:
        if not owner_counts:
            return
        print_adt_header("APPLICATIONS PER LISTED OWNERS:")
        print_adt_table(
            [
                {
                    "owner": owner_count.owner,
                    "applications": owner_count.applications,
                }
                for owner_count in owner_counts
            ]
        )

    def applications(self, schema: str, applications: list[ApexApplication]) -> None:
        if not applications:
            return
        workspace = applications[0].workspace
        print_adt_header("APEX APPLICATIONS:", f"{workspace} | {schema}")
        print_adt_table(
            [
                {
                    "app_id": application.app_id,
                    "name": _truncate_console_value(application.app_name, 40),
                    "pages": application.pages,
                    "updated_at": application.updated_at,
                }
                for application in applications
            ],
            min_widths={"name": 40},
        )


def _truncate_console_value(value: object, width: int) -> str:
    text = str(value or "")
    if len(text) <= width:
        return text
    return f"{text[:width - 2]}.."


class ConsoleExportDataReporter:
    line_width = 78

    def start_export(self, total: int) -> None:
        print_adt_header("EXPORT TABLE DATA:", f"({total})")

    def export_table(self, table: DataTable) -> None:
        print(f"  - {table.name.upper()}", end="", flush=True)

    def finish_table(self, table: DataTable, row_count: int) -> None:
        left = f"  - {table.name.upper()}"
        right = str(row_count)
        dot_count = max(1, self.line_width - len(left) - len(right) - 2)
        print(f" {'.' * dot_count} {right}")

    def finish_export(self) -> None:
        return None

__all__ = [name for name in globals() if not name.startswith("__")]
