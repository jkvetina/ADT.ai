from __future__ import annotations

import argparse
from collections.abc import Sequence

from adt_ai.cli_constants import (
    DEFAULT_ROW_LIMIT,
    PUBLIC_MODULES,
    REMOVED_COMPATIBILITY_FLAGS,
    REVEAL_DEFAULT_LIMIT,
    AdtArgumentParser,
)
from adt_ai.cli_help import generated_command_usage as _generated_command_usage


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
    export_db.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    export_db.add_argument("--env", "-env", help="connection environment")
    export_db.add_argument("--schema", "-schema", action="append", help="schema to export")
    export_db.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern(s) to export, supports %% wildcards",
    )
    export_db.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern(s) to export, supports %% wildcards",
    )
    export_db.add_argument(
        "--recent",
        "-recent",
        type = int,
        help = "export objects changed in the last DAYS days",
    )
    export_db.add_argument(
        "--dry-run",
        "-dry-run",
        action="store_true",
        help="plan writes without changing files",
    )
    export_db.add_argument(
        "--delete",
        "-delete",
        action = "store_true",
        help   = "delete existing object files before export, excluding DATA",
    )
    export_db.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress per-object progress; keep overview, chrome, and timer",
    )
    export_db.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )

    export_data = subparsers.add_parser(
        "export_data",
        description="export table data",
        help="export table data",
    )
    export_data.add_argument("--root", "-root", default=".", help="output root folder")
    export_data.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    export_data.add_argument("--env", "-env", help="connection environment")
    export_data.add_argument("--schema", "-schema", action="append", help="schema to export")
    export_data.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "table name pattern(s) to export, supports %% wildcards",
    )
    export_data.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress per-table progress; keep chrome, summary, and timer",
    )
    export_data.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )

    export_apex = subparsers.add_parser(
        "export_apex",
        description="export APEX applications",
        help="export APEX applications",
    )
    export_apex.add_argument("--root", "-root", default=".", help="output root folder")
    export_apex.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    export_apex.add_argument("--env", "-env", help="connection environment")
    export_apex.add_argument("--schema", "-schema", action="append", help="APEX owner schema")
    export_apex.add_argument("--ws", "-ws", help="APEX workspace")
    export_apex.add_argument("--group", "-group", help="APEX application group")
    export_apex.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "application id(s), or ranges MIN-MAX / MIN+, to export or reveal",
    )
    export_apex.add_argument(
        "--max-app-id",
        "--max_app_id",
        "-max_app_id",
        dest = "max_app_id",
        type = int,
        help = "only list apps with application_id below ID (hides temp/backup apps)",
    )
    export_apex.add_argument(
        "--recent",
        "-recent",
        nargs = "?",
        const = 1,
        type  = int,
        help  = "show components changed in the last DAYS days",
    )
    export_apex.add_argument(
        "--by",
        "-by",
        nargs = "?",
        const = "",
        help  = "show components changed by developer",
    )
    export_apex.add_argument("--release", "-release", help="override APEX release in SQL exports")
    export_apex.add_argument(
        "--reveal",
        "-reveal",
        action = "store_true",
        help   = "show matching APEX workspaces and applications",
    )
    export_apex.add_argument(
        "--owners",
        "-owners",
        action = "store_true",
        help   = "in reveal mode, list app counts for all owners, not just configured schemas",
    )
    export_apex.add_argument(
        "--all",
        "-all",
        action = "store_true",
        dest   = "all_formats",
        help   = "export all APEX formats",
    )
    export_apex.add_argument(
        "--full", "-full", action="store_true", help="export full application SQL"
    )
    export_apex.add_argument(
        "--split", "-split", action="store_true", help="export split application source"
    )
    export_apex.add_argument(
        "--readable", "-readable", action="store_true", help="export readable YAML source"
    )
    export_apex.add_argument(
        "--embedded", "-embedded", action="store_true", help="export embedded code report"
    )
    export_apex.add_argument("--rest", "-rest", action="store_true", help="export REST services")
    export_apex.add_argument(
        "--files", "-files", action="store_true", help="export application files"
    )
    export_apex.add_argument(
        "--files-ws",
        "--files_ws",
        "-files_ws",
        action="store_true",
        dest="files_ws",
        help="export workspace files",
    )
    export_apex.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    search_repo = subparsers.add_parser(
        "search_repo",
        description="search cached Git commit history",
        help="search cached Git commit history",
    )
    search_repo.add_argument("--root", "-root", default=".", help="project root folder")
    search_repo.add_argument("--branch", "-branch", help="branch or ref to search")
    search_repo.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = REVEAL_DEFAULT_LIMIT,
        metavar = "N",
        help    = f"max commits to print (default {REVEAL_DEFAULT_LIMIT}; 0 = all)",
    )
    search_repo.add_argument(
        "--files",
        "-files",
        nargs   = "?",
        type    = int,
        const   = 20,
        default = None,
        metavar = "N",
        help    = (
            "print at most N changed files per commit; file selectors auto-print 20 "
            "(bare -files = 20; 0 = none)"
        ),
    )
    search_repo.add_argument(
        "--summary",
        "-summary",
        nargs = "*",
        help  = "summary word(s), AND-matched case-insensitively",
    )
    search_repo.add_argument(
        "--file",
        "-file",
        nargs = "*",
        help  = "file path word(s), AND-matched case-insensitively",
    )
    search_repo.add_argument("--type", "-type", action="append", help="object type text")
    search_repo.add_argument("--name", "-name", action="append", help="object name text")
    search_repo.add_argument("--by", "-by", action="append", help="author email/name text")
    search_repo.add_argument("--my", "-my", action="store_true", help="show only my commits")
    search_repo.add_argument(
        "--commit",
        "--commits",
        "-commit",
        "-commits",
        dest   = "commit_refs",
        action = "append",
        nargs  = "+",
        help   = "commit number/hash ref(s); N+ selects N and newer",
    )
    search_repo.add_argument(
        "--hash",
        "-hash",
        action = "append",
        nargs  = "+",
        help   = "commit hash prefix(es)",
    )
    search_repo.add_argument("--recent", "-recent", type=int, help="only commits from recent DAYS")
    search_repo.add_argument("--since", "-since", help="oldest commit date, YYYY-MM-DD")
    search_repo.add_argument("--until", "-until", help="newest commit date, YYYY-MM-DD")
    search_repo.add_argument(
        "--restore",
        "-restore",
        action = "store_true",
        help   = "write matching historical file versions next to the original files",
    )
    search_repo.add_argument(
        "--stage",
        "-stage",
        action = "store_true",
        help   = "with -restore, restore to original paths and git add them",
    )
    rebuild = subparsers.add_parser(
        "rebuild",
        description="rebuild the git commit cache for the current branch",
        help="rebuild the git commit cache",
    )
    rebuild.add_argument("--root", "-root", default=".", help="project root folder")
    rebuild.add_argument(
        "--branch",
        "-branch",
        action = "append",
        nargs  = "+",
        help   = "branch name(s) to include; default is the current branch",
    )
    rebuild.add_argument(
        "--reveal",
        "-reveal",
        nargs   = "*",
        default = None,
        metavar = "WORD",
        help    = "list the remote branches (origin/*) without touching the cache, "
                  "newest first. Bare '-reveal' shows the latest 10. Any words filter "
                  "by name, AND-matched ('-reveal feat 4995' lists branches whose name "
                  "contains both). Use -limit to change the row count",
    )
    rebuild.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = None,
        metavar = "N",
        help    = "meaning depends on the mode. In reveal mode: max branches to "
                  f"list (default {REVEAL_DEFAULT_LIMIT}; 0 = all), or with -switch "
                  "the max commits to show for the switched branch (same default; "
                  "0 = all). In normal rebuild mode: max commits to read per "
                  "branch, running a full bounded window (default: incremental "
                  "update since the last cached commit)",
    )
    rebuild.add_argument(
        "--since",
        "-since",
        metavar = "WHEN",
        help    = "rebuild every commit since WHEN. WHEN is a YYYY-MM-DD date, or "
                  "an integer number of days back (e.g. '7' = 7 days ago, converted "
                  "to a date). In normal mode it bounds the rebuild window and shows "
                  "'COMMITS | <count> SINCE <date>' in the header (mutually exclusive "
                  "with -limit). In reveal mode it keeps only branches whose tip "
                  "commit is on or after WHEN (composes with -limit)",
    )
    rebuild.add_argument(
        "--my",
        "-my",
        dest   = "my",
        action = "store_true",
        help   = "in reveal mode, limit to branches whose tip commit is yours "
                 "(matched against 'git config user.email')",
    )
    rebuild.add_argument(
        "--switch",
        "-switch",
        nargs   = "?",
        type    = int,
        const   = 1,
        default = None,
        metavar = "N",
        help    = "in reveal mode, check the working tree out to the Nth branch "
                  "in the filtered order (1-based; bare '-switch' = 1), then show "
                  "BRANCH SWITCHED and that branch's recent COMMITS instead of the "
                  "branch list. -limit caps the commits, -my keeps only yours. "
                  "Skips all git ops when already on that branch",
    )
    doctor = subparsers.add_parser(
        "doctor",
        description="check local ADT.ai environment setup and run explicit updates",
        help="check local setup and run explicit updates",
    )
    doctor.add_argument(
        "-offline",
        action="store_true",
        help="skip online update checks and show local versions only",
    )
    doctor.add_argument(
        "-update",
        action="store_true",
        help="run full ADT.ai, requirements, and SQLcl upgrade",
    )
    doctor.add_argument(
        "-sqlcl",
        action="store_true",
        help="upgrade SQLcl only; runs immediately without -update",
    )
    doctor.add_argument(
        "-init",
        action="store_true",
        help="scaffold project config, ignore rules, and safe local folders",
    )
    doctor.add_argument("--root", "-root", default=".", help="project root folder for -init")
    doctor.add_argument(
        "--force",
        "-force",
        action="store_true",
        help="overwrite existing generated template files with -init",
    )
    recompile = subparsers.add_parser(
        "recompile",
        description="recompile invalid database objects",
        help="recompile invalid database objects",
    )
    recompile.add_argument("--root", "-root", default=".", help="project root folder")
    recompile.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    recompile.add_argument("--env", "-env", help="connection environment")
    recompile.add_argument("--target", "-target", help="connection environment (alias of -env)")
    recompile.add_argument("--schema", "-schema", help="schema to recompile")
    recompile.add_argument(
        "--type",
        "-type",
        default = "%",
        help    = "object type pattern to recompile, supports %% wildcards",
    )
    recompile.add_argument(
        "--name",
        "-name",
        default = "%",
        help    = "object name pattern to recompile, supports %% wildcards",
    )
    recompile.add_argument(
        "--force",
        "-force",
        action = "store_true",
        help   = "recompile all matching objects, not just invalid ones",
    )
    recompile.add_argument(
        "--level",
        "-level",
        type = int,
        help = "PL/SQL optimize level (1-3)",
    )
    recompile.add_argument(
        "--native",
        "-native",
        action = "store_true",
        help   = "compile PL/SQL to native code",
    )
    recompile.add_argument(
        "--interpreted",
        "-interpreted",
        action = "store_true",
        help   = "compile PL/SQL to interpreted code (default)",
    )
    recompile.add_argument(
        "--scope",
        "-scope",
        nargs = "*",
        help  = "PL/Scope settings (IDENTIFIERS, STATEMENTS, ALL)",
    )
    recompile.add_argument(
        "--warnings",
        "-warnings",
        nargs = "*",
        help  = "PL/SQL warnings (SEVERE, PERF, INFO)",
    )
    recompile.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress object overview details; keep required command chrome",
    )
    recompile.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    discovery = subparsers.add_parser(
        "discovery",
        description="run read-only SELECT discovery queries against the target database",
        help="run read-only SELECT discovery queries",
    )
    discovery.add_argument("--root", "-root", default=".", help="project root folder")
    discovery.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    discovery.add_argument("--env", "-env", help="connection environment")
    discovery.add_argument("--schema", "-schema", help="schema to query")
    discovery.add_argument(
        "--sql",
        "-sql",
        help="a single SELECT statement to run",
    )
    discovery.add_argument(
        "--file",
        "-file",
        dest="statements_file",
        help="path to a file of ;-separated SELECT statements",
    )
    discovery.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = DEFAULT_ROW_LIMIT,
        help    = f"max rows rendered per query (default: {DEFAULT_ROW_LIMIT})",
    )
    discovery.add_argument(
        "--no-log",
        "-nolog",
        dest   = "no_log",
        action = "store_true",
        help   = "run queries and print results without writing a discovery report",
    )
    discovery.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    flow = subparsers.add_parser(
        "flow",
        description="map APEX page navigation: query incoming/outgoing links or refresh diagrams",
        help="map APEX page navigation links (to/from, refresh)",
    )
    flow.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "application id(s) — repeat or space-separate for multiple",
    )
    flow.add_argument(
        "--to",
        "-to",
        dest    = "to_page",
        type    = int,
        metavar = "PAGE",
        help    = "show pages that link INTO this page",
    )
    flow.add_argument(
        "--from",
        "-from",
        dest    = "from_page",
        type    = int,
        metavar = "PAGE",
        help    = "show pages reachable FROM this page",
    )
    flow.add_argument(
        "--refresh",
        "-refresh",
        action = "store_true",
        help   = "rescrape the application from the database and rewrite its edges",
    )
    flow.add_argument(
        "--delete",
        "-delete",
        dest   = "delete",
        action = "store_true",
        help   = "delete the application and its edges from the store",
    )
    flow.add_argument("--root", "-root", default=".", help="project root folder")
    flow.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML (refresh)",
    )
    flow.add_argument("--env", "-env", help="connection environment (refresh)")
    flow.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    for command, _description, _aliases in PUBLIC_MODULES:
        _add_completion_args(_command_parser(parser, command))
    _apply_generated_command_usages(parser)
    return parser


def _add_completion_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--beep",
        "-beep",
        action="store_true",
        help="force the completion chime on for this run, even from a worktree checkout",
    )


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

__all__ = [name for name in globals() if not name.startswith("__")]
