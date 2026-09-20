"""The `diff` command's CLI half (ADT #780).

Split out of `cli/commands_history.py` when that file crossed the 24 KB context
cap, the same budget that split `tests/diff/test_runner.py`. `diff` was the
newest and largest of the four history-shaped commands living there and the only
one with a module of its own behind it, so it is the one that leaves.

What stays in `commands_history.py`: `rebuild`, `search` and `calendar`,
which share the commit store this command never touches.

The three modes that replace the object comparison, `-rest`, `-apex` and
`-data`, live in `commands_diff_modes.py` since ADT #893 took this module to
the cap again. What is left here is the command: its refusals (`_refusal`), its
two connections, and the SQLcl object comparison.

**A command module belongs in `cli/__init__._EXPORT_MODULES`, inside the `[2:]`
slice `_PATCH_MODULES` is cut from.** That slice is what makes
`monkeypatch.setattr(cli, "DiffRunner", ...)` reach the module that actually
constructs one: a module left off the tuple keeps the real runner while the test
believes it patched it, and the symptom is a unit test that opens a database.
"""

from __future__ import annotations

import argparse

from adt_ai.cli.commands_diff_modes import (
    ApexSelection,
    _comparison_row,
    _run_apex_diff,
    _run_data_diff,
    _run_rest_diff,
)
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
from adt_ai.cli.context_apex import _parse_apex_app_selection, _parse_apex_page_selection
from adt_ai.cli.diff_pull import Pull, PullScreen
from adt_ai.cli.diff_reporters import COMPARING_HEADER, report_summary
from adt_ai.cli.gateways import build_gateway, debug_wrapped
from adt_ai.diff.data import DataOptions, DataSide
from adt_ai.diff.pull import PullSides, pull_objects
from adt_ai.diff.pull_git import checkout_refusal
from adt_ai.diff.runner import DiffResult, data_log_path, resolve_out
from adt_ai.diff.summary import summarize
from adt_ai.diff.timers import pair_key, previous_seconds, record_seconds, timers_path
from adt_ai.export_data.runner import _existing_data_names
from adt_ai.shared.connections import Connection
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.timed_bar import FALLBACK_TARGET_SECONDS, TimedProgressBar

