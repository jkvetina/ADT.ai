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
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

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
from adt_ai.cli.context_errors import _project_relative
from adt_ai.cli.diff_data_reporter import report_data_diff, write_data_log
from adt_ai.cli.diff_reporters import COMPARING_HEADER, IN_SYNC_REST, report_summary
from adt_ai.cli.gateways import build_gateway, debug_wrapped
from adt_ai.diff.data import TABLE_DATA_TYPE, DataDiffRunner, DataOptions, DataSide
from adt_ai.diff.rest import REST_MODULE_TYPE, RestDiffRunner
from adt_ai.diff.runner import DiffResult, data_log_path, resolve_out
from adt_ai.diff.summary import DiffSummary, summarize
from adt_ai.diff.timers import pair_key, previous_seconds, record_seconds, timers_path
from adt_ai.export_data.runner import _existing_data_names
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

    # `-rest` compares REST definitions and writes no artifact (`#878`), so the two
    # flags that only the object comparison reads would be accepted and then
    # ignored. Refused before anything connects, the way `-target` is.
    rest = getattr(args, "rest", False)
    data = getattr(args, "data", False)
    if rest and data:
        print_adt_error(
            "ARGUMENT INVALID",
            "-rest and -data each replace the object comparison; run them one at a time.",
        )
        return exit_code_for("ARGUMENT INVALID")
    # `-data -out` names the file the untrimmed rows go to (`#886`), so under
    # `-data` only `-type` is left for the object comparison alone.
    if rest or data:
        mode, compares = ("-rest", "REST definitions") if rest else ("-data", "table rows")
        refused = (("-type", args.type), *((("-out", args.out),) if rest else ()))
        for flag, value in refused:
            if value:
                print_adt_error(
                    "ARGUMENT INVALID",
                    f"{flag} applies to the object comparison, and {mode} compares "
                    f"{compares} only.",
                )
                return exit_code_for("ARGUMENT INVALID")
    # `-ignore` and `-limit` shape the row comparison only (`#883`).
    ignore = tuple(_flatten_arg_groups(getattr(args, "ignore", None)) or ())
    limit = getattr(args, "limit", None)
    if not data:
        for flag, value in (("-ignore", ignore), ("-limit", limit)):
            if value or value == 0:
                print_adt_error(
                    "ARGUMENT INVALID",
                    f"{flag} applies to the table data comparison; add -data.",
                )
                return exit_code_for("ARGUMENT INVALID")
    if limit is not None and limit < 0:
        print_adt_error("ARGUMENT INVALID", "-limit counts rows, so it cannot be negative.")
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

    # `-data` compares what `export_data` would export (`#877`): the `-name`
    # patterns, or else every table that already carries a DATA file. With
    # neither there is nothing to compare, and saying the two schemas match
    # would be a claim about tables nobody looked at, so it is refused before
    # anything connects.
    data_names = tuple(_flatten_arg_groups(args.name) or ()) if data else ()
    if data and not data_names:
        data_names = tuple(_existing_data_names(root, startup.config, source_conn.schema))
        if not data_names:
            print_adt_error(
                "ARGUMENT INVALID",
                "-data found no exported table to compare.",
                "Name the tables with -name, or export them with export_data first.",
            )
            return exit_code_for("ARGUMENT INVALID")

    def _diff_gateway(connection: Connection) -> QueryGateway:
        if gateway_factory:
            gateway = gateway_factory(connection.schema)
        else:
            # `project_root` is what a SQLcl call runs from, and only `-rest`
            # makes one through this gateway (`#878`).
            gateway = build_gateway(startup, connection, project_root=root if rest else None)
        # Shared wrap, so the console guard keeps the nesting `build_gateway`
        # documents (`#670`).
        return debug_wrapped(gateway, debug=args.debug)

    gateways = [_diff_gateway(connection) for connection in (source_conn, target_conn)]
    for gateway, connection in zip(gateways, (source_conn, target_conn), strict=True):
        _print_connection_block(gateway, connection, debug=args.debug)

    if args.debug:
        _print_startup_debug(startup)

    # Read once and handed to both halves. The export narrows on them (`#780`)
    # and the summary narrows on the artifact that comes back, so they have to be
    # the same strings or a run would zip one set and print another.
    object_types = tuple(_flatten_arg_groups(args.type) or ())
    object_names = tuple(_flatten_arg_groups(args.name) or ())

    if data:
        return _run_data_diff(
            (DataSide(gateways[0], source_conn.export), DataSide(gateways[1], target_conn.export)),
            (source_conn, target_conn),
            startup.config,
            root,
            names   = data_names,
            options = DataOptions(ignore=ignore, limit=limit or None),
            verbose = args.verbose,
            debug   = args.debug,
            log     = data_log_path(args.out, source_conn, target_conn) if args.out else None,
        )

    if rest:
        return _run_rest_diff(
            gateways,
            (source_conn, target_conn),
            startup.config,
            root,
            names   = object_names,
            verbose = args.verbose,
            debug   = args.debug,
        )

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


