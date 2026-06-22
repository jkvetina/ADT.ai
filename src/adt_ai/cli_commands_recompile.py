from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from adt_ai import __version__
from adt_ai.cli_constants import (
    RESULT_BLOCK_END,
    RESULT_BLOCK_START,
    ActionReporter,
    DiscoveryRequest,
    DiscoveryRunner,
    DoctorRequest,
    DoctorRunner,
    GatewayFactory,
    ObjectOverview,
    OracleGateway,
    QueryGateway,
    RecompileRequest,
    RecompileRunner,
    format_action_line,
    print_adt_header,
    print_adt_table,
)
from adt_ai.cli_context import (
    DebugQueryGateway,
    _is_database_connection_error,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
    _repo_root,
)
from adt_ai.recompile.render import (
    _MVIEW_COLUMNS,
    _ConsoleMViewReporter,
    _locked_row_cells,
    _mview_row_cells,
    print_disabled_tables,
    print_job_tables,
    print_synonym_tables,
)


def _run_recompile(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: RECOMPILE")
    startup = _load_startup_context(args)
    connections = startup.connections

    environment = args.target or args.env or connections.default_environment
    if args.schema:
        schema = args.schema
    else:
        default_schemas = connections.default_schemas(environment)
        schema = default_schemas[0] if default_schemas else "APP"
    connection = connections.resolve(environment=environment, schema=schema)
    prefix = connection.export.get("prefix", "") if connection.export else ""
    ignore = connection.export.get("ignore", "") if connection.export else ""

    silent = args.silent
    if args.debug:
        _print_startup_debug(startup)

    # the recompile runner reconnects between passes, so it takes a no-arg
    # factory; adapt the schema-keyed CLI factory (or build a real gateway).
    def recompile_gateway_factory() -> QueryGateway:
        gateway = (
            gateway_factory(schema)
            if gateway_factory
            else OracleGateway(connection, startup_sql=startup.startup_sql)
        )
        return DebugQueryGateway(gateway) if args.debug else gateway

    _print_connection_block(recompile_gateway_factory(), connection, debug=args.debug)

    request = RecompileRequest(
        object_name    = args.name,
        object_type    = args.type,
        prefix         = prefix,
        ignore         = ignore,
        force          = args.force,
        native         = args.native,
        optimize_level = args.level,
        scope          = args.scope,
        warnings       = args.warnings,
        mview          = args.mviews is not None,
        mview_name     = args.mviews or "%",
        synonyms       = args.synonyms is not None,
        synonym_name   = args.synonyms or "%",
        disabled       = args.disabled is not None,
        disabled_name  = args.disabled or "%",
        jobs           = args.jobs is not None,
        job_name       = args.jobs or "%",
        errors         = args.errors,
        debug          = args.debug,
    )

    # A console reporter streams the LOCKED + MATERIALIZED VIEWS sections live during
    # an -mviews run, so each refresh's hang attaches to the view being worked on. It
    # is injected post-construction so the CLI test fakes (single-arg __init__) are
    # untouched; those fakes never drive the reporter, so `streamed` stays False and
    # the batch render below runs as the fallback (and for every non-mview run).
    console_reporter = _ConsoleMViewReporter()
    runner = RecompileRunner(recompile_gateway_factory)
    runner.reporter = console_reporter
    result = runner.run(request)

    if not silent and not console_reporter.streamed:
        # -mviews, -synonyms, -disabled, and -jobs are focused/report-only runs: skip the
        # objects overview, invalid-object summary, and compile-error report (no
        # object recompile ran), keeping only their specific report sections below.
        if not request.mview and not request.synonyms and not request.disabled and not request.jobs:
            print_adt_header("OBJECTS OVERVIEW")
            _print_recompile_overview_table(result.overview)
            if result.invalid:
                print_adt_header("INVALID OBJECTS")
                print_adt_table(
                    [
                        {
                            "OBJECT_TYPE": invalid.object_type,
                            "OBJECT_NAME": invalid.object_name,
                            "ERRORS":      invalid.errors,
                            "ERROR":       invalid.error or "",
                        }
                        for invalid in result.invalid
                    ]
                )
            if request.errors:
                print_adt_header("COMPILE ERRORS")
                print_adt_table(
                    [
                        {
                            "ID":          detail.id,
                            "OBJECT_TYPE": detail.object_type,
                            "OBJECT_NAME": detail.object_name,
                            "LINE":        detail.line,
                            "POSITION":    detail.position if detail.position is not None else "",
                        }
                        for detail in result.error_details
                    ],
                    columns=["ID", "OBJECT_TYPE", "OBJECT_NAME", "LINE", "POSITION"],
                )
                # the TEXT column would be too wide for the table, so each error's
                # full message is listed below, keyed back to the table's ID column.
                for detail in result.error_details:
                    print(f"  {detail.id}) {detail.text}")
                if result.error_details:
                    # keep the two-blank-lines-above-next-header contract: the
                    # table's trailing blank is consumed by this list, re-emit one.
                    print()
        if result.locked:
            print_adt_header("LOCKED OBJECTS")
            print_adt_table([_locked_row_cells(lock) for lock in result.locked])
        if request.mview:
            # Batch fallback for non-streamed callers (silent path aside, this is the
            # CLI test fakes). Shares _mview_row_cells with the streamed reporter so
            # the two renders stay byte-identical: TYPE resolves the configured
            # refresh_method to F/C, LOG flags the MV log, TIMER is Oracle's recorded
            # refresh duration re-read after the action, errors listed below.
            print_adt_header("MATERIALIZED VIEWS")
            print_adt_table(
                [_mview_row_cells(mview) for mview in result.mviews],
                columns=list(_MVIEW_COLUMNS),
            )
            # a failed refresh/compile lists its error below the table, keyed by
            # object name and styled like the COMPILE ERRORS message list.
            failed_actions = [a for a in result.mview_actions if not a.ok and a.error]
            for action in failed_actions:
                print(f"  {action.object_name}) {action.error}")
            if failed_actions:
                # keep the two-blank-lines-above-next-header contract: the table's
                # trailing blank is consumed by this list, re-emit one.
                print()
        if request.synonyms:
            print_synonym_tables(result.synonyms)
        if request.disabled:
            print_disabled_tables(result.disabled_objects)
        if request.jobs:
            print_job_tables(result.jobs)

    return 0 if result.success else 1


def _print_recompile_overview_table(overviews: Sequence[ObjectOverview]) -> None:
    if not overviews:
        return

    object_width = max(
        12,
        *(len(overview.object_type) for overview in overviews),
    )
    widths = [object_width, 5, 7, 11, 10]
    separators = ["   ", "   ", "   ", "    "]

    def display_cell(cell: object) -> object:
        return "" if isinstance(cell, int) and cell == 0 else cell

    def line(cells: Sequence[object], aligns: Sequence[str]) -> str:
        parts = [
            f"{str(display_cell(cell)):{align}{width}}"
            for cell, width, align in zip(cells, widths, aligns, strict=True)
        ]
        return "  " + "".join(
            part + (separators[index] if index < len(separators) else "")
            for index, part in enumerate(parts)
        )

    plscope_start = 2 + object_width + 3 + widths[1] + 3 + widths[2] + 3
    print()
    print(
        (" " * plscope_start)
        + f"{'MISSING':>{widths[3]}}"
        + separators[3]
        + f"{'MISSING':>{widths[4]}}"
    )
    print(
        line(
            ["OBJECT TYPE", "TOTAL", "INVALID", "IDENTIFIERS", "STATEMENTS"],
            ["<", ">", ">", ">", ">"],
        )
    )
    print(
        line(
            [
                "-" * widths[0],
                "-" * widths[1],
                "-" * widths[2],
                "-" * widths[3],
                "-" * widths[4],
            ],
            ["<", ">", ">", ">", ">"],
        )
    )
    for overview in overviews:
        print(
            line(
                [
                    overview.object_type,
                    overview.total,
                    overview.invalid,
                    overview.missing_plscope_identifiers,
                    overview.missing_plscope_statements,
                ],
                ["<", ">", ">", ">", ">"],
            )
        )
    # Trailing blank so the next header gets two empty lines above it (this
    # blank + the header's own leading blank). print_adt_table sections already
    # close with a blank; this hand-rolled table must match that contract.
    print()


# Built from the shared result-block sentinel so the write-back and this scrub
# can never drift apart: only blocks carrying the ADT-RESULT marker are removed
# on re-run, leaving any hand-written ``/* … */`` comments untouched.
_FILE_RESULT_RE = re.compile(
    r"\n" + re.escape(RESULT_BLOCK_START) + r"\n.*?" + re.escape(RESULT_BLOCK_END),
    re.DOTALL,
)


def _write_file_results(file_path: Path, results: list[str]) -> None:
    """Rewrite ``file_path`` inserting rendered results after each statement.

    Each statement keeps its ``;`` and gets a ``/* … */`` block appended.
    On re-runs the old blocks are replaced, so the file stays clean.
    Statements without a matching result keep their ``;`` and no block is added.
    """
    text = file_path.read_text(encoding="utf-8")
    stripped = _FILE_RESULT_RE.sub("", text)
    raw_pieces = stripped.split(";")
    while raw_pieces and not raw_pieces[-1].strip():
        raw_pieces.pop()

    parts: list[str] = []
    for i, piece in enumerate(raw_pieces):
        stmt = piece.rstrip()
        if i < len(results):
            parts.append(f"{stmt};\n{RESULT_BLOCK_START}\n{results[i]}\n{RESULT_BLOCK_END}\n")
        else:
            parts.append(f"{stmt};\n")
    file_path.write_text("".join(parts), encoding="utf-8")


def _run_discovery(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: DISCOVERY")

    has_sql  = bool(args.sql and args.sql.strip())
    has_file = bool(args.statements_file)
    if has_sql == has_file:
        print(
            "discovery: provide exactly one of -sql or -file",
            file=sys.stderr,
        )
        return 2

    startup = _load_startup_context(args)
    root = startup.root
    connections = startup.connections

    environment = args.env or connections.default_environment
    if args.schema:
        schema = args.schema
    else:
        default_schemas = connections.default_schemas(environment)
        schema = default_schemas[0] if default_schemas else "APP"
    connection = connections.resolve(environment=environment, schema=schema)

    gateway = (
        gateway_factory(schema)
        if gateway_factory
        else OracleGateway(connection, startup_sql=startup.startup_sql)
    )
    if args.debug:
        gateway = DebugQueryGateway(gateway)

    _print_connection_block(gateway, connection, debug=args.debug)

    if args.debug:
        _print_startup_debug(startup)

    result = DiscoveryRunner(gateway, fatal_error=_is_database_connection_error).run(
        DiscoveryRequest(
            root            = root,
            when            = datetime.now(),
            sql             = args.sql if has_sql else None,
            statements_file = Path(args.statements_file).expanduser() if has_file else None,
            limit           = args.limit,
            no_log          = args.no_log,
        )
    )

    if has_file:
        _write_file_results(Path(args.statements_file).expanduser(), result.results)

    print_adt_header("RESULT:")
    if has_sql:
        for index, rendered_result in enumerate(result.results):
            print(rendered_result.rstrip())
            if index + 1 < len(result.results):
                print()
    else:
        for outcome in result.outcomes:
            rows_word = "row" if outcome.row_count == 1 else "rows"
            if outcome.ok:
                print(f"  {outcome.label}: {outcome.row_count} {rows_word}")
            else:
                print(f"  {outcome.label}: ERROR {outcome.error}")
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: DOCTOR")
    selected_actions = [
        flag
        for flag, selected in (
            ("-update", args.update),
            ("-sqlcl", args.sqlcl),
            ("-init", args.init),
        )
        if selected
    ]
    if len(selected_actions) > 1:
        joined_actions = (
            " and ".join(selected_actions)
            if len(selected_actions) == 2
            else f"{', '.join(selected_actions[:-1])}, and {selected_actions[-1]}"
        )
        print(f"Error: {joined_actions} cannot be combined")
        return 1

    printed_lines: list[str] = []
    pending_blank_lines = 0

    def flush_pending_blank_lines() -> None:
        nonlocal pending_blank_lines
        for _ in range(pending_blank_lines):
            print()
        pending_blank_lines = 0

    def print_doctor_line(line: str) -> None:
        nonlocal pending_blank_lines
        printed_lines.append(line)
        if line == "":
            pending_blank_lines += 1
            return
        flush_pending_blank_lines()
        if line in {"CURRENT VERSIONS:", "ENVIRONMENT:", "ACTIONS:", "PROJECT INIT:"}:
            print_adt_header(line)
        else:
            print(line)
        sys.stdout.flush()

    class _ConsoleActionReporter(ActionReporter):
        """Prints the action label immediately, then completes the same line with
        the dot leader and outcome once the work finishes."""

        def begin(self, label: str) -> None:
            flush_pending_blank_lines()
            prefix = f"  {label} "
            self._prefix_len = len(prefix)
            print(prefix, end="", flush=True)

        def end(self, label: str, outcome: str) -> None:
            full = format_action_line(label, outcome)
            print(full[self._prefix_len:], flush=True)

    result = DoctorRunner(
        package_version=__version__,
        package_root=_repo_root(),
        line_callback=print_doctor_line,
        action_reporter=_ConsoleActionReporter(),
        version_cache_dir=Path.home() / ".cache" / "adt-ai" / "doctor",
    ).run(
        DoctorRequest(
            update= args.update,
            sqlcl= args.sqlcl,
            offline=args.offline,
            init  =args.init,
            root  =Path(args.root),
            force =args.force,
        )
    )
    if not printed_lines:
        for line in result.lines:
            print_doctor_line(line)
    return result.exit_code

__all__ = [name for name in globals() if not name.startswith("__")]
