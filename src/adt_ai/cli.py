from __future__ import annotations

import argparse
import fnmatch
import importlib
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TextIO

import yaml

from adt_ai import __version__
from adt_ai.config import ConfigError, ConfigLoader
from adt_ai.connections import ConnectionError as ConnectionConfigError
from adt_ai.connections import ConnectionLoader, ConnectionResult
from adt_ai.db import OracleGateway, QueryGateway
from adt_ai.discovery.render import DEFAULT_ROW_LIMIT
from adt_ai.discovery.runner import (
    RESULT_BLOCK_END,
    RESULT_BLOCK_START,
    DiscoveryRequest,
    DiscoveryRunner,
)
from adt_ai.doctor.runner import ActionReporter, DoctorRequest, DoctorRunner, format_action_line
from adt_ai.export_apex.inventory import (
    ApexApplication,
    ApexDiscovery,
    ApexOwnerCount,
    ApexWorkspace,
)
from adt_ai.export_apex.runner import ApexExportRequest, ApexExportRunner
from adt_ai.export_data.inventory import DataTable
from adt_ai.export_data.runner import ExportDataRequest, ExportDataRunner
from adt_ai.export_db.runner import (
    ConsoleExportDbReporter,
    ExportDbRequest,
    ExportDbRunner,
    GatewayFactory,
    print_adt_header,
    print_adt_table,
)
from adt_ai.rebuild.runner import (
    REVEAL_DEFAULT_LIMIT,
    BranchInfo,
    RebuildRequest,
    RebuildRunner,
    _current_branch,
    branch_commits,
    reveal_branches,
    switch_to_branch,
)
from adt_ai.recompile.inventory import ObjectOverview
from adt_ai.recompile.runner import RecompileRequest, RecompileRunner
from adt_ai.search_repo.runner import SearchRepoError, SearchRepoRequest, SearchRepoRunner

PUBLIC_MODULES = (
    ("export_db", "export database objects", ()),
    ("doctor", "check local setup and run explicit updates", ()),
    ("export_apex", "export APEX applications", ()),
    ("export_data", "export table data", ()),
    ("recompile", "recompile invalid database objects", ()),
    ("rebuild", "rebuild the git commit cache", ()),
    ("search_repo", "search cached Git commit history", ()),
    ("discovery", "run read-only SELECT discovery queries", ()),
)

PUBLIC_COMMANDS = tuple(
    command
    for module_name, _description, aliases in PUBLIC_MODULES
    for command in (module_name, *aliases)
)

REMOVED_COMPATIBILITY_FLAGS = {
    "patch": ("-head", "--head", "-local", "--local"),
    "recompile": ("-key", "--key"),
}

APEX_VERSION_QUERY = """
SELECT
    a.version_no AS version
FROM apex_release a
""".strip()

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

APEX_EXPORT_ACTIONS = ("full", "split", "readable", "embedded", "rest", "files", "files_ws")

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

    @property
    def trailing_newlines(self) -> int:
        return len(self._pending_newlines)

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
        # Flush the visible body immediately. A header line printed right before a
        # long silent operation (e.g. rebuild's per-commit hashing) carries its
        # line-ending newline in _pending_newlines, so without this flush the body
        # stays in the TTY line buffer — invisible until the first progress line
        # commits the pending newline. Flushing only the already-written body keeps
        # the trailing newlines retractable for the shared footer normalizer.
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
        # Keep trailing newlines retractable: a mid-stream flush (e.g. the
        # progress bar's print(flush=True)) must not commit the line-ending
        # newlines, or normalize_trailing_newlines() can no longer trim them
        # and the shared footer ends up with an extra blank line. Pending
        # newlines are emitted when real content follows or at finalize().
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

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        return self.wrapped.fetch_all(sql, params)

    def execute(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
    ) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    parser = AdtArgumentParser(
        prog="adt",
        description="Modern ADT command line tool.",
    )
    parser.add_argument("--version", action="store_true", help="show version and exit")
    subparsers = parser.add_subparsers(
        dest         = "command",
        parser_class = AdtArgumentParser,
    )

    export_db = subparsers.add_parser(
        "export_db",
        description="export database objects",
        help="export database objects",
    )
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

    doctor = subparsers.add_parser(
        "doctor",
        description="check local ADT.ai environment setup and run explicit updates",
        help="check local setup and run explicit updates",
    )
    doctor.add_argument("-offline", action="store_true", help="skip online update checks and show local versions only")
    doctor.add_argument("-update", action="store_true", help="run full ADT.ai, requirements, and SQLcl upgrade")
    doctor.add_argument("-sqlcl", action="store_true", help="upgrade SQLcl only; runs immediately without -update")
    doctor.add_argument("-init", action="store_true", help="scaffold project config, ignore rules, and safe local folders")
    doctor.add_argument("--root", "-root", default=".", help="project root folder for -init")
    doctor.add_argument("--force", "-force", action="store_true", help="overwrite existing generated template files with -init")

    export_apex = subparsers.add_parser(
        "export_apex",
        description="export APEX applications",
        help="export APEX applications",
    )
    export_apex.add_argument("--root", "-root", default=".", help="output root folder")
    export_apex.add_argument("--config-dir", "-config-dir", action="append", help="folder containing config YAML")
    export_apex.add_argument("--env", "-env", help="connection environment")
    export_apex.add_argument("--schema", "-schema", action="append", help="APEX owner schema")
    export_apex.add_argument("--ws", "-ws", help="APEX workspace")
    export_apex.add_argument("--group", "-group", help="APEX application group")
    export_apex.add_argument("--app", "-app", action="append", nargs="+", help="application id(s) to export or reveal")
    export_apex.add_argument("--max-app-id", "--max_app_id", "-max_app_id", dest="max_app_id", type=int, help="only list apps with application_id below ID (hides temp/backup apps)")
    export_apex.add_argument("--recent", "-recent", nargs="?", const=1, type=int, help="show components changed in the last DAYS days")
    export_apex.add_argument("--by", "-by", nargs="?", const="", help="show components changed by developer")
    export_apex.add_argument("--release", "-release", help="override APEX release in SQL exports")
    export_apex.add_argument("--reveal", "-reveal", action="store_true", help="show matching APEX workspaces and applications")
    export_apex.add_argument("--owners", "-owners", action="store_true", help="in reveal mode, list app counts for all owners, not just configured schemas")
    export_apex.add_argument("--all", "-all", action="store_true", dest="all_formats", help="export all APEX formats")
    export_apex.add_argument("--full", "-full", action="store_true", help="export full application SQL")
    export_apex.add_argument("--split", "-split", action="store_true", help="export split application source")
    export_apex.add_argument("--readable", "-readable", action="store_true", help="export readable YAML source")
    export_apex.add_argument("--embedded", "-embedded", action="store_true", help="export embedded code report")
    export_apex.add_argument("--rest", "-rest", action="store_true", help="export REST services")
    export_apex.add_argument("--files", "-files", action="store_true", help="export application files")
    export_apex.add_argument("--files-ws", "--files_ws", "-files_ws", action="store_true", dest="files_ws", help="export workspace files")
    export_apex.add_argument("--debug", "-debug", action="store_true", help="show input parameters and SQL queries with bind values")

    export_data = subparsers.add_parser(
        "export_data",
        description="export table data",
        help="export table data",
    )
    export_data.add_argument("--root", "-root", default=".", help="output root folder")
    export_data.add_argument("--config-dir", "-config-dir", action="append", help="folder containing config YAML")
    export_data.add_argument("--env", "-env", help="connection environment")
    export_data.add_argument("--schema", "-schema", action="append", help="schema to export")
    export_data.add_argument("--name", "-name", action="append", nargs="+", help="table name pattern(s) to export, supports %% wildcards")
    export_data.add_argument("--debug", "-debug", action="store_true", help="show input parameters and SQL queries with bind values")

    recompile = subparsers.add_parser(
        "recompile",
        description="recompile invalid database objects",
        help="recompile invalid database objects",
    )
    recompile.add_argument("--root", "-root", default=".", help="project root folder")
    recompile.add_argument("--config-dir", "-config-dir", action="append", help="folder containing config YAML")
    recompile.add_argument("--env", "-env", help="connection environment")
    recompile.add_argument("--target", "-target", help="connection environment (alias of -env)")
    recompile.add_argument("--schema", "-schema", help="schema to recompile")
    recompile.add_argument("--type", "-type", default="%", help="object type pattern to recompile, supports %% wildcards")
    recompile.add_argument("--name", "-name", default="%", help="object name pattern to recompile, supports %% wildcards")
    recompile.add_argument("--force", "-force", action="store_true", help="recompile all matching objects, not just invalid ones")
    recompile.add_argument("--level", "-level", type=int, help="PL/SQL optimize level (1-3)")
    recompile.add_argument("--native", "-native", action="store_true", help="compile PL/SQL to native code")
    recompile.add_argument("--interpreted", "-interpreted", action="store_true", help="compile PL/SQL to interpreted code (default)")
    recompile.add_argument("--scope", "-scope", nargs="*", help="PL/Scope settings (IDENTIFIERS, STATEMENTS, ALL)")
    recompile.add_argument("--warnings", "-warnings", nargs="*", help="PL/SQL warnings (SEVERE, PERF, INFO)")
    recompile.add_argument("--silent", "-silent", action="store_true", help="suppress object overview details; keep required command chrome")
    recompile.add_argument("--debug", "-debug", action="store_true", help="show input parameters and SQL queries with bind values")

    rebuild = subparsers.add_parser(
        "rebuild",
        description="rebuild the git commit cache for the current branch",
        help="rebuild the git commit cache",
    )
    rebuild.add_argument("--root", "-root", default=".", help="project root folder")
    rebuild.add_argument("--branch", "-branch", action="append", nargs="+", help="branch name(s) to include; default is the current branch")
    rebuild.add_argument("--reveal", "-reveal", nargs="*", default=None, metavar="WORD", help="list the remote branches (origin/*) without touching the cache, newest first")
    rebuild.add_argument("--limit", "-limit", type=int, default=None, metavar="N", help=f"max rows or commits depending on mode (default {REVEAL_DEFAULT_LIMIT}; 0 = all)")
    rebuild.add_argument("--since", "-since", metavar="WHEN", help="rebuild every commit since WHEN; accepts YYYY-MM-DD or days back")
    rebuild.add_argument("--my", "-my", dest="my", action="store_true", help="in reveal mode, limit to branches whose tip commit is yours")
    rebuild.add_argument("--switch", "-switch", nargs="?", type=int, const=1, default=None, metavar="N", help="in reveal mode, check out the Nth filtered branch")

    search_repo = subparsers.add_parser(
        "search_repo",
        description="search cached Git commit history",
        help="search cached Git commit history",
    )
    search_repo.add_argument("--root", "-root", default=".", help="project root folder")
    search_repo.add_argument("--branch", "-branch", help="branch or ref to search")
    search_repo.add_argument("--limit", "-limit", type=int, default=REVEAL_DEFAULT_LIMIT, metavar="N", help=f"max commits to print (default {REVEAL_DEFAULT_LIMIT}; 0 = all)")
    search_repo.add_argument("--files", "-files", nargs="?", type=int, const=20, default=None, metavar="N", help="print at most N changed files per commit; file selectors auto-print 20")
    search_repo.add_argument("--summary", "-summary", nargs="*", help="summary word(s), AND-matched case-insensitively")
    search_repo.add_argument("--file", "-file", nargs="*", help="file path word(s), AND-matched case-insensitively")
    search_repo.add_argument("--type", "-type", action="append", help="object type text")
    search_repo.add_argument("--name", "-name", action="append", help="object name text")
    search_repo.add_argument("--by", "-by", action="append", help="author email/name text")
    search_repo.add_argument("--my", "-my", action="store_true", help="show only my commits")
    search_repo.add_argument("--commit", "--commits", "-commit", "-commits", dest="commit_refs", action="append", nargs="+", help="commit number/hash ref(s); N+ selects N and newer")
    search_repo.add_argument("--hash", "-hash", action="append", nargs="+", help="commit hash prefix(es)")
    search_repo.add_argument("--recent", "-recent", type=int, help="only commits from recent DAYS")
    search_repo.add_argument("--since", "-since", help="oldest commit date, YYYY-MM-DD")
    search_repo.add_argument("--until", "-until", help="newest commit date, YYYY-MM-DD")
    search_repo.add_argument("--restore", "-restore", action="store_true", help="write matching historical file versions next to the original files")
    search_repo.add_argument("--stage", "-stage", action="store_true", help="with -restore, restore to original paths and git add them")

    discovery = subparsers.add_parser(
        "discovery",
        description="run read-only SELECT discovery queries against the target database",
        help="run read-only SELECT discovery queries",
    )
    discovery.add_argument("--root", "-root", default=".", help="project root folder")
    discovery.add_argument("--config-dir", "-config-dir", action="append", help="folder containing config YAML")
    discovery.add_argument("--env", "-env", help="connection environment")
    discovery.add_argument("--schema", "-schema", help="schema to query")
    discovery.add_argument("--sql", "-sql", help="a single SELECT statement to run")
    discovery.add_argument("--file", "-file", dest="statements_file", help="path to a file of ;-separated SELECT statements")
    discovery.add_argument("--limit", "-limit", type=int, default=DEFAULT_ROW_LIMIT, help=f"max rows rendered per query (default: {DEFAULT_ROW_LIMIT})")
    discovery.add_argument("--no-log", "-nolog", dest="no_log", action="store_true", help="run queries and print results without writing a discovery report")
    discovery.add_argument("--debug", "-debug", action="store_true", help="show input parameters and SQL queries with bind values")

    return parser


