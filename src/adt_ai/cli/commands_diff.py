"""The `diff` command's CLI half (ADT #780).

Split out of `cli/commands_history.py` when that file crossed the 24 KB context
cap, the same budget that split `tests/diff/test_runner.py`. `diff` was the
newest and largest of the four history-shaped commands living there and the only
one with a module of its own behind it, so it is the one that leaves.

What stays in `commands_history.py`: `rebuild`, `search_repo` and `calendar`,
which share the commit store this command never touches.

**A command module belongs in `cli/__init__._EXPORT_MODULES`, inside the `[2:]`
slice `_PATCH_MODULES` is cut from.** That slice is what makes
`monkeypatch.setattr(cli, "DiffRunner", ...)` reach the module that actually
constructs one: a module left off the tuple keeps the real runner while the test
believes it patched it, and the symptom is a unit test that opens a database.
"""

from __future__ import annotations

import argparse

from adt_ai.cli.constants import (
    DiffRequest,
    DiffRunner,
    GatewayFactory,
    QueryGateway,
    print_adt_header,
    print_module_banner,
)
from adt_ai.cli.context import (
    _flatten_arg_groups,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
)
from adt_ai.cli.diff_reporters import report_summary
from adt_ai.cli.gateways import build_gateway, debug_wrapped
from adt_ai.diff.runner import DiffResult, resolve_out
from adt_ai.diff.summary import summarize
from adt_ai.diff.timers import pair_key, previous_seconds, record_seconds, timers_path
from adt_ai.shared.connections import Connection
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.timed_bar import FALLBACK_TARGET_SECONDS, TimedProgressBar


def _run_diff(args: argparse.Namespace, *, gateway_factory: GatewayFactory | None = None) -> int:
    print_module_banner("DIFF")

    # `-target` alone is required (`#773`). `-source` falls back to the
    # connection default environment inside `connections.resolve`, the same
    # fallback `-env` takes on every other command, so the environment you are
    # working in is the one you compare FROM without naming it.
    if not args.target:
        print_adt_error(
            "ARGUMENT INVALID",
            "-target is required: it names the environment to compare against.",
        )
        return exit_code_for("ARGUMENT INVALID")

    startup = _load_startup_context(args)
    root = startup.root
    connections = startup.connections

    # The comparison a person actually runs is one schema across two
    # environments, so the target side falls back to the source schema rather
    # than to the target environment's default (Jan, ADT #763). Naming
    # `-target-schema` is what says the two sides are spelled differently.
    source_schema = getattr(args, "schema", None)
    target_schema = getattr(args, "target_schema", None) or source_schema
    source_conn   = connections.resolve(environment=args.source, schema=source_schema)
    target_conn   = connections.resolve(environment=args.target, schema=target_schema)
    # Under `config/`, beside every other folder ADT.ai generates into a project
    # (`commits/`, `discovery/`, `internal/`, `temp/`). It used to default to
    # `<root>/diff_output`, a folder invented at the project root, which is the
    # one place a user keeps their own tree (Jan, ADT #763).
    artifact_out, artifact_name = resolve_out(args.out, root)

    def _diff_gateway(connection: Connection) -> QueryGateway:
        if gateway_factory:
            gateway = gateway_factory(connection.schema)
        else:
            gateway = build_gateway(startup, connection)
        # Shared wrap, so the console guard keeps the nesting `build_gateway`
        # documents (`#670`).
        return debug_wrapped(gateway, debug=args.debug)

    for connection in (source_conn, target_conn):
        _print_connection_block(_diff_gateway(connection), connection, debug=args.debug)

    if args.debug:
        _print_startup_debug(startup)

    # Read once and handed to both halves. The export narrows on them (`#780`)
    # and the summary narrows on the artifact that comes back, so they have to be
    # the same strings or a run would zip one set and print another.
    object_types = tuple(_flatten_arg_groups(args.type) or ())
    object_names = tuple(_flatten_arg_groups(args.name) or ())

    request = DiffRequest(
        source            = source_conn,
        target            = target_conn,
        artifact_out      = artifact_out,
        artifact_name     = artifact_name,
        startup_sql       = startup.startup_sql,
        debug             = args.debug,
        project_root      = root,
        object_types      = object_types,
        object_names      = object_names,
        named_connections = (
            startup.config.get("sqlcl_named_connections") is not False
            if startup.config
            else True
        ),
    )

    try:
        result = _run_diff_under_a_bar(request, _comparison_row(source_conn, target_conn))
    except RuntimeError as error:
        if args.debug:
            raise
        print_adt_header("DIFF FAILED:")
        print(str(error))
        return 1

    # **The artifact does not reach the screen** (Jan, `#769`). `#763` closed
    # the run on a `DIFF COMPLETE:` section carrying the zip path and a
    # three-line hint for deploying it; the whole section is gone, so the last
    # thing `diff` prints is the answer it was asked for. The zip is still
    # written and kept under `-out`, named for the two sides it compares
    # (`diff/runner.artifact_name`), which is what makes it findable without a
    # line of console spent on saying where it is.
    if result.artifact_path is not None:
        # **The screen reads the two export trees, not the zip** (`#790`). The
        # trees are what SQLcl compared, one file per object per side, so every
        # row can say which side it is on; a generated DDL script cannot, which
        # is how `ACTION` came to print `COMMENT` beside `COMMENT`.
        summary = summarize(result.inventory)
        if summary is not None:
            # The export already narrowed on these (`#780`, widened to every
            # dictionary family by `#790`), so this mostly finds nothing left to
            # drop. It still runs: a comment, a grant or an index is narrowed on
            # the name of the object it hangs off, so a row whose own name does
            # not match the pattern can still come back.
            summary = summary.select(types=object_types, names=object_names)
        report_summary(summary, verbose=args.verbose)
    else:
        print_adt_header("DIFF FAILED:")
        if result.output:
            print(result.output)

    return 0 if result.success else 1


