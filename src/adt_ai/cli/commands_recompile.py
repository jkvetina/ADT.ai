from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from adt_ai import __version__
from adt_ai.cli.constants import (
    ActionReporter,
    DiscoveryRequest,
    DiscoveryRunner,
    DoctorRequest,
    DoctorRunner,
    GatewayFactory,
    QueryGateway,
    RecompileRequest,
    RecompileRunner,
    format_action_line,
    print_adt_header,
    print_adt_table,
    print_module_banner,
    write_file_results,
)
from adt_ai.cli.context import (
    DebugQueryGateway,
    StartupContext,
    _config_search_paths,
    _flatten_arg_groups,
    _flatten_compile_setting_groups,
    _is_database_connection_error,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
    _repo_root,
)
from adt_ai.cli.gateways import build_gateway
from adt_ai.cli.recompile_reporters import (
    _print_invalid_object_errors,
    _print_recompile_overview_table,
    print_root_causes,
)
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.recompile.render import (
    _MVIEW_COLUMNS,
    _ConsoleMViewReporter,
    _ConsoleTrailingReporter,
    _mview_row_cells,
    print_disabled_tables,
    print_job_tables,
    print_synonym_tables,
    print_trailing_updated_objects,
)
from adt_ai.shared.config import ConfigError, ConfigLoader
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.object_types import normalize_object_type_patterns


def _run_recompile(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    handler_started_at = time.monotonic()
    print_module_banner("RECOMPILE")
    startup = _load_startup_context(args)
    connections = startup.connections

    environment = args.env or connections.default_environment
    # -schema is repeatable and pattern-aware, the shape export_db already uses:
    # expand_schemas splits a comma-separated value and expands % against the
    # configured schemas, deduping while preserving order. Bare -schema means every
    # default schema, taking only the first is what made a configured
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


def _is_focused_run(request: RecompileRequest) -> bool:
    """Is this one of the report-only passes that skips the objects overview?

    Named because the answer is now needed twice, once before the run to put
    the overview header up and once after it to render the table under it.
    """
    return bool(
        request.mview
        or request.synonyms
        or request.disabled
        or request.jobs
        or request.trailing
    )


def _invalid_dependents_provider(args: argparse.Namespace, schema: str):
    """Reverse edges among the still-invalid objects, read from the local mirror.

    ``dependencies -refresh`` already maintains ``config/internal/dependencies.db``; this
    only reads it, offline, and only for the objects that are still invalid, so
    the cost is one cheap SQLite lookup per leftover, not a graph walk.

    Every failure mode degrades to "no edges" rather than breaking a recompile:
    no mirror yet, a mirror predating this schema, an unreadable file. The ranking
    then runs on compile-error evidence alone, which is what it does for a project
    that never ran ``dependencies`` at all.
    """
    def dependents_for(nodes: list[str]) -> dict[str, list[str]]:
        database = internal_path(Path(args.root).expanduser().resolve(), "dependencies.db")
        if not database.is_file():
            return {}
        wanted = set(nodes)
        try:
            from adt_ai.dependencies.store import DependencyStore

            with DependencyStore.open(database) as store:
                return {
                    node: [
                        dependent
                        for dependent in store.used_by(node, owners=[schema])
                        if dependent in wanted
                    ]
                    for node in nodes
                }
        except Exception:
            if args.debug:
                raise
            return {}

    return dependents_for


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
            else build_gateway(startup, connection)
        )
        return DebugQueryGateway(gateway) if args.debug else gateway

    _print_connection_block(recompile_gateway_factory(), connection, debug=args.debug)

    # -name/-type are multi-pattern (append + nargs="+"): flatten the groups the way
    # export_db does, then join into the comma-separated string the SQL binds split
    # on. No patterns given means no filter, i.e. everything.
    object_names = _flatten_arg_groups(args.name) or ["%"]
    # -type names an Oracle type, so the user's spelling (MVIEW, package_body) is
    # resolved to Oracle's here at the edge, once, before anything downstream sees
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
        schema         = schema,
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
    # The dependency mirror is the offline half of the root-cause ranking: it
    # connects invalid objects whose compile errors name nobody. Injected rather
    # than opened by the runner so the recompile module stays free of SQLite, and
    # so a project with no mirror simply ranks on error evidence.
    runner.dependents_for = _invalid_dependents_provider(args, schema)
    # **The overview header goes up before the run, not after it** (`#372`).
    # Everything a default recompile does is silent, the object survey, the
    # to-do selection, the compiles themselves and the re-read, so the run used
    # to sit under the connection block's closing blank with the screen saying
    # nothing, which is exactly what Jan reported: *"Most of the time you stop
    # on the last line of previous block, for example when you connect to
    # database."* The table below is the same table, filled in behind a header
    # that is already on screen. No new string: this is the header the report
    # already printed, moved.
    reports_overview = not silent and not _is_focused_run(request)
    if reports_overview:
        print_adt_header("OBJECTS OVERVIEW:")
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
        if not _is_focused_run(request):
            # The header for this table went up before the run, see above.
            _print_recompile_overview_table(result.overview)
            if result.invalid:
                print_adt_header("INVALID OBJECTS:")
                _print_invalid_object_errors(result.invalid, result.error_details)
                # The verdict reads under the evidence, not over it: ROOT CAUSES
                # keys off the IDs the tables above just introduced (#209).
                if result.root_causes:
                    print_root_causes(result.root_causes, result.invalid)
        if request.mview:
            # Batch fallback for non-streamed callers (silent path aside, this is the
            # CLI test fakes). Shares _mview_row_cells with the streamed reporter so
            # the two renders stay byte-identical: TYPE resolves the configured
            # refresh_method to F/C, LOG flags the MV log, TIMER is Oracle's recorded
            # refresh duration re-read after the action, errors listed below.
            print_adt_header("MATERIALIZED VIEWS:")
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