#: The flags that switch what `diff` compares, and what each one compares.
_MODES = {
    "rest" : ("-rest", "REST definitions"),
    "data" : ("-data", "table rows"),
    "apex" : ("-apex", "APEX applications and files"),
}


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

    refusal = _refusal(args)
    if refusal is not None:
        print_adt_error("ARGUMENT INVALID", refusal)
        return exit_code_for("ARGUMENT INVALID")
    rest, data, apex = (bool(getattr(args, mode, False)) for mode in _MODES)
    ignore = tuple(_flatten_arg_groups(getattr(args, "ignore", None)) or ())
    # `-limit 0` reads everything, like leaving it out.
    limit = getattr(args, "limit", None) or None

    startup = _load_startup_context(args)
    root = startup.root
    connections = startup.connections

    # `-restore` writes into the checkout, so the checkout is judged before
    # anything connects: a run that compared for a minute and then refused to
    # write would have spent the minute on nothing (`#893`). Uncommitted work
    # is not refused; the screen saves it as a WIP commit before writing (`#897`).
    pull_refusal = checkout_refusal(root, args.branch) if args.restore else None
    if pull_refusal is not None:
        print_adt_error("ARGUMENT INVALID", pull_refusal)
        return exit_code_for("ARGUMENT INVALID")

    # The comparison a person actually runs is one schema across two
    # environments, so the target side falls back to the source schema rather
    # than to the target environment's default (Jan, ADT #763). Naming
    # `-target-schema` is what says the two sides are spelled differently.
    source_schema = getattr(args, "schema", None)
    target_schema = getattr(args, "target_schema", None) or source_schema
    # `-apex` compares what an APEX owner owns, so a side named by environment
    # alone is its `schema_apex`, the schema `export_apex` exports from.
    kind          = "apex" if apex else "db"
    source_conn   = connections.resolve(environment=args.source, schema=source_schema, kind=kind)
    target_conn   = connections.resolve(environment=args.target, schema=target_schema, kind=kind)
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
    # Where the target's versions are written, and what `RESTORED FILES:` reads
    # back (`#893`); `None` without `-restore`.
    pull = (
        Pull(
            PullScreen(root, args.branch, (source_conn, target_conn), limit, args.debug),
            PullSides(root, startup.config, source_conn, target_conn, gateways[1]),
        )
        if args.restore
        else None
    )

    if data:
        return _run_data_diff(
            (DataSide(gateways[0], source_conn.export), DataSide(gateways[1], target_conn.export)),
            (source_conn, target_conn),
            startup.config,
            root,
            names   = data_names,
            options = DataOptions(ignore=ignore, limit=limit),
            verbose = args.verbose,
            debug   = args.debug,
            log     = data_log_path(args.out, source_conn, target_conn) if args.out else None,
            pull    = pull,
        )

    if apex:
        return _run_apex_diff(
            gateways,
            (source_conn, target_conn),
            root,
            selection = _apex_selection(args),
            names     = object_names,
            verbose   = args.verbose,
            debug     = args.debug,
            limit     = limit,
            pull      = pull,
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
            limit   = limit,
            pull    = pull,
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
        tail = None
        if pull is not None and summary is not None:
            shown = summary
            tail = pull.tail("OBJECTS", lambda sides: pull_objects(sides, shown))
        report_summary(summary, verbose=args.verbose, limit=limit, tail=tail)
    else:
        print_adt_header("DIFF FAILED:")
        if result.output:
            print(result.output)

    return 0 if result.success and not (pull and pull.failed) else 1


def _refusal(args: argparse.Namespace) -> str | None:
    """Why these flags cannot run together, checked before anything connects.

    `None` when they can. Every refusal is one the run would otherwise accept
    and then ignore, which reads as a comparison of something it never looked at.
    """
    modes = [flag for dest, (flag, _) in _MODES.items() if getattr(args, dest, False)]
    if len(modes) > 1:
        return f"{' and '.join(modes)} each replace the object comparison; run them one at a time."
    apex = bool(getattr(args, "apex", False))
    # `-app` names applications and `-page` their pages, and only `-apex`
    # compares either (`#778`, `#893`). Both parse the way `export_apex` parses
    # them, ids and `MIN-MAX` / `MIN+` ranges alike.
    for flag, dest, parse in (
        ("-app", "app", _parse_apex_app_selection),
        ("-page", "page", _parse_apex_page_selection),
    ):
        tokens = _flatten_arg_groups(getattr(args, dest, None))
        if tokens and not apex:
            return f"{flag} applies to the APEX comparison; add -apex."
        try:
            parse(tokens)
        except ValueError as error:
            return str(error)
    refusal = _pairing_refusal(args, apex)
    if refusal is not None:
        return refusal
    # `-data -out` names the file the untrimmed rows go to (`#886`), so under
    # `-data` only `-type` is left for the object comparison alone.
    if modes:
        mode, compares = next(_MODES[dest] for dest in _MODES if getattr(args, dest, False))
        refused = (("-type", args.type), *((("-out", args.out),) if mode != "-data" else ()))
        for flag, value in refused:
            if value:
                return (
                    f"{flag} applies to the object comparison, and {mode} compares "
                    f"{compares} only."
                )
    # `-ignore` shapes the row comparison only (`#883`). `-limit` caps every
    # mode's listings since `#893`, so only its sign is checked.
    if _flatten_arg_groups(getattr(args, "ignore", None)) and not getattr(args, "data", False):
        return "-ignore applies to the table data comparison; add -data."
    limit = getattr(args, "limit", None)
    if limit is not None and limit < 0:
        return "-limit counts rows, so it cannot be negative."
    return None


def _pairing_refusal(args: argparse.Namespace, apex: bool) -> str | None:
    """`-target-app` pairs one application, and `-branch` is where `-restore` writes (`#893`).

    Jan: *"-target-app to override the app, this will allow us to compare our
    app to a working copy"*. A pairing is one application with one other, so a
    range or a second id on `-app` has nothing to pair with.
    """
    if getattr(args, "target_app", None) is not None:
        if not apex:
            return "-target-app pairs an application on the APEX comparison; add -apex."
        selection = _parse_apex_app_selection(_flatten_arg_groups(getattr(args, "app", None)))
        ids = selection.explicit_ids if selection is not None and not selection.ranges else ()
        if len(ids) != 1 or not ids[0].isdigit():
            return "-target-app pairs one application: name exactly one id with -app."
    if getattr(args, "branch", None) is not None and not getattr(args, "restore", False):
        return "-branch names the branch -restore writes to; add -restore."
    return None


def _apex_selection(args: argparse.Namespace) -> ApexSelection:
    """`-app`, `-page` and `-target-app` as `diff -apex` reads them, already known to parse."""
    app_tokens = tuple(_flatten_arg_groups(getattr(args, "app", None)) or ())
    page_tokens = tuple(_flatten_arg_groups(getattr(args, "page", None)) or ())
    return ApexSelection(
        app_tokens  = app_tokens,
        apps        = _parse_apex_app_selection(list(app_tokens)),
        page_tokens = page_tokens,
        pages       = _parse_apex_page_selection(list(page_tokens)),
        target_app  = getattr(args, "target_app", None),
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
