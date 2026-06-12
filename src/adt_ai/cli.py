from __future__ import annotations

import argparse
import fnmatch
import importlib
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from adt_ai import __version__
from adt_ai.config import ConfigError, ConfigLoader
from adt_ai.connections import ConnectionError as ConnectionConfigError
from adt_ai.connections import ConnectionLoader, ConnectionResult
from adt_ai.db import OracleGateway, QueryGateway
from adt_ai.doctor.runner import ActionReporter, DoctorRequest, DoctorRunner, format_action_line
from adt_ai.export_db.runner import (
    ConsoleExportDbReporter,
    ExportDbRequest,
    ExportDbRunner,
    GatewayFactory,
    print_adt_header,
)

PUBLIC_MODULES = (
    ("doctor", "check local setup and run explicit updates", ()),
    ("export_db", "export database objects", ()),
)
PUBLIC_COMMANDS = tuple(command for name, _description, aliases in PUBLIC_MODULES for command in (name, *aliases))

APEX_VERSION_QUERY = "SELECT a.version_no AS version FROM apex_release a"
DATABASE_VERSION_QUERY = """
SELECT
    p.version_full || ' | ' ||
    REGEXP_REPLACE(SYS_CONTEXT('USERENV', 'DB_NAME'), '^[^_]+_', '') AS version
FROM product_component_version p
""".strip()
DATABASE_VERSION_OLD_QUERY = """
SELECT p.version
FROM product_component_version p
WHERE p.product LIKE 'Oracle Database%'
""".strip()
DROPBOX_PATH_RE = re.compile(r"/Users/[^/]+/Library/CloudStorage/Dropbox/")


class AdtArgumentError(Exception):
    pass


class AdtArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def format_help(self) -> str:
        help_text = super().format_help()
        return help_text if help_text.endswith("\n\n") else f"{help_text}\n"

    def error(self, message: str) -> None:
        raise AdtArgumentError(message)


class _StdoutTracker:
    def __init__(self, wrapped: TextIO) -> None:
        self.wrapped = wrapped
        self._pending_newlines = ""
        self._committed_trailing_newlines = 0
        self.had_output = False

    def write(self, text: str) -> int:
        if not text:
            return 0
        self.had_output = True
        stripped = text.rstrip("\n")
        if not stripped:
            self._pending_newlines += text
            return len(text)
        trailing_count = len(text) - len(stripped)
        body = text[:-trailing_count] if trailing_count else text
        self._flush_pending()
        self.wrapped.write(body)
        self.wrapped.flush()
        self._committed_trailing_newlines = 0
        self._pending_newlines = "\n" * trailing_count
        return len(text)

    def normalize_trailing_newlines(self, count: int) -> None:
        if self._pending_newlines:
            self._pending_newlines = "\n" * count
            return
        self._pending_newlines = "\n" * max(count - self._committed_trailing_newlines, 0)

    def flush(self) -> None:
        self.wrapped.flush()

    def commit_pending(self) -> None:
        self._flush_pending()
        self.wrapped.flush()

    def finalize(self) -> None:
        self.commit_pending()

    def _flush_pending(self) -> None:
        if self._pending_newlines:
            self.wrapped.write(self._pending_newlines)
            self._committed_trailing_newlines = len(self._pending_newlines)
            self._pending_newlines = ""

    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)


class _StderrTracker(_StdoutTracker):
    def __init__(self, wrapped: TextIO, stdout_tracker: _StdoutTracker) -> None:
        super().__init__(wrapped)
        self._stdout_tracker = stdout_tracker

    def write(self, text: str) -> int:
        if text:
            self._stdout_tracker.commit_pending()
        return super().write(text)


