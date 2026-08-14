from __future__ import annotations

import argparse
import textwrap
from collections.abc import Sequence

from adt_ai.cli.constants import PUBLIC_MODULES
from adt_ai.cli.help_summaries import COMMAND_SUMMARIES

COMMON_DEST_ORDER = ("debug", "beep", "nobeep", "env", "root", "config_dir", "key")
OPTION_HELP_WIDTH = 80

ACTION_DESTS = {
    "all_formats",
    "apexlang",
    "archive",
    "calendar_offset",
    "checksum",
    "contents",
    "create",
    "delete",
    "disabled",
    "deldiff",
    "deploy",
    "dump",
    "embedded",
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
    "input",
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

# The two sets above key on `dest`, which is GLOBAL: a dest several commands
# declare cannot be grouped one way here and another way there. Where one
# dest genuinely carries different meanings in different commands, regrouping
# it by dest would move it for every command that shares it, so the override
# below is per COMMAND and is the only sanctioned way to disagree with the
# dest sets above.
COMMAND_SECTION_OVERRIDES = {
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
    grouped = _group_actions(parser._actions, canonical)
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
        for action in ordered_option_actions(parser._actions, command)
    ]
    return f"adt {command} {' '.join(tokens)}".rstrip()


def ordered_option_actions(
    actions: Sequence[argparse.Action],
    command: str | None = None,
) -> list[argparse.Action]:
    grouped = _group_actions(actions, command)
    ordered: list[argparse.Action] = []
    for _title, key in SECTION_ORDER:
        ordered.extend(grouped[key])
    return ordered


def _group_actions(
    actions: Sequence[argparse.Action],
    command: str | None = None,
) -> dict[str, list[argparse.Action]]:
    grouped: dict[str, list[argparse.Action]] = {
        "actions": [],
        "filters": [],
        "modifiers": [],
        "common": [],
    }
    overrides = COMMAND_SECTION_OVERRIDES.get(command or "", {})
    for action in actions:
        if not action.option_strings or isinstance(action, argparse._HelpAction):
            continue
        grouped[overrides.get(action.dest) or _section_key(action)].append(action)
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
