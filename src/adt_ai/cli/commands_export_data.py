from __future__ import annotations

import argparse

from adt_ai.cli.constants import (
    ExportDataRequest,
    ExportDataRunner,
    GatewayFactory,
    OracleGateway,
    QueryGateway,
    print_adt_header,
)
from adt_ai.cli.context import (
    DebugQueryGateway,
    _flatten_arg_groups,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
)
from adt_ai.cli.export_reporters import ConsoleExportDataReporter


def _run_export_data(
    args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_DATA")
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
    if args.debug:
        _print_startup_debug(startup)

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(
            schema_connections[schema], startup_sql=startup.startup_sql, config=config
        )

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
            reporter      = ConsoleExportDataReporter(silent=args.silent),
        )
    )
    return 0