class DebugQueryGateway:
    def __init__(self, wrapped: QueryGateway) -> None:
        self.wrapped = wrapped

    def fetch_all(self, sql: str, params: Mapping[str, object] | None = None) -> list[dict[str, object]]:
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        return self.wrapped.fetch_all(sql, params)

    def execute(self, sql: str, params: Mapping[str, object] | None = None) -> None:
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        self.wrapped.execute(sql, params)

    def sqlcl_request(self, request: str, root: Path) -> str:
        print()
        print("SQLCL REQUEST:")
        print(request)
        print()
        return self.wrapped.sqlcl_request(request, root)


@dataclass(frozen=True)
class StartupContext:
    root                    : Path
    repo_root               : Path
    config                  : dict[str, object]
    config_search_paths     : list[Path]
    config_dirs             : list[Path]
    connection_search_paths : list[Path]
    connection_files        : list[Path]
    connections             : ConnectionResult
    startup_sql             : str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = AdtArgumentParser(prog="adt-ai", description="Modern ADT command line tool.")
    parser.add_argument("--version", action="store_true", help="show version and exit")
    subparsers = parser.add_subparsers(dest="command", parser_class=AdtArgumentParser)

    export_db = subparsers.add_parser("export_db", description="export database objects", help="export database objects")
    export_db.add_argument("--root", "-root", default=".", help="output root folder")
    export_db.add_argument("--config-dir", "-config-dir", action="append", help="folder containing config YAML")
    export_db.add_argument("--env", "-env", help="connection environment")
    export_db.add_argument("--schema", "-schema", action="append", help="schema to export")
    export_db.add_argument("--type", "-type", action="append", nargs="+", help="object type pattern(s) to export, supports %% wildcards")
    export_db.add_argument("--name", "-name", action="append", nargs="+", help="object name pattern(s) to export, supports %% wildcards")
    export_db.add_argument("--recent", "-recent", type=int, help="export objects changed in the last DAYS days")
    export_db.add_argument("--dry-run", "-dry-run", action="store_true", help="plan writes without changing files")
    export_db.add_argument("--delete", "-delete", action="store_true", help="delete existing object files before export, excluding DATA")
    export_db.add_argument("--silent", "-silent", action="store_true", help="suppress per-object progress; keep overview, chrome, and timer")
    export_db.add_argument("--debug", "-debug", action="store_true", help="show input parameters and SQL queries with bind values")

    doctor = subparsers.add_parser("doctor", description="check local ADT.ai environment setup and run explicit updates", help="check local setup and run explicit updates")
    doctor.add_argument("-offline", action="store_true", help="skip online update checks and show local versions only")
    doctor.add_argument("-update", action="store_true", help="run full ADT.ai, requirements, and SQLcl upgrade")
    doctor.add_argument("-sqlcl", action="store_true", help="upgrade SQLcl only; runs immediately without -update")
    doctor.add_argument("-init", action="store_true", help="scaffold project config, ignore rules, and safe local folders")
    doctor.add_argument("--root", "-root", default=".", help="project root folder for -init")
    doctor.add_argument("--force", "-force", action="store_true", help="overwrite existing generated template files with -init")
    return parser