def _canonical_command_name(command: str) -> str:
    for module_name, _description, aliases in PUBLIC_MODULES:
        if command == module_name or command in aliases:
            return module_name
    return command


def _command_title(command: str) -> str:
    return _canonical_command_name(command).upper()


def _command_parser(parser: argparse.ArgumentParser, command: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[command]
    raise KeyError(command)


def _has_help_flag(argv: Sequence[str]) -> bool:
    return any(arg in {"-h", "--help"} for arg in argv)


def _apply_generated_command_usages(parser: argparse.ArgumentParser) -> None:
    for command, _description, _aliases in PUBLIC_MODULES:
        command_parser = _command_parser(parser, command)
        command_parser.usage = _generated_command_usage(command, command_parser)


def _generated_command_usage(command: str, parser: argparse.ArgumentParser) -> str:
    tokens = [
        _usage_token(action)
        for action in parser._actions
        if action.option_strings
    ]
    return f"adt {command} {' '.join(tokens)}"


def _usage_token(action: argparse.Action) -> str:
    option = _preferred_usage_option(action)
    suffix = _usage_argument_suffix(action)
    return f"[{option}{suffix}]"


def _preferred_usage_option(action: argparse.Action) -> str:
    return next(
        (
            option
            for option in action.option_strings
            if option.startswith("-") and not option.startswith("--")
        ),
        action.option_strings[0],
    )


def _usage_argument_suffix(action: argparse.Action) -> str:
    if action.nargs == 0 or isinstance(
        action,
        (
            argparse._StoreTrueAction,
            argparse._StoreFalseAction,
            argparse._HelpAction,
        ),
    ):
        return ""

    metavar = _usage_metavar(action)
    if action.nargs == "?":
        return f" [{metavar}]"
    if action.nargs == "*":
        return f" [{metavar} ...]"
    if action.nargs == "+":
        return f" {metavar} [{metavar} ...]"
    if isinstance(action.nargs, int):
        return " " + " ".join([metavar] * action.nargs)
    return f" {metavar}"


def _usage_metavar(action: argparse.Action) -> str:
    if action.metavar is not None:
        if isinstance(action.metavar, tuple):
            return str(action.metavar[0])
        return str(action.metavar)
    if action.choices is not None:
        return "{" + ",".join(str(choice) for choice in action.choices) + "}"
    return action.dest.upper()


def _removed_compatibility_args(command: str, argv: Sequence[str]) -> list[str]:
    removed = set(REMOVED_COMPATIBILITY_FLAGS.get(_canonical_command_name(command), ()))
    rejected: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in removed:
            rejected.append(arg)
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                rejected.append(argv[index + 1])
                index += 1
        index += 1
    return rejected


def _run_static_screen(
    render: Callable[[], None],
    *,
    exit_code: int = 0,
    timer_stdout: TextIO | None = None,
) -> int:
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    tracked_stdout = (
        original_stdout
        if isinstance(original_stdout, _StdoutTracker)
        else _StdoutTracker(original_stdout)
    )
    tracked_stderr = (
        original_stderr
        if isinstance(original_stderr, _StdoutTracker)
        else _StderrTracker(original_stderr, tracked_stdout)
    )
    sys.stdout = tracked_stdout
    sys.stderr = tracked_stderr
    started_at = time.monotonic()
    try:
        render()
    finally:
        footer_stdout = tracked_stderr if exit_code != 0 and tracked_stderr.had_output else (timer_stdout or tracked_stdout)
        _print_completion_timer(started_at, stdout=footer_stdout)
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return exit_code


def _run_command_help(command: str, parser: argparse.ArgumentParser) -> int:
    def render() -> None:
        print_adt_header(f"APEX DEPLOYMENT TOOL: {_command_title(command)}")
        print(_command_parser(parser, command).format_help(), end="")

    return _run_static_screen(render)


def _run_command_argument_error(command: str, message: str) -> int:
    def render() -> None:
        print_adt_header(f"APEX DEPLOYMENT TOOL: {_command_title(command)}", file=sys.stderr)
        print(f"{command}: error: {message}", file=sys.stderr)
        print(file=sys.stderr)

    return _run_static_screen(render, exit_code=2)


def _run_top_level_argument_error(message: str) -> int:
    def render() -> None:
        print_adt_header("APEX DEPLOYMENT TOOL: ERROR")
        print(f"Error: {message}")
        print()
        _print_module_overview()

    return _run_static_screen(render, exit_code=2)


def main(
    argv: Sequence[str] | None = None,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv or raw_argv in (["-h"], ["--help"]):
        return _run_module_overview()
    if raw_argv and _is_unknown_command(raw_argv[0]):
        return _run_invalid_command(raw_argv[0])

    parser = build_parser()
    if raw_argv[0] in PUBLIC_COMMANDS:
        removed_args = _removed_compatibility_args(raw_argv[0], raw_argv[1:])
        if removed_args:
            return _run_command_argument_error(
                raw_argv[0],
                f"unrecognized arguments: {' '.join(removed_args)}",
            )
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
    tracked_stdout = (
        original_stdout
        if isinstance(original_stdout, _StdoutTracker)
        else _StdoutTracker(original_stdout)
    )
    tracked_stderr = (
        original_stderr
        if isinstance(original_stderr, _StdoutTracker)
        else _StderrTracker(original_stderr, tracked_stdout)
    )
    sys.stdout = tracked_stdout
    sys.stderr = tracked_stderr
    started_at = time.monotonic()
    timer_stdout = _command_timer_stdout(args, tracked_stdout)
    exit_code = 0
    try:
        if args.command == "calendar":
            exit_code = _run_calendar(args)
        elif args.command == "export_db":
            exit_code = _run_export_db(args, gateway_factory=gateway_factory)
        elif args.command == "export_data":
            exit_code = _run_export_data(args, gateway_factory=gateway_factory)
        elif args.command == "export_apex":
            exit_code = _run_export_apex(args, gateway_factory=gateway_factory)
        elif args.command == "patch":
            exit_code = _run_patch(args, gateway_factory=gateway_factory)
        elif args.command == "diff":
            exit_code = _run_diff(args, gateway_factory=gateway_factory)
        elif args.command == "rebuild":
            exit_code = _run_rebuild(args)
        elif args.command == "search_repo":
            exit_code = _run_search_repo(args)
        elif args.command == "recompile":
            exit_code = _run_recompile(args, gateway_factory=gateway_factory)
        elif args.command == "doctor":
            exit_code = _run_doctor(args)
        elif args.command in {"dependencies", "depends"}:
            exit_code = _run_dependencies(args, gateway_factory=gateway_factory)
        elif args.command == "discovery":
            exit_code = _run_discovery(args, gateway_factory=gateway_factory)
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
        footer_stdout = (
            tracked_stderr
            if exit_code != 0 and tracked_stderr.had_output
            else timer_stdout
        )
        _print_completion_timer(started_at, stdout=footer_stdout)
        _notify_completion(args, exit_code)
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    return exit_code


def _is_unknown_command(value: str) -> bool:
    return not value.startswith("-") and value not in PUBLIC_COMMANDS


def _module_display_name(module_name: str, aliases: tuple[str, ...]) -> str:
    if not aliases:
        return module_name
    return f"{module_name} ({', '.join(aliases)})"


def _print_module_overview() -> None:
    module_rows = [
        (_module_display_name(module_name, aliases), description)
        for module_name, description, aliases in PUBLIC_MODULES
    ]
    module_width = max(len(module_name) for module_name, _description in module_rows)

    print_adt_header("MODULES:")
    for module_name, description in module_rows:
        print(f"  {module_name:<{module_width}}  {description}")


def _run_module_overview() -> int:
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    tracked_stdout = (
        original_stdout
        if isinstance(original_stdout, _StdoutTracker)
        else _StdoutTracker(original_stdout)
    )
    tracked_stderr = (
        original_stderr
        if isinstance(original_stderr, _StdoutTracker)
        else _StderrTracker(original_stderr, tracked_stdout)
    )
    sys.stdout = tracked_stdout
    sys.stderr = tracked_stderr
    started_at = time.monotonic()
    try:
        print_adt_header("APEX DEPLOYMENT TOOL")
        print("Modern ADT command line tool.")
        print()
        _print_module_overview()
    finally:
        _print_completion_timer(started_at, stdout=tracked_stdout)
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return 0


def _run_invalid_command(command: str) -> int:
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    tracked_stdout = (
        original_stdout
        if isinstance(original_stdout, _StdoutTracker)
        else _StdoutTracker(original_stdout)
    )
    tracked_stderr = (
        original_stderr
        if isinstance(original_stderr, _StdoutTracker)
        else _StderrTracker(original_stderr, tracked_stdout)
    )
    sys.stdout = tracked_stdout
    sys.stderr = tracked_stderr
    started_at = time.monotonic()
    try:
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
    finally:
        _print_completion_timer(started_at, stdout=tracked_stdout)
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return 1


def _command_timer_stdout(args: argparse.Namespace, stdout: TextIO) -> TextIO:
    if args.command in {"dependencies", "depends"} and getattr(args, "format", "table") != "table":
        return sys.stderr
    return stdout


def console_main() -> None:
    raise SystemExit(main())


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


def _run_diff(args: argparse.Namespace, *, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: DIFF")

    missing = [
        flag
        for flag, value in (("-source", args.source), ("-target", args.target))
        if not value
    ]
    if missing:
        print(
            f"diff: the following arguments are required: {', '.join(missing)}",
            file=sys.stderr,
        )
        print(file=sys.stderr)
        return 2

    startup = _load_startup_context(args)
    root = startup.root
    connections = startup.connections

    source_schema = getattr(args, "source_schema", None)
    target_schema = getattr(args, "target_schema", None)
    source_conn   = connections.resolve(environment=args.source, schema=source_schema)
    target_conn   = connections.resolve(environment=args.target, schema=target_schema)
    artifact_out  = Path(args.out).expanduser().resolve() if args.out else root / "diff_output"

    def _diff_gateway(connection: object) -> QueryGateway:
        if gateway_factory:
            gateway = gateway_factory(connection.schema)
        else:
            gateway = OracleGateway(connection, startup_sql=startup.startup_sql)
        return DebugQueryGateway(gateway) if args.debug else gateway

    for connection in (source_conn, target_conn):
        _print_connection_block(_diff_gateway(connection), connection, debug=args.debug)

    if args.debug:
        _print_startup_debug(startup)

    try:
        result = DiffRunner().run(
            DiffRequest(
                source       = source_conn,
                target       = target_conn,
                artifact_out = artifact_out,
                verbose      = args.verbose,
                debug        = args.debug,
                project_root = root,
            )
        )
    except RuntimeError as error:
        if args.debug:
            raise
        print_adt_header("DIFF FAILED")
        print(str(error))
        return 1

    if result.success:
        print_adt_header("DIFF COMPLETE")
        print(f"  Artifact: {_display(result.artifact_path)}")
        print()
        print("  Deploy with:")
        print(f"    sql {target_conn.username}/\"<pwd>\"@<service>")
        print(f"    project deploy -file {_display(result.artifact_path)}")
        print()
    else:
        print_adt_header("DIFF FAILED")
        if result.output:
            print(result.output)

    return 0 if result.success else 1


class ConsoleRebuildReporter:
    def __init__(self, branch_label: str, since_label: str | None = None) -> None:
        self.branch_label = branch_label
        self.since_label = since_label
        self._started_at: float | None = None
        self._progress = DottedProgressBar()

    def on_count(
        self,
        total_commits: int,
        branch_count: int,
        commit_limit: int | None = None,
        missing_commits: int | None = None,
    ) -> None:
        print(f"    BRANCH | {self.branch_label}")
        if self.since_label is not None:
            # `-since` window: the total is the count of commits in the window.
            print(f"   COMMITS | {total_commits} SINCE {self.since_label}")
        elif missing_commits is not None:
            print(f"   COMMITS | {total_commits} + {missing_commits}")
        elif commit_limit is not None:
            print(f"   COMMITS | {total_commits} - {commit_limit}")
        else:
            print(f"   COMMITS | {total_commits}")
        print()

    def on_commit_start(self, index: int, total: int) -> None:
        import time

        if self._started_at is None:
            self._started_at = time.monotonic()

    def on_commit(self, index: int, total: int) -> None:
        import time

        # Nothing to rebuild (e.g. -update with no new commits) -> instant 100%.
        if total <= 0:
            self._progress.print_line("REBUILDING", 100, 0, close=True)
            return

        fraction = index / total
        percent  = min(int((fraction * 100) + 0.5), 100)
        elapsed  = time.monotonic() - self._started_at
        remaining = (elapsed / index) * (total - index) if index else 0.0
        seconds  = int(elapsed if index == total else remaining)

        self._progress.print_line("REBUILDING", percent, seconds, close=index == total)


def _resolve_since(value: str, *, option: str = "-since") -> str:
    # `-since` accepts a YYYY-MM-DD date or an integer number of days back
    # (e.g. '7' -> 7 days ago). Both resolve to an ISO date string that bounds
    # the rebuild window via `git log --since`.
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{option}: '{value}' is not a valid date") from exc
        return text
    if re.fullmatch(r"\d+", text):
        return (date.today() - timedelta(days=int(text))).isoformat()
    raise ValueError(
        f"{option}: '{value}' must be a YYYY-MM-DD date or a number of days back"
    )


def _run_rebuild(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    print_adt_header("APEX DEPLOYMENT TOOL: REBUILD")

    since_value = getattr(args, "since", None)
    since_date: str | None = None
    if since_value is not None:
        try:
            since_date = _resolve_since(since_value)
        except ValueError as exc:
            print(f"Error: {exc}")
            print()
            return 1

    if getattr(args, "reveal", None) is not None:
        # In reveal mode `-since` is a date filter on each branch's tip commit and
        # `-limit` caps the rows — they are orthogonal, so (unlike normal mode)
        # they compose instead of conflicting.
        return _run_rebuild_reveal(args, root, since_date)

    if getattr(args, "switch", None) is not None:
        print("Error: -switch only works with -reveal")
        print()
        return 1

    if since_date is not None and args.limit is not None:
        print("Error: -since and -limit cannot be combined")
        print()
        return 1

    try:
        config = ConfigLoader(
            _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
        ).load().data
        cache_file_template = str(
            config.get("repo_commits_file") or "./config/commits/#BRANCH#.yaml"
        )
        branches = _flatten_arg_groups(args.branch)
        branch_label = _rebuild_branch_label(root, branches)
        # Default mode is an incremental update since the last cached commit.
        # An explicit -limit window runs a full bounded rebuild instead. (In
        # normal mode `-limit` is the per-branch commit cap; in reveal mode it
        # is the branch-row count — see _run_rebuild_reveal.)
        # A `-since` window is a full bounded rebuild, never an incremental
        # update (same as an explicit -limit).
        update_only = args.limit is None and since_date is None
        request = RebuildRequest(
            root               = root,
            commit_limit       = args.limit,
            branches           = branches,
            cache_file_template= cache_file_template,
            update_only        = update_only,
            since_date         = since_date,
        )
        RebuildRunner().run(
            request,
            reporter=ConsoleRebuildReporter(branch_label, since_label=since_date),
        )
    except Exception as exc:
        print(f"Error: {exc}")
        print()
        return 1

    return 0


def _run_search_repo(args: argparse.Namespace) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: SEARCH_REPO")
    try:
        file_limit = _search_repo_file_limit(args)
        result = SearchRepoRunner().run(
            SearchRepoRequest(
                root          = Path(args.root).resolve(),
                branch        = args.branch,
                commit_limit  = None if args.limit == 0 else args.limit,
                show_files    = file_limit > 0,
                file_limit    = file_limit,
                summary_terms = args.summary or [],
                file_terms    = args.file or [],
                object_types  = args.type or [],
                object_names  = args.name or [],
                authors       = args.by or [],
                commit_refs   = _flatten_arg_groups(args.commit_refs),
                hash_refs     = _flatten_arg_groups(args.hash),
                since         = _resolve_since(args.since, option="-since") if args.since else None,
                until         = _resolve_since(args.until, option="-until") if args.until else None,
                recent        = args.recent,
                my            = args.my,
                restore       = args.restore,
                stage         = args.stage,
            )
        )
    except (SearchRepoError, ValueError) as exc:
        print(f"Error: {exc}")
        print()
        return 1
    if result.records:
        print_adt_header("COMMITS:")
        for record in result.records:
            print(f"{record.number}) {record.summary}")
            print(
                f"  {record.author} | {_display_commit_date(record.date)} | "
                f"{record.commit_hash[:8]}"
            )
            if file_limit > 0:
                file_paths = record.files[:file_limit]
                for file_path in file_paths:
                    status = record.file_statuses.get(file_path, "M")
                    print(f"    - {status} | {file_path}")
            print()
    else:
        print("No commits found.")

    if result.restored_files:
        print_adt_header("RESTORED FILES:")
        root = Path(args.root).resolve()
        for path in result.restored_files:
            print(f"  - {_relative_display(root, path)}")
    return 0


def _run_calendar(args: argparse.Namespace) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: CALENDAR")
    try:
        result = CalendarRunner().run(
            CalendarRequest(
                root      = Path(args.root).resolve(),
                branch    = args.branch,
                month     = _resolve_calendar_month(args.month) if args.month else None,
                offset    = args.calendar_offset or 0,
                authors   = args.by or [],
                my        = args.my,
                list_mode = args.list,
            )
        )
    except (CalendarError, ValueError) as exc:
        print(f"Error: {exc}")
        print()
        return 1

    print_adt_header(f"MONTHLY OVERVIEW: {result.month}")
    if not result.authors:
        print("No patch commits found.")
        return 0
    for author in result.authors:
        print(f"  {author.author:<49} {author.commit_count}")

    for author in result.authors:
        print()
        print_adt_header(
            f"{author.commit_count} COMMITS BY {author.author} ({author.ticket_count})"
        )
        if args.list:
            for day, tickets in author.days.items():
                print(f"{day} {', '.join(tickets)}")
            print()
        else:
            _print_calendar_grid(result.month, author.days)
    return 0


def _resolve_calendar_month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError(f"-month: '{value}' must be YYYY-MM")
    datetime.strptime(f"{value}-01", "%Y-%m-%d")
    return value


def _print_calendar_grid(month: str, days: dict[str, list[str]]) -> None:
    first = datetime.strptime(f"{month}-01", "%Y-%m-%d").date()
    curr = first - timedelta(days=first.weekday())
    while True:
        week_days = [(curr + timedelta(days=index)).isoformat() for index in range(5)]
        curr += timedelta(days=7)
        if not any(day.startswith(month) for day in week_days):
            if curr.month != first.month:
                break
            continue
        print(" | ".join(day if day.startswith(month) else " " * 10 for day in week_days))
        rows = max((len(days.get(day, [])) for day in week_days), default=0)
        for row_index in range(rows):
            print(
                " | ".join(
                    (
                        days.get(day, [])[row_index]
                        if row_index < len(days.get(day, []))
                        else ""
                    ).ljust(10)
                    for day in week_days
                )
            )
        print()


def _search_repo_file_limit(args: argparse.Namespace) -> int:
    if args.files is not None:
        return args.files
    if args.file or args.type or args.name:
        return 20
    return 0


def _flatten_arg_groups(values: list[list[str]] | None) -> list[str]:
    return [item for group in (values or []) for item in group]


def _display_commit_date(value: str) -> str:
    return value[:16].replace("T", " ")


def _relative_display(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return _display(path)


def _rebuild_branch_label(root: Path, branches: list[str]) -> str:
    if branches:
        return ", ".join(branches)
    return _current_branch(root)


def _run_rebuild_reveal(
    args: argparse.Namespace, root: Path, since_date: str | None = None
) -> int:
    # Read-only branch inspector: lists branches, never touches the cache.
    # `-reveal` carries the filter words (AND-matched); `-limit` caps the rows;
    # `-since` keeps only branches whose tip commit is on or after the cutoff.
    # The shared `-limit` default is None (so normal mode can detect "absent" for
    # incremental rebuilds), so resolve the reveal default here: absent -> 10,
    # 0 -> all.
    patterns = list(args.reveal or [])
    mine = bool(getattr(args, "my", False))

    # `-switch` takes over the whole report: instead of listing branches it checks
    # one out and shows that branch's recent commits. There `-limit` caps the
    # commit list, so the branch selection runs against the full filtered list.
    switch = getattr(args, "switch", None)
    if switch is not None:
        return _run_rebuild_switch(args, root, since_date, patterns, mine, switch)

    count = getattr(args, "limit", None)
    if count is None:
        count = REVEAL_DEFAULT_LIMIT
    limit = count if count and count > 0 else None
    try:
        result = reveal_branches(
            root, patterns=patterns, mine=mine, since=since_date, limit=limit
        )
    except Exception as exc:
        print(f"Error: {exc}")
        print()
        return 1

    # `-since` is rendered as a trailing ` SINCE <date>` on whichever title the
    # word/`-my` filters produced.
    since_part = f" SINCE {result.since}" if result.since else ""
    if result.patterns:
        mine_suffix = " (mine)" if result.mine else ""
        title = f"BRANCHES MATCHING {' '.join(result.patterns)}{mine_suffix}{since_part}"
    else:
        mine_prefix = "MY " if result.mine else ""
        title = f"{mine_prefix}RECENT BRANCHES{since_part}"
    _print_reveal_list(title, result.branches, result.total)
    return 0


def _run_rebuild_switch(
    args: argparse.Namespace,
    root: Path,
    since_date: str | None,
    patterns: list[str],
    mine: bool,
    switch: int,
) -> int:
    # Select the branch against the FULL filtered list (no row cap) so the rank
    # resolves regardless of `-limit` — which here caps the COMMITS section, not
    # the branch list. The list itself is not printed; the switched branch and its
    # commits are.
    try:
        result = reveal_branches(
            root, patterns=patterns, mine=mine, since=since_date, limit=None
        )
    except Exception as exc:
        print(f"Error: {exc}")
        print()
        return 1

    if switch < 1 or switch > len(result.branches):
        upper = len(result.branches)
        print(f"Error: -switch {switch} is out of range (1..{upper})")
        print()
        return 1
    target = result.branches[switch - 1]

    # Skip the checkout entirely when we're already on the branch — no git ops,
    # so in-flight WiP is left exactly where it is.
    if _current_branch(root) != target.name:
        try:
            switch_to_branch(root, target.name)
        except Exception as exc:
            print(f"Error: {exc}")
            print()
            return 1

    print_adt_header("BRANCH SWITCHED:")
    print()
    print(f"  {target.name}"[:SWITCH_LINE_WIDTH])

    count = getattr(args, "limit", None)
    if count is None:
        count = REVEAL_DEFAULT_LIMIT
    commit_limit = count if count and count > 0 else None
    commits = branch_commits(root, target.name, limit=commit_limit, mine=mine)
    print()  # second blank line above the header — print_adt_header adds one, we want two
    print_adt_header("COMMITS:")
    print()
    if not commits:
        print("(none)")
        print()
        return 0
    for when, subject in commits:
        print(f"  {when} | {subject}"[:SWITCH_LINE_WIDTH])
    print()
    return 0


# `-reveal` shows branch names only, clipped to this width so long feature
# branch names don't wrap the report.
REVEAL_NAME_WIDTH = 78

# `-switch` prints the branch name and each commit line flush-left, every line
# capped at this width so a long branch name or commit subject can't wrap.
SWITCH_LINE_WIDTH = 78


def _print_reveal_list(
    title: str,
    branches: list[BranchInfo],
    total: int,
) -> None:
    # The count rides in the header: `(<shown>/<total>)` when the list is capped,
    # otherwise just `(<total>)`. No separate "showing N of M" trailer line.
    shown = len(branches)
    if total and shown < total:
        count = f"({shown}/{total})"
    elif total:
        count = f"({total})"
    else:
        count = ""
    print_adt_header(f"{title}:", count)
    print()
    if not branches:
        print("  (none)")
        print()
        return
    # Print branch names directly (no `BRANCH` column header / dashed rule),
    # two-space-indented to match the switch output. Same for -my and non-my.
    for branch in branches:
        print(f"  {branch.name}"[:REVEAL_NAME_WIDTH])
    print()


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
        debug          = args.debug,
    )

    result = RecompileRunner(recompile_gateway_factory).run(request)

    if not silent:
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


_FILE_RESULT_RE = re.compile(r"\n/\*\n.*?\*/", re.DOTALL)


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

def _run_patch(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: PATCH")
    try:
        return _run_patch_command(args, gateway_factory)
    except PatchError as error:
        if args.debug:
            raise
        print_adt_header("PATCH FAILED")
        print(str(error))
        return 1


def _run_patch_command(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None,
) -> int:
    root = Path(args.root).expanduser().resolve()
    workspace = PatchWorkspace(root)
    if args.deldiff:
        config = ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
        gateway, schema, environment, connection = _patch_delete_diff_gateway(
            args, root, config, gateway_factory
        )
        _print_connection_block(gateway, connection, debug=args.debug)
        if args.debug:
            _print_startup_debug(_load_startup_context(args))
        dropped = workspace.delete_diff_tables(gateway)
        print_adt_header("DROPPING DIFF TABLES:")
        for table_name in dropped:
            print(f"  - {table_name}")
        print()
        return 0
    if args.install:
        config = ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
        result = workspace.create_install_script(config)
        print_adt_header("INSTALL SCRIPT:")
        print(result.path.read_text(encoding="utf-8"))
        print(f"Created install script: {_display(result.path)}")
        print()
        return 0
    if args.implode:
        config = ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
        for result in implode_pattern(root, args.implode, config):
            print_adt_header("IMPLODE FOLDER:", result.folder or args.implode)
            for file in result.files:
                print(f"  - {file}")
            print(f"Created implode script: {_display(result.target)}")
            print()
        return 0
    if args.refresh:
        plan = workspace.refresh_plan(ref=args.ref or args.patch_code)
        print_adt_header("REFRESHING OBJECTS:")
        if plan.folder:
            print(f"Patch folder: {_display(plan.folder)}")
        for file in plan.database_files:
            print(f"  - {_display(file)}")
        for app_id, components in plan.apex_components.items():
            print(f"APP {app_id}")
            for component in components:
                print(f"  - {component}")
        print()
        return 0
    if args.archive is not None:
        config = ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
        result = workspace.archive_patches(config, refs=args.archive)
        print_adt_header("ARCHIVING PATCHES:")
        print_adt_table(
            [
                {
                    "FOLDER": folder,
                    "ARCHIVE": _display(path.as_posix()),
                }
                for folder, path in zip(result.archived, result.archive_paths, strict=True)
            ]
        )
        if result.script_archives:
            print("Patch script archives:")
            for path in result.script_archives:
                print(f"  - {_display(path)}")
        print()
        return 0
    if args.ref and not args.create and not args.deploy:
        print_adt_header("PATCH FOLDERS:")
        print_adt_table(folder_preview_rows(workspace.discover(ref=args.ref)))
        return 0
    if args.patch_code and not args.create and not args.deploy:
        print(f"Next patch folder: {_display(workspace.next_folder(args.patch_code))}")
        print()
    if args.deploy and not args.create:
        if not args.target:
            print("Missing required target: use -target TARGET with -deploy", file=sys.stderr)
            print(file=sys.stderr)
            return 2
        config = ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
        selected_gateway_factory, dev_gateway_factory, connection_provider = (
            _patch_deploy_gateway_factories(args, root, config, gateway_factory)
        )
        deploy_gateway_factory = _patch_print_connection_block(
            workspace,
            config,
            ref               = args.ref or args.patch_code,
            target_env        = args.target,
            gateway_factory   = selected_gateway_factory,
            connection_provider= connection_provider,
            debug             = args.debug,
        )
        if args.debug:
            _print_startup_debug(_load_startup_context(args))
        result = workspace.deploy_patch(
            config,
            ref               = args.ref or args.patch_code,
            target_env        = args.target,
            gateway_factory   = deploy_gateway_factory,
            dev_gateway_factory= dev_gateway_factory,
            force             = args.force,
            continue_on_error = args.continue_patch,
        )
        print_adt_header("DEPLOYING PATCH:")
        print_adt_table(_deployment_rows(result.results))
        if result.view_mismatches:
            print("View column mismatches:")
            for mismatch in result.view_mismatches:
                print(
                    f"  - {mismatch['schema']}.{mismatch['view']}: "
                    f"{mismatch['actual']} != {mismatch['expected']}"
                )
        print()
        return 1 if result.status == "ERROR" else 0
    if args.create and not args.patch_code:
        print(
            "Missing required patch code: use -patch PATCH_CODE with -create",
            file=sys.stderr,
        )
        print(file=sys.stderr)
        return 2
    if args.create and args.deploy and not args.target:
        print("Missing required target: use -target TARGET with -deploy", file=sys.stderr)
        print(file=sys.stderr)
        return 2
    if args.hash is not False and not args.target:
        print("Missing required target: use -target TARGET with -hash", file=sys.stderr)
        print(file=sys.stderr)
        return 2
    if args.locked and args.hash is False:
        print("Missing required hash mode: use -hash with -locked", file=sys.stderr)
        print(file=sys.stderr)
        return 2
    records = PatchRunner().run(
        PatchRequest(
            root                 = root,
            commit_limit         = args.commits,
            patch_code           = args.patch_code,
            rebuild              = args.rebuild,
            search_terms         = args.search,
            authors              = _patch_authors(args, root),
            commit_refs          = args.commit,
            ignore_commits       = args.ignore,
            files_only           = args.files,
            include_full_exports = bool(args.full),
        )
    )
    if args.hash is not False:
        config = ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
        rollout = build_hash_rollout(
            records,
            folder        = hash_rollout_folder(root, config, args.target),
            target_commit = args.hash if not isinstance(args.hash, bool) else None,
            locked        = args.locked,
        )
        print_adt_header("LOADING HASH FILES:")
        for log_path in rollout.loaded:
            print(f"  - {_display(log_path)}")
        print()
        if args.create and not rollout.changed:
            raise PatchError("no hash-changed files to patch")
        if args.create and args.locked:
            missing = missing_rollout_commits(records, rollout)
            if missing:
                missing_list = ", ".join(str(number) for number in missing)
                raise PatchError(
                    f"locked hash commits missing from the commit window: {missing_list}, "
                    "increase -commits"
                )
        if args.create and not args.locked:
            write_hash_rollout(rollout)
            print_adt_header("GENERATING HASH FILE:", str(rollout.target_commit))
            print(f"  - {_display(rollout.path)}")
            print()
        records = restrict_records_to_rollout(records, rollout)
        if records and rollout.changed:
            print_adt_header("CHANGED FILES:", str(len(rollout.changed)))
            print_adt_table(_hash_changed_rows(rollout))
        elif rollout.changed:
            print_adt_header(f"HASH ON {len(rollout.changed)} FILES CHANGED:")
            for file in sorted(rollout.changed):
                print(f"  - {file}")
            print()
        if not records and not args.create:
            print(f"Commit cache: {_display(root / '.adt-ai' / 'patch_commits.yaml')}")
            print()
            return 0
    if args.create:
        config = ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
        result = workspace.create_database_patch(
            config,
            patch_code = args.patch_code,
            records    = records,
            full_app_ids = args.full,
            target_env = args.target,
        )
        print_adt_header("PATCH FILES:")
        print(f"Patch folder: {_display(result.folder)}")
        for schema, path in result.sql_files.items():
            print(f"  - {schema}: {_display(path)}")
        print()
        if not args.deploy:
            return 0
        selected_gateway_factory, dev_gateway_factory, connection_provider = (
            _patch_deploy_gateway_factories(args, root, config, gateway_factory)
        )
        deploy_gateway_factory = _patch_print_connection_block(
            workspace,
            config,
            ref                = result.folder.name,
            target_env         = args.target,
            gateway_factory    = selected_gateway_factory,
            connection_provider= connection_provider,
            debug              = args.debug,
        )
        if args.debug:
            _print_startup_debug(_load_startup_context(args))
        deploy_result = workspace.deploy_patch(
            config,
            ref                = result.folder.name,
            target_env         = args.target,
            gateway_factory    = deploy_gateway_factory,
            dev_gateway_factory= dev_gateway_factory,
            force              = args.force,
            continue_on_error  = args.continue_patch,
        )
        print_adt_header("DEPLOYING PATCH:")
        print_adt_table(_deployment_rows(deploy_result.results))
        print()
        return 1 if deploy_result.status == "ERROR" else 0
    print_adt_header("RECENT COMMITS:")
    print_adt_table(preview_rows(records))
    print(f"Commit cache: {_display(root / '.adt-ai' / 'patch_commits.yaml')}")
    print()
    return 0


def _hash_changed_rows(rollout: HashRollout) -> list[dict[str, object]]:
    return [
        {
            "FILE": file,
            "PREVIOUS": rollout.previous_commits.get(file, ""),
            "COMMIT": rollout.changed_commits.get(file, ""),
        }
        for file in sorted(rollout.changed)
    ]


def _patch_authors(args: argparse.Namespace, root: Path) -> list[str] | None:
    authors = list(args.by or [])
    if args.my:
        result = subprocess.run(
            ["git", "config", "user.name"],
            cwd            = root,
            capture_output = True,
            text           = True,
            check          = False,
        )
        current_user = result.stdout.strip()
        if current_user:
            authors.append(current_user)
    return authors or None


def _patch_deploy_gateway_factories(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, object],
    gateway_factory: GatewayFactory | None,
) -> tuple[GatewayFactory, GatewayFactory | None, Callable[[str], object | None]]:
    debug = getattr(args, "debug", False)
    if gateway_factory:
        def selected_gateway_factory(schema: str) -> QueryGateway:
            gateway = gateway_factory(schema)
            return DebugQueryGateway(gateway) if debug else gateway

        return selected_gateway_factory, None, lambda _schema: None

    repo_root = _repo_root()
    connection_search_paths = _connection_search_paths(config, args.config_dir, root, repo_root)
    connection_files = _connection_file_candidates(config, args.config_dir, root, repo_root)
    connections = ConnectionLoader(
        connection_search_paths,
        wallet_roots = _wallet_roots(config, root, repo_root, connection_search_paths),
    ).load(candidates=connection_files)
    target_env = args.target or connections.default_environment
    source_env = str(config.get("patch_source_env") or config.get("source_env") or "DEV")
    startup_sql = _load_startup_sql(args.config_dir, root, repo_root)

    def target_connection(schema: str) -> object:
        return connections.resolve(environment=target_env, schema=schema)

    def target_gateway_factory(schema: str) -> QueryGateway:
        gateway = OracleGateway(target_connection(schema), project_root=root, startup_sql=startup_sql)
        return DebugQueryGateway(gateway) if debug else gateway

    if source_env.upper() == target_env.upper():
        return target_gateway_factory, None, target_connection

    def source_gateway_factory(schema: str) -> QueryGateway:
        gateway = OracleGateway(
            connections.resolve(environment=source_env, schema=schema),
            project_root=root,
            startup_sql=startup_sql,
        )
        return DebugQueryGateway(gateway) if debug else gateway

    return target_gateway_factory, source_gateway_factory, target_connection


def _patch_print_connection_block(
    workspace: PatchWorkspace,
    config: dict[str, object],
    *,
    ref: str | None,
    target_env: str,
    gateway_factory: GatewayFactory,
    connection_provider: Callable[[str], object | None],
    debug: bool,
) -> GatewayFactory:
    """Print the standard CONNECTING TO block per deploy-plan schema.

    Returns a caching wrapper around ``gateway_factory`` so the version probe and
    the subsequent ``deploy_patch`` reuse the same gateway per schema (one connection).
    """
    cache: dict[str, QueryGateway] = {}

    def cached_factory(schema: str) -> QueryGateway:
        if schema not in cache:
            cache[schema] = gateway_factory(schema)
        return cache[schema]

    try:
        _folder, plan = workspace.deployment_plan(config, ref=ref)
    except PatchError:
        # Let deploy_patch surface the same error; just skip the connection block.
        return cached_factory

    for schema in sorted({item.schema for item in plan}):
        _print_connection_block(
            cached_factory(schema),
            connection_provider(schema),
            schema       = schema,
            environment  = target_env,
            debug        = debug,
        )
    return cached_factory


def _patch_delete_diff_gateway(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, object],
    gateway_factory: GatewayFactory | None,
) -> tuple[QueryGateway, str, str, object | None]:
    debug = getattr(args, "debug", False)
    if gateway_factory:
        gateway = gateway_factory("APP")
        environment = args.target or getattr(args, "env", None) or "DEV"
        return (
            DebugQueryGateway(gateway) if debug else gateway,
            "APP",
            environment,
            None,
        )

    repo_root = _repo_root()
    connection_search_paths = _connection_search_paths(config, args.config_dir, root, repo_root)
    connection_files = _connection_file_candidates(config, args.config_dir, root, repo_root)
    connections = ConnectionLoader(
        connection_search_paths,
        wallet_roots = _wallet_roots(config, root, repo_root, connection_search_paths),
    ).load(candidates=connection_files)
    environment = args.target or getattr(args, "env", None) or connections.default_environment
    schemas = connections.default_schemas(environment)
    schema = schemas[0] if schemas else "APP"
    connection = connections.resolve(environment=environment, schema=schema)
    gateway = OracleGateway(
        connection,
        startup_sql=_load_startup_sql(args.config_dir, root, repo_root),
    )
    return (
        DebugQueryGateway(gateway) if debug else gateway,
        schema,
        environment,
        connection,
    )


def _deployment_rows(results: Sequence[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for result in results:
        rows.append(
            {
                "#": result.order,
                "FILE": result.file,
                "SCHEMA": result.schema,
                "FILES": result.files,
                "COMMITS": result.commits,
                "STATUS": result.status,
            }
        )
    return rows


_NO_DEPENDENCY_INDEX_MESSAGE = (
    "No dependency index found. Run 'adt dependencies --refresh' to build it."
)


def _run_dependencies(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    # Human (table) output carries the generic banner/footer on stdout like every
    # other command; machine output (-format yaml/md) keeps stdout pure data and
    # sends the chrome to stderr so it stays pipeable.
    chrome = sys.stdout if args.format == "table" else sys.stderr
    print_adt_header("APEX DEPLOYMENT TOOL: DEPENDENCIES", file=chrome)

    root             = Path(args.root).expanduser().resolve()
    dependencies_dir = root / "dependencies"

    if args.refresh:
        return _refresh_dependency_index(args, root, gateway_factory)

    if not (dependencies_dir / "index.yaml").exists():
        print(_NO_DEPENDENCY_INDEX_MESSAGE, file=sys.stderr)
        return 1

    if args.rebuild_db:
        db_path = dependencies_dir / "dependencies.db"
        DependencyStore.rebuild_from_yaml(dependencies_dir, db_path).close()
        print_adt_header("REBUILD DATABASE:", file=chrome)
        print(f"  {_display(db_path)}", file=chrome)
        return 0

    with DependencyStore.rebuild_from_yaml(dependencies_dir) as store:
        if args.uses:
            exit_code = _print_dependency_list(args.uses, store.uses(args.uses), "uses", args.format)
        elif args.used_by:
            exit_code = _print_dependency_list(
                args.used_by, store.used_by(args.used_by), "used_by", args.format
            )
        elif args.impact:
            exit_code = _print_dependency_impact(
                args.impact,
                store.impact(args.impact),
                args.format,
                store.affected_columns(args.impact),
            )
        elif args.unused:
            exit_code = _print_unused(store.unused(type=args.type), args.format)
        else:
            _print_dependencies_hint()
            exit_code = 0

    return exit_code


def _print_dependency_list(
    query: str,
    items: list[str],
    relation: str,
    output_format: str,
) -> int:
    if output_format == "yaml":
        print(yaml.safe_dump({"object": query, relation: items}, sort_keys=False).rstrip())
        return 0
    if output_format == "md":
        heading = "Uses" if relation == "uses" else "Used by"
        lines = [f"## {heading}: {query} ({len(items)})", ""]
        lines.extend(f"- {item}" for item in items)
        print("\n".join(lines))
        return 0
    heading = "USES" if relation == "uses" else "USED BY"
    print_adt_header(f"{heading}: {query} ({len(items)})")
    if items:
        print_adt_table([{"OBJECT": item} for item in items])
    else:
        print("  (none)")
    return 0


def _print_dependency_impact(
    query: str,
    items: list[tuple[str, int]],
    output_format: str,
    columns: list[dict[str, str]] | None = None,
) -> int:
    # Column lineage exists only when the index was refreshed with PL/Scope
    # data; every format omits the section entirely when there is none.
    columns = columns or []
    if output_format == "yaml":
        payload = {
            "object": query,
            "impact": [{"object": node, "depth": depth} for node, depth in items],
        }
        if columns:
            payload["columns"] = [
                {
                    "view": row["view_name"],
                    "column": row["column_name"],
                    "source": f"{row['src_table']}.{row['src_column']}",
                }
                for row in columns
            ]
        print(yaml.safe_dump(payload, sort_keys=False).rstrip())
        return 0
    if output_format == "md":
        lines = [f"## Impact: {query} ({len(items)})", ""]
        lines.extend(f"- {node} (depth {depth})" for node, depth in items)
        if columns:
            lines.extend(["", f"## Affected columns ({len(columns)})", ""])
            lines.extend(
                f"- {row['view_name']}.{row['column_name']} "
                f"(from {row['src_table']}.{row['src_column']})"
                for row in columns
            )
        print("\n".join(lines))
        return 0
    print_adt_header(f"IMPACT: {query} ({len(items)})")
    if items:
        print_adt_table([{"OBJECT": node, "DEPTH": depth} for node, depth in items])
    else:
        print("  (none)")
    if columns:
        print_adt_header(f"AFFECTED COLUMNS ({len(columns)})")
        print_adt_table(
            [
                {
                    "VIEW": row["view_name"],
                    "COLUMN": row["column_name"],
                    "SOURCE": f"{row['src_table']}.{row['src_column']}",
                }
                for row in columns
            ]
        )
    return 0


def _print_unused(
    items: list[str],
    output_format: str,
) -> int:
    if output_format == "yaml":
        print(yaml.safe_dump({"unused": items}, sort_keys=False).rstrip())
        return 0
    if output_format == "md":
        lines = [f"## Unused ({len(items)})", ""]
        lines.extend(f"- {item}" for item in items)
        print("\n".join(lines))
        return 0
    print_adt_header(f"UNUSED ({len(items)})")
    if items:
        print_adt_table([{"OBJECT": item} for item in items])
    else:
        print("  (none)")
    return 0


def _print_dependencies_hint() -> None:
    print("Specify a query: -uses OBJ, -used-by OBJ, -impact OBJ, or -unused.")
    print("Use -refresh to rebuild from the database, -rebuild-db to rebuild the SQLite cache.")


def _refresh_dependency_index(
    args: argparse.Namespace,
    root: Path,
    gateway_factory: GatewayFactory | None,
) -> int:
    repo_root = _repo_root()
    config_search_paths = _config_search_paths(args.config_dir, root, repo_root)
    config = ConfigLoader(config_search_paths).load().data
    connection_search_paths = _connection_search_paths(config, args.config_dir, root, repo_root)
    connection_files = _connection_file_candidates(config, args.config_dir, root, repo_root)
    connections = ConnectionLoader(
        connection_search_paths,
        wallet_roots = _wallet_roots(config, root, repo_root, connection_search_paths),
    ).load(candidates=connection_files)
    environment = args.env or connections.default_environment
    schemas = (
        connections.expand_schemas(args.schema, environment=environment)
        if args.schema
        else connections.default_schemas(environment)
    )
    schema_connections = {
        schema: connections.resolve(environment=environment, schema=schema)
        for schema in schemas
    }
    schema_export = {
        schema: schema_connections[schema].export
        for schema in schemas
    }
    config = _with_schema_folders(config, schema_export)

    debug = getattr(args, "debug", False)
    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(schema_connections[schema])

    base_gateway_factory = gateway_factory or default_gateway_factory

    def selected_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = base_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if debug else gateway
        return gateway_cache[schema]

    for schema in schemas:
        _print_connection_block(
            selected_gateway_factory(schema), schema_connections[schema], debug=debug
        )

    if getattr(args, "with_plscope", False):
        # Session setting only affects objects compiled afterwards — recompiling
        # is a deliberate, separate step the user runs; never trigger it here.
        for schema in schemas:
            selected_gateway_factory(schema).execute(PLSCOPE_SESSION_STATEMENT)
        print_adt_header("PL/SCOPE SESSION ENABLED")
        print("  PL/Scope data appears only for objects compiled with the setting on.")
        print("  To gather column-level data, recompile first (review before running):")
        print("    adt recompile -force -scope ALL")

    print_adt_header(f"BUILDING DEPENDENCY INDEX: {', '.join(schemas)}")
    DependencyIndexRunner(selected_gateway_factory).refresh(
        DependencyIndexRequest(
            root          = root,
            schemas       = schemas,
            config        = config,
            schema_export = schema_export,
        )
    )
    print(f"\nDependency index: {_display(root / 'dependencies')}")
    print()
    return 0


def _run_export_db(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_DB")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    schemas = (
        connections.expand_schemas(args.schema, environment=environment)
        if args.schema
        else connections.default_schemas(environment)
    )
    schema_connections = {
        schema: connections.resolve(environment=environment, schema=schema)
        for schema in schemas
    }
    schema_export = {
        schema: schema_connections[schema].export
        for schema in schemas
    }
    config = _with_schema_folders(config, schema_export)
    object_types = _flatten_arg_groups(args.type)
    object_names = _flatten_arg_groups(args.name)
    if args.debug:
        _print_startup_debug(startup)
    if _has_job_recent_conflict(args.recent, object_types or []):
        print(
            "export_db: JOB objects cannot be exported with -recent; "
            "export jobs separately with -type JOB.",
            file=sys.stderr,
        )
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
        _print_connection_block(
            cached_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )

    runner = ExportDbRunner(cached_gateway_factory)
    runner.run(
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


def _run_export_data(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_DATA")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    schemas = (
        connections.expand_schemas(args.schema, environment=environment)
        if args.schema
        else connections.default_schemas(environment)
    )
    schema_connections = {
        schema: connections.resolve(environment=environment, schema=schema)
        for schema in schemas
    }
    schema_export = {
        schema: schema_connections[schema].export
        for schema in schemas
    }
    if args.debug:
        _print_startup_debug(startup)

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(schema_connections[schema], startup_sql=startup.startup_sql)

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def export_data_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = selected_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[schema]

    for schema in schemas:
        _print_connection_block(
            export_data_gateway_factory(schema), schema_connections[schema], debug=args.debug
        )

    runner = ExportDataRunner(export_data_gateway_factory)
    runner.run(
        ExportDataRequest(
            root          = root,
            schemas       = schemas,
            config        = config,
            schema_export = schema_export,
            names         = _flatten_arg_groups(args.name),
            reporter      = ConsoleExportDataReporter(),
        )
    )
    return 0


def _run_export_apex(args: argparse.Namespace, gateway_factory: GatewayFactory | None = None) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: EXPORT_APEX")
    startup = _load_startup_context(args)
    root = startup.root
    config = startup.config
    connections = startup.connections
    environment = args.env or connections.default_environment
    if args.schema:
        schemas = connections.expand_schemas(args.schema, environment=environment)
        connection_schema = schemas[0]
    elif args.reveal:
        schemas = connections.schema_names(environment)
        connection_schema = _apex_reveal_connection_schema(connections, environment, schemas)
    else:
        schemas = connections.default_schemas(environment, kind="apex")
        connection_schema = schemas[0]
    schema_connections = {
        schema: connections.resolve(environment=environment, schema=schema, kind="apex")
        for schema in schemas
    }
    schema_scope = {
        schema: _apex_scope(
            schema_connections[schema].apex,
            workspace = args.ws,
            group     = args.group,
            app_ids   = _flatten_arg_groups(args.app),
        )
        for schema in schemas
    }
    actions = _apex_actions(args, config)
    recent_days = _apex_recent_days(args.recent, config)
    if args.debug:
        _print_startup_debug(startup)
    if not args.reveal and not any(actions.values()):
        _print_missing_apex_format_guidance()
        return 2

    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(
            schema_connections[connection_schema if args.reveal else schema],
            project_root=root,
            startup_sql=startup.startup_sql,
        )

    selected_gateway_factory = gateway_factory or default_gateway_factory

    def export_apex_gateway_factory(schema: str) -> QueryGateway:
        gateway_schema = connection_schema if args.reveal else schema
        if gateway_schema not in gateway_cache:
            gateway = selected_gateway_factory(gateway_schema)
            gateway_cache[gateway_schema] = DebugQueryGateway(gateway) if args.debug else gateway
        return gateway_cache[gateway_schema]

    reporter = ConsoleApexRevealReporter()
    applications_by_schema: dict[str, list[ApexApplication]] = {}

    # Reveal/listing reads apex_applications, which is scoped by the parsing
    # schema's workspace association (and the owner/workspace filters in SQL),
    # not by the APEX security context. The context is set per-app at export
    # time (EXPORT_START_QUERY), so listing needs no workspace switch here.
    if args.reveal:
        _print_connection_block(
            export_apex_gateway_factory(connection_schema),
            schema_connections[connection_schema],
            debug=args.debug,
        )
        discovery = ApexDiscovery(export_apex_gateway_factory(connection_schema))
        workspace = schema_scope[connection_schema]["workspace"]
        reporter.workspaces(
            discovery.workspaces(workspace=workspace, max_app_id=args.max_app_id)
        )
        owner_filter = None if args.owners else schemas
        reporter.owner_counts(
            discovery.owner_app_counts(owner_filter, max_app_id=args.max_app_id)
        )
    for schema in schemas:
        if not args.reveal:
            _print_connection_block(
                export_apex_gateway_factory(schema), schema_connections[schema], debug=args.debug
            )
        discovery = ApexDiscovery(export_apex_gateway_factory(schema))
        scope = schema_scope[schema]
        applications = discovery.applications(
            owner     = schema,
            workspace = scope["workspace"],
            group     = scope["group"],
            app_ids   = scope["app_ids"],
            recent_days = recent_days if args.reveal else None,
            max_app_id = args.max_app_id,
        )
        applications_by_schema[schema] = applications
        reporter.applications(schema, applications)
    if not args.reveal:
        requested_app_ids = _flatten_arg_groups(args.app)
        if requested_app_ids:
            found_ids = {
                str(application.app_id)
                for apps in applications_by_schema.values()
                for application in apps
            }
            missing_app_ids = [
                app_id for app_id in requested_app_ids if str(app_id) not in found_ids
            ]
            if missing_app_ids:
                owner_discovery = ApexDiscovery(export_apex_gateway_factory(connection_schema))
                owner_to_app_ids, not_configured, not_found = _resolve_apex_app_owners(
                    owner_discovery,
                    missing_app_ids,
                    connections.schema_names(environment),
                )
                for owner_schema, owner_app_ids in owner_to_app_ids.items():
                    connection = schema_connections.get(owner_schema)
                    if connection is None:
                        connection = connections.resolve(
                            environment=environment, schema=owner_schema, kind="apex"
                        )
                        schema_connections[owner_schema] = connection
                        _print_connection_block(
                            export_apex_gateway_factory(owner_schema),
                            connection,
                            debug=args.debug,
                        )
                        schemas.append(owner_schema)
                        applications_by_schema.setdefault(owner_schema, [])
                    scope = _apex_scope(
                        connection.apex,
                        workspace = args.ws,
                        group     = args.group,
                        app_ids   = owner_app_ids,
                    )
                    schema_scope[owner_schema] = scope
                    discovery = ApexDiscovery(export_apex_gateway_factory(owner_schema))
                    found = discovery.applications(
                        owner      = owner_schema,
                        workspace  = scope["workspace"],
                        group      = scope["group"],
                        app_ids    = scope["app_ids"],
                        max_app_id = args.max_app_id,
                    )
                    existing = {app.app_id for app in applications_by_schema[owner_schema]}
                    new_apps = [app for app in found if app.app_id not in existing]
                    applications_by_schema[owner_schema].extend(new_apps)
                    if new_apps:
                        reporter.applications(owner_schema, new_apps)
                for app_id, owner in not_configured:
                    _print_apex_owner_not_configured(app_id, owner, environment)
                for app_id in not_found:
                    _print_apex_app_not_found(app_id)
    if not args.reveal and any(actions.values()):
        ApexExportRunner(export_apex_gateway_factory).run(
            ApexExportRequest(
                root         = root,
                schemas      = schemas,
                applications = applications_by_schema,
                actions      = actions,
                config       = config,
                release      = args.release,
                recent_days  = recent_days,
                changed_by   = args.by or None,
            )
        )
    return 0


def _apex_reveal_connection_schema(
    connections: ConnectionResult,
    environment: str,
    schemas: list[str],
) -> str:
    try:
        default_schemas = connections.default_schemas(environment, kind="apex")
    except ConnectionConfigError:
        default_schemas = []
    return default_schemas[0] if default_schemas else schemas[0]


def _apex_app_id_value(app_id: str | int) -> str | int:
    try:
        return int(app_id)
    except (TypeError, ValueError):
        return app_id


def _resolve_apex_app_owners(
    discovery: ApexDiscovery,
    missing_app_ids: list[str],
    schema_names: list[str],
) -> tuple[dict[str, list[str]], list[tuple[str, str]], list[str]]:
    """Look up the owner schema for each app missing from the scanned schemas.

    Returns the apps grouped by configured owner schema, the apps whose owner is
    not a configured schema (with that owner), and the apps not found anywhere.
    """
    schema_lookup = {name.upper(): name for name in schema_names}
    owner_to_app_ids: dict[str, list[str]] = {}
    not_configured: list[tuple[str, str]] = []
    not_found: list[str] = []
    for app_id in missing_app_ids:
        owner = discovery.application_owner(_apex_app_id_value(app_id))
        if not owner:
            not_found.append(app_id)
            continue
        owner_schema = schema_lookup.get(owner.upper())
        if owner_schema is None:
            not_configured.append((app_id, owner))
            continue
        owner_to_app_ids.setdefault(owner_schema, []).append(app_id)
    return owner_to_app_ids, not_configured, not_found


def _print_apex_owner_not_configured(app_id: str, owner: str, environment: str) -> None:
    print()
    print(
        f"APP {app_id} is owned by schema {owner}, which is not configured "
        f"for environment {environment}."
    )
    print(f"Add {owner} to your connections to export it; skipping APP {app_id}.")


def _print_apex_app_not_found(app_id: str) -> None:
    print()
    print(f"APP {app_id} was not found in any configured APEX schema.")


def _apex_recent_days(argument: int | None, _config: Mapping[str, object]) -> int | None:
    return argument


def _print_missing_apex_format_guidance() -> None:
    formats = ", ".join(["-all", *(f"-{action}" for action in APEX_EXPORT_ACTIONS)])
    print("Use -reveal to list workspaces and applications without exporting.")
    print()
    print("To export app(s) pass application number(s) and format.")
    print(f"Available formats: {formats}")
    print("Example: adtai export_apex -app 1000 -readable")
    print()


class ConsoleApexRevealReporter:
    def workspaces(self, workspaces: list[ApexWorkspace]) -> None:
        print_adt_header("WORKSPACES:")
        print_adt_table(
            [
                {
                    "workspace": workspace.workspace,
                    "workspace_id": workspace.workspace_id,
                    "owners": workspace.owners,
                    "applications": workspace.applications,
                    "developers": workspace.developers,
                }
                for workspace in workspaces
            ]
        )

    def owner_counts(self, owner_counts: list[ApexOwnerCount]) -> None:
        if not owner_counts:
            return
        print_adt_header("APPLICATIONS PER LISTED OWNERS:")
        print_adt_table(
            [
                {
                    "owner": owner_count.owner,
                    "applications": owner_count.applications,
                }
                for owner_count in owner_counts
            ]
        )

    def applications(self, schema: str, applications: list[ApexApplication]) -> None:
        if not applications:
            return
        print_adt_header("APEX APPLICATIONS:", schema)
        print_adt_table(
            [
                {
                    "app_id": application.app_id,
                    "name": _truncate_console_value(application.app_name, 40),
                    "pages": application.pages,
                    "updated_at": application.updated_at,
                }
                for application in applications
            ],
            min_widths={"name": 40},
        )


def _truncate_console_value(value: object, width: int) -> str:
    text = str(value or "")
    if len(text) <= width:
        return text
    return f"{text[:width - 2]}.."


class ConsoleExportDataReporter:
    line_width = 78

    def start_export(self, total: int) -> None:
        print_adt_header("EXPORT TABLE DATA:", f"({total})")

    def export_table(self, table: DataTable) -> None:
        print(f"  - {table.name.upper()}", end="", flush=True)

    def finish_table(self, table: DataTable, row_count: int) -> None:
        left = f"  - {table.name.upper()}"
        right = str(row_count)
        dot_count = max(1, self.line_width - len(left) - len(right) - 2)
        print(f" {'.' * dot_count} {right}")

    def finish_export(self) -> None:
        return None


def _is_user_database_error(error: Exception) -> bool:
    text = str(error)
    if any(marker in text for marker in ("DPY-", "DPI-", "ORA-", "TNS-")):
        return True
    module = type(error).__module__
    return module.startswith("oracledb")


def _is_database_connection_error(error: Exception) -> bool:
    text = str(error)
    connection_markers = (
        "DPY-",
        "DPI-",
        "TNS-",
        "ORA-01017",
        "ORA-12154",
        "ORA-12514",
        "ORA-12541",
        "ORA-12545",
        "ORA-12560",
        "Connect failed",
        "connection",
        "listener",
        "tnsnames.ora",
        "wallet",
    )
    return any(marker.lower() in text.lower() for marker in connection_markers)


def _display(value: object) -> str:
    return DROPBOX_PATH_RE.sub("Dropbox/", str(value))


def _print_database_error(error: Exception) -> None:
    print(file=sys.stderr)
    print("DATABASE CONNECTION FAILED", file=sys.stderr)
    print("--------------------------", file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Check the connection file and wallet under ADT.ai connections/wallets, then rerun.",
        file=sys.stderr,
    )
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)


def _print_config_error(error: Exception) -> None:
    print(file=sys.stderr)
    print("CONFIGURATION NOT FOUND", file=sys.stderr)
    print("-----------------------", file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Run ADT.ai from a project folder that has a connection file, or pass "
        "-config-dir / -root to point at one. See USAGE.md and `adtai doctor -init`.",
        file=sys.stderr,
    )
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_startup_context(args: argparse.Namespace) -> StartupContext:
    root = Path(args.root).expanduser().resolve()
    repo_root = _repo_root()
    config_search_paths = _config_search_paths(args.config_dir, root, repo_root)
    config_result = ConfigLoader(config_search_paths).load()
    _remember_completion_config(args, config_result.data)
    connection_search_paths = _connection_search_paths(
        config_result.data, args.config_dir, root, repo_root
    )
    connection_file_candidates = _connection_file_candidates(
        config_result.data, args.config_dir, root, repo_root
    )
    connections = ConnectionLoader(
        connection_search_paths,
        wallet_roots = _wallet_roots(config_result.data, root, repo_root, connection_search_paths),
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


def _remember_completion_config(args: argparse.Namespace, config: dict[str, object]) -> None:
    setattr(args, "_adt_completion_config", config)


def _completion_config(args: argparse.Namespace) -> dict[str, object] | None:
    config = getattr(args, "_adt_completion_config", None)
    if isinstance(config, dict):
        return config
    root_value = getattr(args, "root", None)
    if not root_value:
        return None
    try:
        root = Path(root_value).expanduser().resolve()
        config = ConfigLoader(
            _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
        ).load().data
    except ConfigError:
        return None
    _remember_completion_config(args, config)
    return config


def _notify_completion(args: argparse.Namespace, exit_code: int) -> None:
    config = _completion_config(args)
    theme = _configured_chime_theme(config)
    if not theme:
        return
    try:
        chime = importlib.import_module("chime")
    except ImportError:
        return
    chime.theme(theme)
    if exit_code == 0:
        chime.success()
    else:
        chime.error()


def _configured_chime_theme(config: Mapping[str, object] | None) -> str | None:
    if not config:
        return None
    value = config.get("chime_theme")
    if not value:
        return None
    return str(value)


def _config_search_paths(
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> list[Path]:
    project_paths = [Path(path).expanduser() for path in config_dirs or []]
    if not project_paths:
        project_paths = [root / "config", root]
    return [repo_root / "config", *project_paths]


def _load_startup_sql(
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> str | None:
    """Return ``STARTUP.sql`` content for the nearest project location.

    Resolution is nearest-wins so a project ``config/STARTUP.sql`` overrides the
    shipped repo default; the repo copy is the last fallback. The first existing
    file with non-whitespace content wins — there is no concatenation, since
    session settings must run exactly once per connection. An empty (comment-only)
    file resolves to ``None`` so the shipped template never contributes content.
    """
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


def _connection_search_paths(
    config: dict[str, object],
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> list[Path]:
    configured_paths = _path_list(
        _config_value(config, ("connections", "path")),
        _config_value(config, ("connections_path",)),
        _config_value(config, ("connections_dir",)),
    )
    return [
        repo_root / "connections",
        repo_root / "config",
        *configured_paths,
        *[Path(path).expanduser() for path in config_dirs or []],
        root / "connections",
        root / "config",
        root,
    ]


def _wallet_roots(
    config: dict[str, object],
    root: Path,
    repo_root: Path,
    connection_search_paths: list[Path],
) -> list[Path]:
    configured_paths = _path_list(
        _config_value(config, ("connections", "wallet_path")),
        _config_value(config, ("wallet_path",)),
    )
    return [
        repo_root / "connections" / "wallets",
        *configured_paths,
        *[path / "wallets" for path in connection_search_paths],
        root / "connections" / "wallets",
    ]


def _connection_file_candidates(
    config: dict[str, object],
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> list[Path]:
    """Ordered connection-file candidates; the first existing one wins.

    Default scheme (folder basename = ``root.name``):
      1. ``<project>/connections.yaml``                 generic name, project root only
      2. ``<project>/connections/<FOLDER>.yaml``        folder-named, project connections/ dir
      3. ``<repo>/connections/<FOLDER>.yaml``           folder-named, repo connections/ dir

    Overrides (searched first, highest priority): ``connections.path`` /
    ``-config-dir`` directories, and ``connections.file`` replaces the
    derived filenames everywhere (secrets-outside-repo support).
    """
    configured_file = (
        _config_value(config, ("connections", "file"))
        or _config_value(config, ("connections_file",))
        or _config_value(config, ("connection_file",))
    )
    filename = str(configured_file) if configured_file else None

    override_dirs = [
        *_path_list(
            _config_value(config, ("connections", "path")),
            _config_value(config, ("connections_path",)),
            _config_value(config, ("connections_dir",)),
        ),
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
        candidates.append(root / filename)
        candidates.append(root / "connections" / filename)
        candidates.append(repo_root / "connections" / filename)
    else:
        candidates.append(root / "connections.yaml")
        candidates.append(root / "connections" / f"{root.name}.yaml")
        candidates.append(repo_root / "connections" / f"{root.name}.yaml")

    return _dedup_paths(candidates)


def _dedup_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _with_schema_folders(
    config: dict[str, object],
    schema_export: dict[str, dict[str, object]],
) -> dict[str, object]:
    schema_folders = {
        schema: subfolder
        for schema, export in schema_export.items()
        if (subfolder := export.get("subfolder"))
    }
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
    return [
        part.strip()
        for group in groups
        for item in group
        for part in item.split(",")
        if part.strip()
    ]


def _apex_scope(
    apex_config: Mapping[str, object],
    workspace: str | None = None,
    group: str | None = None,
    app_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "workspace": workspace or _string_or_none(apex_config.get("workspace")),
        "group": group or _string_or_none(apex_config.get("group")),
        "app_ids": app_ids or _split_config_values(apex_config.get("app")),
    }


def _apex_actions(
    args: argparse.Namespace,
    _config: Mapping[str, object] | None = None,
) -> dict[str, bool]:
    actions = dict.fromkeys(APEX_EXPORT_ACTIONS, False)
    if getattr(args, "all_formats", False):
        return {action: True for action in APEX_EXPORT_ACTIONS}
    for action in APEX_EXPORT_ACTIONS:
        if getattr(args, action, False):
            actions[action] = True
    return actions


def _string_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _split_config_values(value: object) -> list[str] | None:
    if value is None:
        return None
    values = value if isinstance(value, list | tuple) else [value]
    normalized = [
        part.strip()
        for item in values
        for part in str(item).replace(" ", ",").split(",")
        if part.strip()
    ]
    return normalized or None


def _is_enabled(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


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
    if isinstance(value, list):
        rendered = " | ".join(_display(item) for item in value)
    else:
        rendered = _display(value)
    if not rendered:
        return
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


def _print_connection_block(
    gateway: QueryGateway,
    connection: object | None,
    *,
    schema: str | None = None,
    environment: str | None = None,
    debug: bool = False,
) -> None:
    resolved_schema = schema or str(getattr(connection, "schema", "APP"))
    resolved_environment = environment or str(getattr(connection, "environment", "DEV"))
    print_adt_header(f"CONNECTING TO SCHEMA {resolved_schema}, {resolved_environment}:")
    _print_connection_versions(gateway, connection, debug=debug)


def _print_connection_versions(
    gateway: QueryGateway,
    connection: object | None,
    *,
    debug: bool = False,
) -> None:
    # in -debug mode surface probe failures instead of silently swallowing them
    ignore_errors = not debug
    versions: dict[str, str] = {}
    apex_version = _fetch_version(gateway, APEX_VERSION_QUERY, ignore_errors=ignore_errors)
    if apex_version:
        versions["APEX"] = apex_version

    database_version = _fetch_version(gateway, DATABASE_VERSION_QUERY, ignore_errors=ignore_errors)
    if not database_version:
        database_version = _fetch_version(
            gateway, DATABASE_VERSION_OLD_QUERY, ignore_errors=ignore_errors
        )
    if database_version:
        versions["DATABASE"] = database_version

    thick = getattr(connection, "thick", False)
    if thick:
        versions["THICK"] = _oracle_client_version() or "Y"

    if not versions:
        return
    for key in sorted(versions):
        print(f"{key:>18} | {versions[key]}")
    print()


def _oracle_client_version() -> str:
    try:
        version = importlib.import_module("oracledb").clientversion()
    except Exception:
        return ""
    if not version:
        return ""
    if isinstance(version, tuple) and len(version) >= 2:
        return f"{version[0]}.{version[1]}"
    return str(version)


def _fetch_version(
    gateway: QueryGateway,
    sql: str,
    ignore_errors: bool = False,
) -> str | None:
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


def _print_completion_timer(
    started_at: float,
    stdout: TextIO | None = None,
) -> None:
    # Footer spacing is enforced here, in the shared layer, for every command:
    # exactly two empty lines before TIMER (...\n\n\nTIMER). Whatever trailing
    # newlines the previous section left are normalized away, so no module needs
    # to tune its own blank-line count. Keep this knob-free — a per-call override
    # is what let export_apex and update drift apart in the first place.
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
