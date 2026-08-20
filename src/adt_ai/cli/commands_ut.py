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
from adt_ai.ut.grouping import gated_packages
from adt_ai.ut.limits import error_limit, packages_below, resolve_gate
from adt_ai.ut.naming import UtNaming
from adt_ai.ut.render import (
    print_coverage_gate,
    print_module_summary,
    print_summary_rows,
)
from adt_ai.ut.reporter import ConsoleUt3Reporter
from adt_ai.ut.runner import Ut3Request, Ut3Runner
from adt_ai.ut.store import record_run, run_history
from adt_ai.ut.timers import previous_seconds, record_seconds, timers_path, variant_key


def _run_ut3(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    handler_started_at = time.monotonic()
    print_module_banner("UT")
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
        return _run_ut_for_schema(
            args, startup, environment, schema, gateway_factory, naming
        )

    return run_schema_sections(schemas, run_one, first_started_at=handler_started_at)


def _run_ut_for_schema(
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
    # own history, see `ut/timers.py`.
    timers_file = timers_path(startup.root)
    variant = variant_key(names)

    # The reporter owns the screen from the first thing that blocks to the
    # summary heading: the mode's own header ahead of discovery, the dotted bar
    # or the per-test rows through the suites, then the problem stanzas and
    # `SUMMARY PER SUITE:` ahead of the coverage read (`#379`). `names` reaches
    # it because that first heading is where the run states its filter,
    # `RUNNING TESTS FOR <PATTERNS>:`, and `error_limit` because the stanzas it
    # prints are capped.
    #
    # `started_at` is taken here rather than inside the bar so the countdown and
    # the figure recorded below are measured from one origin, the run the bar
    # estimates begins with discovery, which happens before the bar exists.
    started_at = time.monotonic()
    reporter = ConsoleUt3Reporter(
        silent            = args.silent,
        verbose           = args.verbose,
        names             = names,
        previous_seconds  = previous_seconds(timers_file, owner, variant),
        started_at        = started_at,
        error_limit       = error_limit(startup.config),
        # **Read before the run records itself.** `record_run` below appends this
        # run, so reading afterwards would compare the run against itself and
        # every delta would be zero, which looks exactly like a stable schema
        # (`#251`).
        #
        # Keyed by `variant`, the same `-name` selection the timers above are
        # keyed by: a filtered run measures only the packages it selected, so
        # standing it in front of a full run reports every other package as
        # having no previous figure (`#436`).
        history           = run_history(startup.root, owner, variant),
    )
    try:
        result = Ut3Runner(gateway, reporter=reporter).run(
            Ut3Request(
                owner   = owner,
                names   = names,
                refresh = args.refresh,
                naming  = naming or UtNaming(),
            )
        )
    except BaseException:
        # **A row the run left open is completed before the error banner, not
        # after.** The banner starts with its own blank line, which on a bare
        # label is spent terminating that label instead of spacing the banner,
        # so the failure lands welded to a row that still reads as running
        # (`#232`). The close is idempotent, which is why the happy path below
        # can call it too.
        reporter.close()
        raise

    # Idempotent, and normally already done: `measuring_coverage` ends the bar
    # the moment the last suite returns. This is what closes it on a run that
    # never got that far.
    reporter.close()

    if result.timings:
        # **Only a run that executed something is recorded.** A schema whose
        # suites all stopped compiling finishes in no time at all, and storing
        # that would seed `0:00:00` into the next real run's countdown.
        record_seconds(timers_file, owner, variant, time.monotonic() - started_at)

    # The rows under the heading the reporter laid down before the coverage
    # read, which is the one part of the report that had to wait for it. Then
    # the same run grouped per module when `ut_module` is configured. Neither
    # heading carries `names`: they say what they group, and the section the run
    # happened under said what it covered.
    print_summary_rows(result)
    if result.modules:
        print_module_summary(result)

    # **Recorded whether or not this run printed a change table.** The table is
    # `-verbose`, the history is not: a quiet run still moves the figure, and a
    # store that only remembered verbose runs would compare against whenever
    # somebody last passed the flag rather than against last time (`#251`).
    if result.coverage.packages:
        record_run(startup.root, owner, result.coverage.packages, variant=variant)

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