COMPARING_HEADER = "COMPARING SCHEMAS:"


def _comparison_row(source: Connection, target: Connection) -> str:
    """`DEV.GSN -> TEST.GSN`: the direction, named once, for the whole screen.

    It read `GSN -> GSN` until `#790`, which is the same word twice and the usual
    comparison, one schema across two environments. The listings below carry
    `MISSING` / `CHANGED` / `EXTRA` and no `ON <env>` suffix on any row, because
    the target is the same for every one of them (Jan: *"thats redundant and
    increasing column width for no valid reason"*). This row is where the reader
    learns which side those words are about, so it has to name both sides.
    """
    return (
        f"{source.environment}.{source.schema} -> "
        f"{target.environment}.{target.schema}"
    )

# What a comparison is assumed to cost before it has run. SQLcl DIFF reports no
# progress of its own, so the bar crawls against this and holds at 99 until the
def _run_diff_under_a_bar(request: DiffRequest, row: str) -> DiffResult:
    """Announce the comparison, then count it down under a crawling row (`#763`).

    The connection blocks above END in a blank line, which retires their claim
    on the screen, so everything under them was unannounced: `diff` printed two
    version tables and then nothing at all for the length of the SQLcl run.

    **The countdown's target is what this pair AND THIS FILTER cost last time**,
    folded through the rolling average in `diff/timers.py`, exactly as
    `export_apex` reads `apex_timers.yaml` and `ut` reads `ut_timers.yaml` per
    suite variant. A first-ever run has no figure and falls back to
    `FALLBACK_TARGET_SECONDS`, which is what keeps the bar crawling instead of
    jumping to 99%; that is the same answer the other two bars give, and it is
    the answer here rather than a different column.

    The elapsed time is recorded whether or not the comparison produced an
    artifact: a run SQLcl finished without a diff still walked both schemas, and
    the next run has to count down through that same walk. A run that raised
    records nothing, because the figure it would store is the time spent up to a
    failure rather than the time a comparison costs.

    `-debug` keeps the bare call. Debug output is the raw transport, and a row
    redrawing itself over it is noise in the one mode that exists to show
    exactly what happened; the header still prints, so the wait is still named.

    The bar returns the elapsed time rather than the operation's value, so the
    result is carried out of the worker by closure. A raise inside the worker
    still propagates: `TimedProgressBar.run` closes the row with `FAILED` and
    re-raises, which is what keeps `DIFF FAILED:` off the end of a live row.
    """
    print_adt_header(COMPARING_HEADER)
    print()
    if request.debug:
        return DiffRunner().run(request)
    # `project_root` is optional on the request, so the history falls back to the
    # artifact folder rather than refusing to record: a bar with no target is the
    # defect this card exists for, and every CLI path passes a real root.
    path = timers_path(request.project_root or request.artifact_out)
    # The filter is part of the key, not just the pair: a narrowed run exports
    # less and finishes in a fraction of the time, so one shared figure would
    # count a filtered run down from a full one's clock and back again.
    pair = pair_key(
        request.source.schema,
        request.target.schema,
        types = request.object_types,
        names = request.object_names,
    )
    target = previous_seconds(path, pair) or FALLBACK_TARGET_SECONDS
    carried: list[DiffResult] = []
    elapsed = TimedProgressBar().run(
        row, target, lambda: carried.append(DiffRunner().run(request))
    )
    record_seconds(path, pair, elapsed)
    return carried[0]


__all__ = [name for name in globals() if not name.startswith("__")]