def _run_discovery(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_module_banner("DISCOVERY")

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
        else build_gateway(startup, connection)
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
        write_file_results(Path(args.statements_file).expanduser(), result.results)

    print_adt_header("RESULT:")
    if has_sql:
        for index, rendered_result in enumerate(result.results):
            print(rendered_result.rstrip())
            # -sql always resolves to exactly one statement (DiscoveryRunner._statements
            # never splits it), so this branch is unreachable through the CLI.
            if index + 1 < len(result.results):  # pragma: no cover
                print()
    else:
        for outcome in result.outcomes:
            rows_word = "row" if outcome.row_count == 1 else "rows"
            if outcome.ok:
                print(f"  {outcome.label}: {outcome.row_count} {rows_word}")
            else:
                print(f"  {outcome.label}: ERROR {outcome.error}")
    return 0


def _doctor_config(args: argparse.Namespace) -> dict[str, object] | None:
    """The project config, or None when this setup does not have one yet.

    Doctor is the command a broken setup runs first, so a config it cannot read
    is a normal outcome and never an error: the checks that need one report
    nothing, and the rest of the screen is unaffected.
    """
    try:
        root = Path(args.root).expanduser().resolve()
        return ConfigLoader(
            _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
        ).load().data
    except (ConfigError, OSError):
        return None


def _run_doctor(args: argparse.Namespace) -> int:
    print_module_banner("DOCTOR")
    # `-update` carries an optional version, so its absence is None rather than
    # False: `is not None` is what tells "no update asked for" apart from a
    # version that happens to be falsy.
    update_requested = args.update is not None
    update_version = args.update if isinstance(args.update, str) else None
    selected_actions = [
        flag
        for flag, selected in (
            ("-update", update_requested),
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

        # begin()/end() only fire inside a real DoctorRunner -update/-sqlcl action
        # (real pip/git/network calls): _run_doctor builds DoctorRunner with no
        # command_runner/fetcher injection point, unlike tests/helpers/doctor_runner.py's
        # status_runner used for the read-only path, so driving these two print
        # statements through the CLI would mean exercising the real upgrade
        # machinery rather than this reporter.
        def begin(self, label: str) -> None:  # pragma: no cover
            flush_pending_blank_lines()
            prefix = f"  {label} "
            self._prefix_len = len(prefix)
            print(prefix, end="", flush=True)

        def end(self, label: str, outcome: str) -> None:  # pragma: no cover
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
            update= update_requested,
            update_version=update_version,
            sqlcl= args.sqlcl,
            offline=args.offline,
            init  =args.init,
            root  =Path(args.root),
            force =args.force,
            config=_doctor_config(args),
        )
    )
    if not printed_lines:
        for line in result.lines:
            print_doctor_line(line)
    return result.exit_code

__all__ = [name for name in globals() if not name.startswith("__")]