def main(argv: Sequence[str] | None = None, gateway_factory: GatewayFactory | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv or raw_argv in (["-h"], ["--help"]):
        return _run_module_overview()
    if raw_argv and _is_unknown_command(raw_argv[0]):
        return _run_invalid_command(raw_argv[0])

    parser = build_parser()
    if raw_argv[0] in PUBLIC_COMMANDS and _has_help_flag(raw_argv[1:]):
        return _run_command_help(raw_argv[0], parser)
    try:
        args = parser.parse_args(raw_argv)
    except AdtArgumentError as error:
        if raw_argv[0] in PUBLIC_COMMANDS:
            return _run_command_argument_error(raw_argv[0], str(error))
        return _run_top_level_argument_error(str(error))
    except SystemExit as error:
        return int(error.code or 0)

    if args.version:
        print(f"ADT.ai {__version__}")
        return 0

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    tracked_stdout = original_stdout if isinstance(original_stdout, _StdoutTracker) else _StdoutTracker(original_stdout)
    tracked_stderr = original_stderr if isinstance(original_stderr, _StdoutTracker) else _StderrTracker(original_stderr, tracked_stdout)
    sys.stdout = tracked_stdout
    sys.stderr = tracked_stderr
    started_at = time.monotonic()
    exit_code = 0
    try:
        if args.command == "export_db":
            exit_code = _run_export_db(args, gateway_factory=gateway_factory)
        elif args.command == "doctor":
            exit_code = _run_doctor(args)
    except KeyboardInterrupt:
        exit_code = 130
        print("\nInterrupted by user.", file=sys.stderr)
    except (ConnectionConfigError, ConfigError) as error:
        exit_code = 1
        if getattr(args, "debug", False):
            raise
        _print_config_error(error)
    except Exception as error:
        exit_code = 1
        if getattr(args, "debug", False) or not _is_user_database_error(error):
            raise
        _print_database_error(error)
    finally:
        footer_stdout = tracked_stderr if exit_code != 0 and tracked_stderr.had_output else tracked_stdout
        _print_completion_timer(started_at, stdout=footer_stdout)
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return exit_code


def console_main() -> None:
    raise SystemExit(main())


def _run_export_db(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_DB")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    schemas = connections.expand_schemas(args.schema, environment=environment) if args.schema else connections.default_schemas(environment)
    schema_connections = {schema: connections.resolve(environment=environment, schema=schema) for schema in schemas}
    schema_export = {schema: schema_connections[schema].export for schema in schemas}
    config = _with_schema_folders(config, schema_export)
    object_types = _flatten_arg_groups(args.type)
    object_names = _flatten_arg_groups(args.name)
    if args.debug:
        _print_startup_debug(startup)
    if _has_job_recent_conflict(args.recent, object_types or []):
        print("export_db: JOB objects cannot be exported with -recent; export jobs separately with -type JOB.", file=sys.stderr)
        return 2

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(schema_connections[schema], startup_sql=startup.startup_sql)

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def cached_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = selected_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[schema]

    for schema in schemas:
        _print_connection_block(cached_gateway_factory(schema), schema_connections[schema], debug=args.debug)

    ExportDbRunner(cached_gateway_factory).run(
        ExportDbRequest(
            root          = root,
            schemas       = schemas,
            config        = config,
            schema_export = schema_export,
            object_types  = object_types,
            names         = object_names,
            recent_days   = args.recent,
            clean         = args.delete,
            dry_run       = args.dry_run,
            reporter      = ConsoleExportDbReporter(silent=args.silent),
        )
    )
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: DOCTOR")
    selected_actions = [flag for flag, selected in (("-update", args.update), ("-sqlcl", args.sqlcl), ("-init", args.init)) if selected]
    if len(selected_actions) > 1:
        joined = " and ".join(selected_actions) if len(selected_actions) == 2 else f"{', '.join(selected_actions[:-1])}, and {selected_actions[-1]}"
        print(f"Error: {joined} cannot be combined")
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
    ).run(
        DoctorRequest(
            update = args.update,
            sqlcl  = args.sqlcl,
            offline= args.offline,
            init   = args.init,
            root   = Path(args.root),
            force  = args.force,
        )
    )
    if not printed_lines:
        for line in result.lines:
            print_doctor_line(line)
    return result.exit_code


def _run_module_overview() -> int:
    return _run_static_screen(
        lambda: (
            print_adt_header("APEX DEPLOYMENT TOOL"),
            print("Modern ADT command line tool."),
            print(),
            _print_module_overview(),
        ),
        exit_code=0,
    )


def _run_invalid_command(command: str) -> int:
    def render() -> None:
        print_adt_header("APEX DEPLOYMENT TOOL: ERROR")
        print(f"Error: unknown command `{command}`.")
        print()
        if command == "init":
            print("Use:")
            print("  adtai doctor -init")
            print()
        elif command in {"update", "upgrade"}:
            print("Use one of:")
            print("  adtai doctor -update")
            print("  adtai doctor -sqlcl")
            print()
        _print_module_overview()

    return _run_static_screen(render, exit_code=1)


def _run_command_help(command: str, parser: argparse.ArgumentParser) -> int:
    def render() -> None:
        command_parser = parser._subparsers._group_actions[0].choices[command]  # noqa: SLF001
        print_adt_header(f"APEX DEPLOYMENT TOOL: {command.upper()}")
        print(command_parser.format_help(), end="")

    return _run_static_screen(render, exit_code=0)


def _run_command_argument_error(command: str, message: str) -> int:
    def render() -> None:
        print_adt_header(f"APEX DEPLOYMENT TOOL: {command.upper()}")
        print(f"Error: {message}", file=sys.stderr)
        print(file=sys.stderr)

    return _run_static_screen(render, exit_code=2)


def _run_top_level_argument_error(message: str) -> int:
    def render() -> None:
        print_adt_header("APEX DEPLOYMENT TOOL: ERROR")
        print(f"Error: {message}")
        print()
        _print_module_overview()

    return _run_static_screen(render, exit_code=2)


def _run_static_screen(render: Callable[[], object], exit_code: int) -> int:
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    tracked_stdout = original_stdout if isinstance(original_stdout, _StdoutTracker) else _StdoutTracker(original_stdout)
    tracked_stderr = original_stderr if isinstance(original_stderr, _StdoutTracker) else _StderrTracker(original_stderr, tracked_stdout)
    sys.stdout = tracked_stdout
    sys.stderr = tracked_stderr
    started_at = time.monotonic()
    try:
        render()
    finally:
        _print_completion_timer(started_at, stdout=tracked_stdout)
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return exit_code


def _is_unknown_command(value: str) -> bool:
    return not value.startswith("-") and value not in PUBLIC_COMMANDS


def _has_help_flag(values: Sequence[str]) -> bool:
    return any(value in {"-h", "--help"} for value in values)


def _module_display_name(module_name: str, aliases: tuple[str, ...]) -> str:
    return module_name if not aliases else f"{module_name} ({', '.join(aliases)})"


def _print_module_overview() -> None:
    rows = [(_module_display_name(name, aliases), description) for name, description, aliases in PUBLIC_MODULES]
    width = max(len(name) for name, _description in rows)
    print_adt_header("MODULES:")
    for module_name, description in rows:
        print(f"  {module_name:<{width}}  {description}")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_startup_context(args: argparse.Namespace) -> StartupContext:
    root = Path(args.root).expanduser().resolve()
    repo_root = _repo_root()
    config_search_paths = _config_search_paths(args.config_dir, root, repo_root)
    config_result = ConfigLoader(config_search_paths).load()
    connection_search_paths = _connection_search_paths(config_result.data, args.config_dir, root, repo_root)
    connection_file_candidates = _connection_file_candidates(config_result.data, args.config_dir, root, repo_root)
    connections = ConnectionLoader(
        connection_search_paths,
        wallet_roots=_wallet_roots(config_result.data, root, repo_root, connection_search_paths),
    ).load(candidates=connection_file_candidates)
    return StartupContext(
        root                    = root,
        repo_root               = repo_root,
        config                  = config_result.data,
        config_search_paths     = config_search_paths,
        config_dirs             = _existing_paths(config_search_paths),
        connection_search_paths = connection_search_paths,
        connection_files        = connections.files,
        connections             = connections,
        startup_sql             = _load_startup_sql(args.config_dir, root, repo_root),
    )


def _existing_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists()]


