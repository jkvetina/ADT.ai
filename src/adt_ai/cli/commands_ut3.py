from __future__ import annotations

import argparse
import time

from adt_ai.cli.constants import (
    GatewayFactory,
    print_adt_header,
)
from adt_ai.cli.context import (
    DebugQueryGateway,
    StartupContext,
    _flatten_arg_groups,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
)
from adt_ai.cli.gateways import build_gateway
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.ut3.render import (
    ConsoleUt3Reporter,
    print_problems,
    print_results,
    print_summary,
)
from adt_ai.ut3.runner import Ut3Request, Ut3Runner


def _run_ut3(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    handler_started_at = time.monotonic()
    print_adt_header("APEX DEPLOYMENT TOOL: UT3")
    startup = _load_startup_context(args)
    connections = startup.connections

    environment = args.env or connections.default_environment
    # -schema is repeatable and pattern-aware, the shape export_db and recompile
    # already use: every configured default schema runs when none is named.
    schemas = (
        connections.expand_schemas(_flatten_arg_groups(args.schema), environment=environment)
        if args.schema
        else connections.default_schemas(environment)
    )

    if args.debug:
        _print_startup_debug(startup)

    def run_one(schema: str) -> int:
        return _run_ut3_for_schema(args, startup, environment, schema, gateway_factory)

    return run_schema_sections(schemas, run_one, first_started_at=handler_started_at)


def _run_ut3_for_schema(
    args: argparse.Namespace,
    startup: StartupContext,
    environment: str,
    schema: str,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    connection = startup.connections.resolve(environment=environment, schema=schema)
    gateway = (
        gateway_factory(schema)
        if gateway_factory
        else build_gateway(startup, connection)
    )
    if args.debug:
        gateway = DebugQueryGateway(gateway)
    _print_connection_block(gateway, connection, debug=args.debug)

    # -name is multi-pattern (append + nargs="+"), matching recompile/export_db;
    # no pattern means every _UT package in the schema.
    names = tuple(_flatten_arg_groups(args.name) or ())

    # The connected user owns USER_OBJECTS and is what utPLSQL's annotation
    # cache is keyed by; the configured schema key is only its label.
    owner = connection.username or connection.schema or schema

    # The reporter prints the suites roll-up the moment discovery returns and
    # then each suite's results as it finishes — the whole point of listing them.
    reporter = ConsoleUt3Reporter(silent=args.silent)
    result = Ut3Runner(gateway, reporter=reporter).run(
        Ut3Request(
            owner   = owner,
            names   = names,
            refresh = args.refresh,
        )
    )

    if not args.silent and not reporter.streamed:
        # Nothing ran, so nothing streamed. The section still prints, empty: a
        # run that found no suite reports it in the same shape as one that did,
        # and the exit code below carries the failure.
        print_results(result)

    # `-silent` takes out the two listings — the suites roll-up and the per-test
    # results — and nothing else. The problem stanzas and the summary print on
    # every run: the flag exists to make a green run quiet, not to make a red one
    # unreadable, and a `FAILED` count whose message is only reachable by
    # re-running without the flag is not a report.
    print_problems(result)
    print_summary(result)
    return 0 if result.success else 1


__all__ = [name for name in globals() if not name.startswith("__")]
