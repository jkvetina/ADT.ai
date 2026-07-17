from __future__ import annotations

import argparse
import textwrap
from collections.abc import Sequence

from adt_ai.cli.constants import PUBLIC_MODULES

COMMON_DEST_ORDER = ("debug", "beep", "nobeep", "env", "root", "config_dir", "key")
OPTION_HELP_WIDTH = 80

ACTION_DESTS = {
    "all_formats",
    "archive",
    "calendar_offset",
    "continue_patch",
    "create",
    "delete",
    "disabled",
    "deldiff",
    "deploy",
    "dump",
    "embedded",
    "fetch",
    "files",
    "files_ws",
    "from_page",
    "full",
    "groups",
    "impact",
    "init",
    "install",
    "jobs",
    "list",
    "mviews",
    "offline",
    "owners",
    "readable",
    "rebuild",
    "rebuild_db",
    "refresh",
    "remove",
    "restore",
    "rest",
    "reveal",
    "split",
    "sql",
    "sqlcl",
    "stage",
    "statements_file",
    "switch",
    "synonyms",
    "to_page",
    "trailing",
    "update",
    "used_by",
    "uses",
    "with_plscope",
}

FILTER_DESTS = {
    "app",
    "branch",
    "by",
    "commit",
    "commit_refs",
    "file",
    "format",
    "group",
    "hash",
    "ignore",
    "max_app_id",
    "month",
    "my",
    "name",
    "patch_code",
    "prefix",
    "recent",
    "schema",
    "search",
    "source",
    "source_schema",
    "subfolder",
    "summary",
    "target",
    "target_schema",
    "type",
    "workspace",
    "ws",
}
# `scope` and `warnings` are deliberately absent: they tune *how* PL/SQL compiles
# (PL/Scope settings, warning levels) rather than selecting *which* objects to act
# on, so they belong in MODIFIERS. Both dests are recompile-only, so no other
# command's help shifts.

COMMAND_SUMMARIES = {
    "flow": (
        "Maps APEX page navigation links into a local SQLite store you can query offline.",
        "Use it to answer where links point to a page and which pages you can reach from a page.",
        "Refresh mode rescrapes one application from the database and rewrites its edges.",
        "The to and from queries report incoming and outgoing page links for a chosen page.",
        "Each refresh writes Mermaid, Graphviz DOT, and JSON diagrams under config/flow.",
    ),
    "dependencies": (
        "Builds and queries the Oracle dependency index used by ADT.ai impact reports.",
        "Use it to see what an object uses, what depends on it, and what a change can break.",
        "Refresh mode reloads dependency metadata from the configured database connection.",
        "Impact mode walks transitive dependencies instead of stopping at direct relationships.",
        "Use -schema OWNER to scope any query to a specific tracked owner.",
    ),
    "discovery": (
        "Runs configured read-only SELECT discovery queries against an Oracle schema.",
        "Use it to collect inventory or diagnostic facts without changing the database.",
        "Queries can be scoped to a schema and written back into the project as reports.",
        "The command is intended for safe metadata exploration before export or cleanup work.",
        "Result handling keeps discovery output reproducible instead of relying on "
        "ad hoc SQL scratchpads.",
    ),
    "doctor": (
        "Checks whether the local ADT.ai environment is ready for export and deployment work.",
        "Use it to inspect tool versions, configuration paths, and required external dependencies.",
        "Init and install actions bootstrap project configuration or missing local "
        "tooling deliberately.",
        "Update actions refresh ADT.ai-managed assets without running a deployment workflow.",
        "Debug output helps separate local setup problems from database or repository problems.",
    ),
    "export_apex": (
        "Exports Oracle APEX workspaces, applications, REST modules, files, and code reports.",
        "Use it to refresh source-controlled APEX artifacts from configured workspaces.",
        "Discovery actions reveal available workspaces and applications before choosing "
        "an export target.",
        "Format switches cover readable, split, SQLcl, and legacy export layouts "
        "used by reviewers.",
        "Modifiers control static files, embedded code extraction, REST export, "
        "cleanup, and dry-run planning.",
    ),
    "export_data": (
        "Exports configured Oracle table data into CSV files and generated MERGE scripts.",
        "Use it for reference data, seed data, or deployment data that belongs in source control.",
        "The module discovers configured tables and writes replayable scripts for selected rows.",
        "Filters limit work by schema, table name, recent changes, or configured export scope.",
        "Cleanup and dry-run options make it possible to inspect file changes before "
        "rewriting exports.",
    ),
    "export_db": (
        "Export database object DDL from configured schemas into the project tree.",
        "Use it to refresh source-controlled tables, views, packages, triggers, grants, "
        "jobs, and metadata.",
        "Filters narrow the export by schema, object type, object name, and recent DDL changes.",
        "The writer preserves configured folder layouts and can clean stale files when requested.",
        "DDL is normalized toward known ADT output parity, including readable table, "
        "view, and index formatting.",
    ),
    "rebuild": (
        "Refreshes the cached Git commit index that powers repository search workflows.",
        "Use it after new commits, branch switches, or repository fetches so ADT.ai "
        "sees current history.",
        "Incremental mode updates only missing commit data for normal day-to-day use.",
        "Full rebuild mode recreates the cache when history, parser rules, or metadata changed.",
        "Branch actions reveal remote branches and can switch the working tree when "
        "reviewing history.",
    ),
    "recompile": (
        "Recompiles invalid or selected Oracle database objects through configured "
        "database connections.",
        "Use it after deployment, export validation, or dependency repair to clear "
        "invalid objects.",
        "Object filters target specific schemas, names, types, or compiler settings.",
        "PL/SQL options control warnings, PL/Scope identifiers, native compilation, "
        "and interpreted mode.",
        "Reporting shows remaining errors so failed recompiles can be handled without "
        "hunting through SQL clients.",
    ),
    "search_repo": (
        "Searches cached Git history for commits, files, database objects, authors, and dates.",
        "Use it to answer where a change happened before opening raw Git logs manually.",
        "Filters combine branch, commit, file path, object name, hash, author, and "
        "date boundaries.",
        "Restore mode can recover historical file contents into the working tree for inspection.",
        "The command depends on rebuild's cache, so searches stay fast across large "
        "ADT repositories.",
    ),
}