def _run_rest_diff(
    gateways: Sequence[QueryGateway],
    connections: tuple[Connection, Connection],
    config: Mapping[str, object],
    root: Path,
    *,
    names: tuple[str, ...],
    verbose: bool,
    debug: bool,
) -> int:
    """`diff -rest`: both schemas' REST modules, privileges and roles, compared (ADT #878, #880).

    The same screen as the object comparison, top to bottom: `COMPARING
    SCHEMAS:` over one crawling row, then the counts, and `-verbose` for the
    listing. `REST MODULE`, `REST PRIVILEGE` and `REST ROLE` are the object
    types it can print. The two exports
    run from a throwaway folder that is gone when the run ends, because a
    comparison writes nothing a user asked to keep.
    """
    def compare() -> DiffSummary:
        with tempfile.TemporaryDirectory(prefix="adt_diff_rest_") as workdir:
            return RestDiffRunner().run(gateways[0], gateways[1], config, Path(workdir))

    # Keyed apart from the object comparison of the same pair: a REST export
    # takes seconds where the object one takes a quarter minute, so one shared
    # figure would count each down from the other's clock.
    source, target = connections
    summary = _compare_under_a_bar(
        connections,
        root,
        pair_key(source.schema, target.schema, types=(REST_MODULE_TYPE,)),
        compare,
        debug = debug,
    )
    if summary is None:
        return 1
    report_summary(summary.select(names=names), verbose=verbose, in_sync_note=IN_SYNC_REST)
    return 0


def _run_data_diff(
    sides: tuple[DataSide, DataSide],
    connections: tuple[Connection, Connection],
    config: Mapping[str, object],
    root: Path,
    *,
    names: tuple[str, ...],
    options: DataOptions,
    verbose: bool,
    debug: bool,
    log: Path | None = None,
) -> int:
    """`diff -data`: the rows of both schemas' exported tables, compared (ADT #877, #883).

    The `-rest` screen up to the counts, which are rows per table rather than
    objects per type (`cli/diff_data_reporter`). Its countdown is keyed by the
    tables asked for and the `-limit`, since either one changes the size of the
    job. `log` is where `-out` asked for the untrimmed rows (`#886`).
    """
    source, target = connections
    variant = (TABLE_DATA_TYPE, *((f"LIMIT {options.limit}",) if options.limit else ()))
    result = _compare_under_a_bar(
        connections,
        root,
        pair_key(source.schema, target.schema, types=variant, names=names),
        lambda: DataDiffRunner().run(sides[0], sides[1], config, names=names, options=options),
        debug = debug,
    )
    if result is None:
        return 1
    if log is not None:
        write_data_log(result, log, _comparison_row(source, target))
    report_data_diff(
        result,
        verbose = verbose,
        log     = _project_relative(log, root) if log is not None else None,
    )
    return 0


def _compare_under_a_bar[T](
    connections: tuple[Connection, Connection],
    root: Path,
    pair: str,
    compare: Callable[[], T],
    *,
    debug: bool,
) -> T | None:
    """`COMPARING SCHEMAS:` over one crawling row, for the modes that replace the objects.

    `None` means the comparison failed and `DIFF FAILED:` is already on screen.
    `-debug` compares without the bar and lets a failure raise, the posture
    the object comparison takes.
    """
    print_adt_header(COMPARING_HEADER)
    print()
    carried: list[T] = []
    try:
        if debug:
            carried.append(compare())
        else:
            path = timers_path(root)
            target_seconds = previous_seconds(path, pair) or FALLBACK_TARGET_SECONDS
            elapsed = TimedProgressBar().run(
                _comparison_row(*connections), target_seconds, lambda: carried.append(compare())
            )
            record_seconds(path, pair, elapsed)
    except RuntimeError as error:
        if debug:
            raise
        print_adt_header("DIFF FAILED:")
        print(str(error))
        return None
    return carried[0]


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