def _config_search_paths(config_dirs: list[str] | None, root: Path, repo_root: Path) -> list[Path]:
    project_paths = [Path(path).expanduser() for path in config_dirs or []]
    if not project_paths:
        project_paths = [root / "config", root]
    return [repo_root / "config", *project_paths]


def _load_startup_sql(config_dirs: list[str] | None, root: Path, repo_root: Path) -> str | None:
    from adt_ai.startup import split_statements

    project_paths = [Path(path).expanduser() for path in config_dirs or []]
    if not project_paths:
        project_paths = [root / "config", root]
    for directory in [*project_paths, repo_root / "config"]:
        candidate = directory / "STARTUP.sql"
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8")
        if split_statements(text):
            return text
    return None


def _connection_search_paths(config: dict[str, object], config_dirs: list[str] | None, root: Path, repo_root: Path) -> list[Path]:
    configured_paths = _path_list(_config_value(config, ("connections", "path")), _config_value(config, ("connections_path",)), _config_value(config, ("connections_dir",)))
    return [
        repo_root / "connections",
        repo_root / "config",
        *configured_paths,
        *[Path(path).expanduser() for path in config_dirs or []],
        root / "connections",
        root / "config",
        root,
    ]


def _wallet_roots(config: dict[str, object], root: Path, repo_root: Path, connection_search_paths: list[Path]) -> list[Path]:
    configured_paths = _path_list(_config_value(config, ("connections", "wallet_path")), _config_value(config, ("wallet_path",)))
    return [repo_root / "connections" / "wallets", *configured_paths, *[path / "wallets" for path in connection_search_paths], root / "connections" / "wallets"]