SECTION_ORDER = (
    ("ACTIONS", "actions"),
    ("FILTERS", "filters"),
    ("MODIFIERS", "modifiers"),
    ("COMMON OPTIONS", "common"),
)


def format_command_help(command: str, parser: argparse.ArgumentParser) -> str:
    canonical = _canonical_command_name(command)
    parts = [
        f"usage: {generated_command_usage(canonical, parser)}",
        "",
        "SUMMARY:",
        "--------",
        *_summary_lines(canonical),
        "",
        f"More details: USAGE/{canonical}.md",
        "",
        "",
    ]
    grouped = _group_actions(parser._actions)
    for title, key in SECTION_ORDER:
        actions = grouped[key]
        if not actions:
            continue
        parts.extend([f"{title}:", "-" * len(title)])
        parts.extend(_format_action(action) for action in actions)
        parts.append("")
    return "\n".join(parts).rstrip() + "\n\n"


def generated_command_usage(command: str, parser: argparse.ArgumentParser) -> str:
    tokens = [
        _usage_token(action)
        for action in ordered_option_actions(parser._actions)
    ]
    return f"adt {command} {' '.join(tokens)}".rstrip()


def ordered_option_actions(actions: Sequence[argparse.Action]) -> list[argparse.Action]:
    grouped = _group_actions(actions)
    ordered: list[argparse.Action] = []
    for _title, key in SECTION_ORDER:
        ordered.extend(grouped[key])
    return ordered


def _group_actions(actions: Sequence[argparse.Action]) -> dict[str, list[argparse.Action]]:
    grouped: dict[str, list[argparse.Action]] = {
        "actions": [],
        "filters": [],
        "modifiers": [],
        "common": [],
    }
    for action in actions:
        if not action.option_strings or isinstance(action, argparse._HelpAction):
            continue
        grouped[_section_key(action)].append(action)
    grouped["common"].sort(key=lambda action: COMMON_DEST_ORDER.index(action.dest))
    return grouped


def _section_key(action: argparse.Action) -> str:
    if action.dest in COMMON_DEST_ORDER:
        return "common"
    if action.dest in ACTION_DESTS:
        return "actions"
    if action.dest in FILTER_DESTS:
        return "filters"
    return "modifiers"


def _format_action(action: argparse.Action) -> str:
    option_names = ", ".join(_display_option_strings(action.option_strings))
    suffix = _display_argument_suffix(action)
    left = f"  {option_names}{suffix}"
    help_text = (action.help or "").replace("%%", "%")
    if not help_text:
        return left
    first_width = 32
    if len(left) >= first_width:
        wrapped = textwrap.fill(
            help_text,
            width=OPTION_HELP_WIDTH,
            initial_indent=" " * first_width,
            subsequent_indent=" " * first_width,
        ).lstrip()
        return f"{left}\n{' ' * first_width}{wrapped}"
    wrapped = textwrap.fill(
        help_text,
        width=OPTION_HELP_WIDTH,
        initial_indent=left.ljust(first_width),
        subsequent_indent=" " * first_width,
    )
    return wrapped


def _ordered_option_strings(option_strings: Sequence[str]) -> list[str]:
    return sorted(option_strings, key=lambda option: (option.startswith("--"), option))


def _display_option_strings(option_strings: Sequence[str]) -> list[str]:
    ordered = _ordered_option_strings(option_strings)
    preferred = [
        option
        for option in ordered
        if option.startswith("-") and not option.startswith("--")
    ]
    return preferred or ordered


def _display_argument_suffix(action: argparse.Action) -> str:
    suffix = _usage_argument_suffix(action)
    return f" {suffix}" if suffix else ""


def _usage_token(action: argparse.Action) -> str:
    option = _preferred_usage_option(action)
    suffix = _usage_argument_suffix(action)
    return f"[{option} {suffix}]" if suffix else f"[{option}]"


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
        return f"[{metavar}]"
    if action.nargs == "*":
        return f"[{metavar} ...]"
    if action.nargs == "+":
        return f"{metavar} [{metavar} ...]"
    if isinstance(action.nargs, int):
        return " ".join([metavar] * action.nargs)
    return metavar


def _usage_metavar(action: argparse.Action) -> str:
    if action.metavar is not None:
        if isinstance(action.metavar, tuple):
            return str(action.metavar[0])
        return str(action.metavar)
    if action.choices is not None:
        return "{" + ",".join(str(choice) for choice in action.choices) + "}"
    return action.dest.upper()


def _canonical_command_name(command: str) -> str:
    for module_name, _description, aliases in PUBLIC_MODULES:
        if command == module_name or command in aliases:
            return module_name
    return command


def _summary_lines(command: str) -> tuple[str, ...]:
    return (" ".join(COMMAND_SUMMARIES[command]),)
