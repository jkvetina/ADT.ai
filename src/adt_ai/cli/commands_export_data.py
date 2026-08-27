from __future__ import annotations

import argparse
import sys
import time

from adt_ai.cli.commands_export_data_groups import run_data_groups_move
from adt_ai.cli.constants import (
    ExportDataRequest,
    ExportDataRunner,
    GatewayFactory,
    QueryGateway,
    print_module_banner,
)
from adt_ai.cli.context import (
    DebugQueryGateway,
    _flatten_arg_groups,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
)
from adt_ai.cli.export_reporters import ConsoleExportDataReporter
from adt_ai.cli.gateways import build_gateway
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.export_data.groups import resolve_group_inputs


def _run_export_data(
    args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    handler_started_at = time.monotonic()
    print_module_banner("EXPORT_DATA")
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
        # -groups is a MOVE action: reorganize already-exported table files into
        # data/<group>/ subfolders. It never connects or exports.
        if args.debug:
            _print_startup_debug(startup)
        return run_data_groups_move(args, root, config, schemas)
    if args.force:
        # -force applies a -groups plan and means nothing on an export. Accepting
        # it here would be the accepted-but-unused flag §Command surface bans.
        print(
            "export_data: -force applies a -groups plan; add -groups, "
            "or drop -force to export.",
            file=sys.stderr,
        )
        return 2
    if args.debug:
        _print_startup_debug(startup)

    group_rules = resolve_group_inputs(startup.config_search_paths)
    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, schema_connections[schema])

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def export_data_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = selected_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[schema]

    runner = ExportDataRunner(export_data_gateway_factory)

    def run_one(schema: str) -> int:
        _print_connection_block(
            export_data_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )
        runner.run(
            ExportDataRequest(
                root          = root,
                schemas       = [schema],
                config        = config,
                schema_export = {schema: schema_export[schema]},
                names         = _flatten_arg_groups(args.name),
                group_rules   = group_rules,
                reporter      = ConsoleExportDataReporter(silent=args.silent),
            )
        )
        return 0

    return run_schema_sections(schemas, run_one, first_started_at=handler_started_at)
