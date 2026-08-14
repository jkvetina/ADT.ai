from __future__ import annotations

import argparse
import time

from adt_ai.cli.constants import (
    GatewayFactory,
    print_module_banner,
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
from adt_ai.ut3.grouping import gated_packages
from adt_ai.ut3.limits import error_limit, packages_below, resolve_gate
from adt_ai.ut3.naming import UtNaming
from adt_ai.ut3.problems import print_problems
from adt_ai.ut3.render import (
    ConsoleUt3Reporter,
    print_coverage_gate,
    print_module_summary,
    print_results,
    print_summary,
)
from adt_ai.ut3.runner import Ut3Request, Ut3Runner
from adt_ai.ut3.timers import previous_seconds, record_seconds, timers_path, variant_key


def _run_ut3(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    handler_started_at = time.monotonic()
    print_module_banner("UT3")
    startup = _load_startup_context(args)
    connections = startup.connections
    # Built once for the whole command, before the first schema connects: a
    # malformed `ut_pattern` is a configuration failure, and reporting it after
    # a connection banner and a schema header would bury it.
    naming = UtNaming.from_config(startup.config)

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
        return _run_ut3_for_schema(
            args, startup, environment, schema, gateway_factory, naming
        )

    return run_schema_sections(schemas, run_one, first_started_at=handler_started_at)


def _run_ut3_for_schema(
    args: argparse.Namespace,
    startup: StartupContext,
    environment: str,
    schema: str,
    gateway_factory: GatewayFactory | None = None,
    naming: UtNaming | None = None,
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

    # -name is multi-pattern (append + nargs="+"), matching recompile/export_db.
    # It selects the suites to run; the coverage figures then describe whatever
    # those suites reached. No pattern means everything.
    names = tuple(_flatten_arg_groups(args.name) or ())

    # The connected user owns USER_OBJECTS and is what utPLSQL's annotation
    # cache is keyed by; the configured schema key is only its label.
    owner = connection.username or connection.schema or schema

    # What the last run of this schema-and-variant cost, so the progress bar has
    # something to count down from before the first suite returns. `-name`
    # selects the suites that run, so it selects the job being timed and keys its
    # own history, see `ut3/timers.py`.
    timers_file = timers_path(startup.root)
    variant = variant_key(names)

    # The reporter prints the suites roll-up the moment discovery returns, then
    # the mode's own section: the dotted bar by default, the per-test rows under
    # `-verbose`, neither under `-silent`.
    #
    # `started_at` is taken here rather than inside the bar so the countdown and
    # the figure recorded below are measured from one origin, the run the bar
    # estimates begins with discovery, which happens before the bar exists.
    started_at = time.monotonic()
    reporter = ConsoleUt3Reporter(
        silent           = args.silent,
        verbose          = args.verbose,
        previous_seconds = previous_seconds(timers_file, owner, variant),
        started_at       = started_at,
    )
    result = Ut3Runner(gateway, reporter=reporter).run(
        Ut3Request(
            owner   = owner,
            names   = names,
            refresh = args.refresh,
            naming  = naming or UtNaming(),
        )
    )

    # The streamed section closes here, not inside the loop: the bar is one row
    # for the whole run, so the newline that ends it, and the blank that
    # separates the section from the next header, are owed once, at the end.
    reporter.close()

    if result.timings:
        # **Only a run that executed something is recorded.** A schema whose
        # suites all stopped compiling finishes in no time at all, and storing
        # that would seed `0:00:00` into the next real run's countdown.
        record_seconds(timers_file, owner, variant, time.monotonic() - started_at)

    if args.verbose and not args.silent and not reporter.streamed:
        # Nothing ran, so nothing streamed. The section still prints, empty: a
        # run that found no suite reports it in the same shape as one that did,
        # and the exit code below carries the failure. It belongs to the
        # `-verbose` section, so it follows that section rather than printing a
        # `TEST RESULTS:` header the default mode has no other use for.
        print_results(result)

    # `-silent` never takes out the problem stanzas: it exists to make a green
    # run quiet, not to make a red one unreadable, and a `FAIL` count whose
    # message is reachable only by re-running is not a report. It is capped
    # instead: a schema with hundreds of failing tests printed three thousand
    # lines here and pushed the tables below off the terminal's scrollback.
    print_problems(result, limit=error_limit(startup.config))

    # The run closes on its roll-up, per-suite first, then per module when
    # `ut_module` is configured. `names` reaches the renderer because the header
    # says what the table covers: `SUMMARY FOR <PATTERNS>:` under `-name`.
    print_summary(result, names)
    if result.modules:
        print_module_summary(result)

    # The gate reads the report rather than replacing it: every table above has
    # already printed, and what follows is only the list of packages under the
    # bar. Absent `-gate` nothing is compared and the exit code is the suites'.
    threshold = resolve_gate(args.gate, startup.config)
    below = () if threshold is None else tuple(
        packages_below(gated_packages(result), threshold)
    )
    if below:
        print_coverage_gate(below, threshold)
    return 0 if result.success and not below else 1


__all__ = [name for name in globals() if not name.startswith("__")]