def _connection_file_candidates(config: dict[str, object], config_dirs: list[str] | None, root: Path, repo_root: Path) -> list[Path]:
    configured_file = _config_value(config, ("connections", "file")) or _config_value(config, ("connections_file",)) or _config_value(config, ("connection_file",))
    filename = str(configured_file) if configured_file else None
    override_dirs = [
        *_path_list(_config_value(config, ("connections", "path")), _config_value(config, ("connections_path",)), _config_value(config, ("connections_dir",))),
        *[Path(path).expanduser() for path in config_dirs or []],
    ]
    candidates: list[Path] = []
    for directory in override_dirs:
        if filename:
            candidates.append(directory / filename)
        else:
            candidates.append(directory / "connections.yaml")
            candidates.append(directory / f"{root.name}.yaml")
    if filename:
        candidates.extend([root / filename, root / "connections" / filename, repo_root / "connections" / filename])
    else:
        candidates.extend([root / "connections.yaml", root / "connections" / f"{root.name}.yaml", repo_root / "connections" / f"{root.name}.yaml"])
    return _dedup_paths(candidates)


def _dedup_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _with_schema_folders(config: dict[str, object], schema_export: dict[str, dict[str, object]]) -> dict[str, object]:
    schema_folders = {schema: subfolder for schema, export in schema_export.items() if (subfolder := export.get("subfolder"))}
    if not schema_folders:
        return config
    existing = config.get("schema_folders", {})
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(schema_folders)
    return {**config, "schema_folders": merged}


def _path_list(*values: object | None) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list | tuple):
            paths.extend(Path(str(item)).expanduser() for item in value)
        else:
            paths.append(Path(str(value)).expanduser())
    return paths


def _flatten_arg_groups(groups: list[list[str]] | None) -> list[str] | None:
    if not groups:
        return None
    return [part.strip() for group in groups for item in group for part in item.split(",") if part.strip()]


def _has_job_recent_conflict(recent_days: int | None, object_types: list[str]) -> bool:
    if recent_days is None:
        return False
    return any(_matches_adt_like("JOB", object_type) for object_type in object_types)


