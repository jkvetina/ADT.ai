from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from adt_ai import __version__
from adt_ai.cli.constants import (
    RESULT_BLOCK_END,
    RESULT_BLOCK_START,
    ActionReporter,
    DiscoveryRequest,
    DiscoveryRunner,
    DoctorRequest,
    DoctorRunner,
    GatewayFactory,
    OracleGateway,
    QueryGateway,
    RecompileRequest,
    RecompileRunner,
    format_action_line,
    print_adt_header,
    print_adt_table,
)
from adt_ai.cli.context import (
    DebugQueryGateway,
    StartupContext,
    _flatten_arg_groups,
    _flatten_compile_setting_groups,
    _is_database_connection_error,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
    _repo_root,
)
from adt_ai.cli.recompile_reporters import (
    _print_invalid_object_errors,
    _print_recompile_overview_table,
)
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.recompile.render import (
    _MVIEW_COLUMNS,
    _ConsoleMViewReporter,
    _ConsoleTrailingReporter,
    _locked_row_cells,
    _mview_row_cells,
    print_disabled_tables,
    print_job_tables,
    print_synonym_tables,
    print_trailing_updated_objects,
)
from adt_ai.shared import text_files
from adt_ai.shared.object_types import normalize_object_type_patterns


def _run_recompile(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    handler_started_at = time.monotonic()
    print_adt_header("APEX DEPLOYMENT TOOL: RECOMPILE")
    startup = _load_startup_context(args)
    connections = startup.connections

    environment = args.env or connections.default_environment
    # -schema is repeatable and pattern-aware, the shape export_db already uses:
    # expand_schemas splits a comma-separated value and expands % against the
    # configured schemas, deduping while preserving order. Bare -schema means every
    # default schema — taking only the first is what made a configured
    # `schema_db: APP,CORE` export both and recompile one.
    schemas = (
        connections.expand_schemas(_flatten_arg_groups(args.schema), environment=environment)
        if args.schema
        else connections.default_schemas(environment)
    )

    if args.debug:
        _print_startup_debug(startup)

    # Each schema is an independent pass against its own connection, run as its own
    # console segment (connection block -> full pass -> TIMER). They all run even
    # when one fails, so a broken schema cannot mask the state of the rest; the first
    # failure sets the exit code.
    def run_one(schema: str) -> int:
        return _run_recompile_for_schema(args, startup, environment, schema, gateway_factory)

    return run_schema_sections(schemas, run_one, first_started_at=handler_started_at)


def _run_recompile_for_schema(
    args: argparse.Namespace,
    startup: StartupContext,
    environment: str,
    schema: str,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    connection = startup.connections.resolve(environment=environment, schema=schema)
    prefix = connection.export.get("prefix", "") if connection.export else ""
    ignore = connection.export.get("ignore", "") if connection.export else ""

    silent = args.silent

    # the recompile runner reconnects between passes, so it takes a no-arg
    # factory; adapt the schema-keyed CLI factory (or build a real gateway).
    def recompile_gateway_factory() -> QueryGateway:
        gateway = (
            gateway_factory(schema)
            if gateway_factory
            else OracleGateway(connection, startup_sql=startup.startup_sql, config=startup.config)
        )
        return DebugQueryGateway(gateway) if args.debug else gateway

    _print_connection_block(recompile_gateway_factory(), connection, debug=args.debug)

    # -name/-type are multi-pattern (append + nargs="+"): flatten the groups the way
    # export_db does, then join into the comma-separated string the SQL binds split
    # on. No patterns given means no filter, i.e. everything.
    object_names = _flatten_arg_groups(args.name) or ["%"]
    # -type names an Oracle type, so the user's spelling (MVIEW, package_body) is
    # resolved to Oracle's here at the edge — once, before anything downstream sees
    # it. -name is an identifier pattern and gets no such treatment: an underscore
    # there is a real wildcard over real underscores.
    object_types = normalize_object_type_patterns(_flatten_arg_groups(args.type) or ["%"])

    # -scope/-warnings are repeatable multi-value keyword lists (space/comma/+/repeated
    # forms all equivalent), flattened and upper-cased the way -name/-type already are.
    request = RecompileRequest(
        object_name    = ",".join(object_names),
        object_type    = ",".join(object_types),
        prefix         = prefix,
        ignore         = ignore,
        force          = args.force,
        native         = args.native,
        interpreted    = args.interpreted,
        optimize_level = args.level,
        scope          = _flatten_compile_setting_groups(args.scope),
        warnings       = _flatten_compile_setting_groups(args.warnings),
        mview          = args.mviews,
        synonyms       = args.synonyms,
        disabled       = args.disabled,
        jobs           = args.jobs,
        trailing       = args.trailing,
        debug          = args.debug,
    )

    # A console reporter streams the live sections so a per-object hang attaches to the
    # object being worked on: MATERIALIZED VIEWS for -mviews, UPDATED OBJECTS for
    # -trailing. The two passes are mutually exclusive branches in the runner, so
    # one reporter is selected per run. It is injected post-construction so the CLI
    # test fakes (single-arg __init__) are untouched; those fakes never drive the
    # reporter, so `streamed` stays False and the batch render runs as the fallback.
    console_reporter = (
        _ConsoleTrailingReporter(silent=silent) if args.trailing else _ConsoleMViewReporter()
    )
    runner = RecompileRunner(recompile_gateway_factory)
    runner.reporter = console_reporter
    result = runner.run(request)

    if request.trailing:
        # -trailing renders itself: the header must survive -silent (the console
        # contract drops per-row detail only), which the shared `not silent` guard
        # below would swallow. The run normally streams through the reporter; the
        # batch render is the fallback when the runner never drove it.
        if not console_reporter.streamed:
            print_trailing_updated_objects(result.trailing, result.trailing_actions, silent)
        return 0 if result.success else 1

    if not silent and not console_reporter.streamed:
        # -mviews, -synonyms, -disabled, -jobs, and -trailing are focused/report-only
        # runs: skip the objects overview, invalid-object summary, and compile-error
        # report (no object recompile ran), keeping only their specific report
        # sections below.
        if not (
            request.mview
            or request.synonyms
            or request.disabled
            or request.jobs
            or request.trailing
        ):
            print_adt_header("OBJECTS OVERVIEW")
            _print_recompile_overview_table(result.overview)
            if result.invalid:
                print_adt_header("INVALID OBJECTS")
                _print_invalid_object_errors(result.invalid, result.error_details)
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
    text_files.write_text(file_path, "".join(parts))


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
        else OracleGateway(connection, startup_sql=startup.startup_sql, config=startup.config)
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