def _matches_adt_like(value: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(value.upper(), pattern.upper().replace("%", "*"))


def _print_startup_debug(context: StartupContext) -> None:
    print()
    print("STARTUP:")
    _print_debug_value("config_dirs", context.config_dirs)
    _print_debug_value("connection_files", context.connection_files)
    print()


def _print_debug_value(key: str, value: object) -> None:
    if value is None or value == "":
        return
    rendered = " | ".join(_display(item) for item in value) if isinstance(value, list) else _display(value)
    if rendered:
        print(f"  {key:<18} {rendered}")


def _debug_sql(sql: str, params: Mapping[str, object]) -> str:
    rendered = sql.strip()
    for key in sorted(params, key=len, reverse=True):
        rendered = rendered.replace(f":{key}", _debug_value(params[key]))
    return rendered


def _debug_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return str(value)


def _print_connection_block(gateway: QueryGateway, connection: object | None, *, schema: str | None = None, environment: str | None = None, debug: bool = False) -> None:
    resolved_schema = schema or str(getattr(connection, "schema", "APP"))
    resolved_environment = environment or str(getattr(connection, "environment", "DEV"))
    print_adt_header(f"CONNECTING TO SCHEMA {resolved_schema}, {resolved_environment}:")
    _print_connection_versions(gateway, connection, debug=debug)


def _print_connection_versions(gateway: QueryGateway, connection: object | None, *, debug: bool = False) -> None:
    ignore_errors = not debug
    versions: dict[str, str] = {}
    apex_version = _fetch_version(gateway, APEX_VERSION_QUERY, ignore_errors=ignore_errors)
    if apex_version:
        versions["APEX"] = apex_version
    database_version = _fetch_version(gateway, DATABASE_VERSION_QUERY, ignore_errors=ignore_errors) or _fetch_version(gateway, DATABASE_VERSION_OLD_QUERY, ignore_errors=ignore_errors)
    if database_version:
        versions["DATABASE"] = database_version
    if getattr(connection, "thick", False):
        versions["THICK"] = _oracle_client_version() or "Y"
    for key in sorted(versions):
        print(f"{key:>18} | {versions[key]}")
    if versions:
        print()


def _oracle_client_version() -> str:
    try:
        version = importlib.import_module("oracledb").clientversion()
    except Exception:
        return ""
    if isinstance(version, tuple) and len(version) >= 2:
        return f"{version[0]}.{version[1]}"
    return str(version or "")


def _fetch_version(gateway: QueryGateway, sql: str, ignore_errors: bool = False) -> str | None:
    try:
        rows = gateway.fetch_all(sql)
    except KeyboardInterrupt:
        raise
    except Exception:
        if ignore_errors:
            return None
        raise
    if not rows:
        return None
    row = rows[0]
    return str(row.get("VERSION") or row.get("version") or "") or None


def _is_user_database_error(error: Exception) -> bool:
    text = str(error)
    if any(marker in text for marker in ("DPY-", "DPI-", "ORA-", "TNS-")):
        return True
    return type(error).__module__.startswith("oracledb")


def _display(value: object) -> str:
    return DROPBOX_PATH_RE.sub("Dropbox/", str(value))


def _print_database_error(error: Exception) -> None:
    print(file=sys.stderr)
    print("DATABASE CONNECTION FAILED", file=sys.stderr)
    print("--------------------------", file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    print("Check the connection file and wallet under ADT.ai connections/wallets, then rerun.", file=sys.stderr)
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)


def _print_config_error(error: Exception) -> None:
    print(file=sys.stderr)
    print("CONFIGURATION NOT FOUND", file=sys.stderr)
    print("-----------------------", file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    print("Run ADT.ai from a project folder that has a connection file, or pass -config-dir / -root to point at one. See USAGE.md and `adtai doctor -init`.", file=sys.stderr)
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)


def _print_completion_timer(started_at: float, stdout: TextIO | None = None) -> None:
    elapsed = int(time.monotonic() - started_at + 0.5)
    output = stdout or sys.stdout
    if isinstance(output, _StdoutTracker):
        output.normalize_trailing_newlines(3)
    else:
        output.write("\n\n")
    print(f"TIMER: {elapsed}s", file=output)
    print(file=output)


def _config_value(config: dict[str, object], key_path: tuple[str, ...]) -> object | None:
    value: object = config
    for key in key_path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value
