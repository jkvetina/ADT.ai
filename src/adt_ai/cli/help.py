from __future__ import annotations

import argparse
import textwrap
from collections.abc import Sequence

from adt_ai.cli.constants import PUBLIC_MODULES
from adt_ai.cli.help_summaries import COMMAND_SUMMARIES

COMMON_DEST_ORDER = ("debug", "beep", "nobeep", "env", "root", "config_dir", "key")
OPTION_HELP_WIDTH = 80

# Every flag a command declares lands in exactly one section, and which one is
# a rule, not a feel (TECHNICAL_REQUIREMENTS.md §Help, ADT #894):
#
#   ACTIONS    the verbs a run exists to perform, and the operands it cannot
#              start without (`diff`'s two sides, `patch -name`).
#   MODES      an optional flag that turns the command's default run into a
#              different kind of run, "instead of" what it does bare.
#   FILTERS    an optional flag that narrows what the run touches. Selectors of
#              one thing render together, `-app` beside `-page`.
#   MODIFIERS  an optional flag that tunes how the run works or prints,
#              including a side effect such as writing what it found.
#
# Every dest is classified by name in one of the four sets below, or per
# command in `COMMAND_SECTION_OVERRIDES`. There is no fall-through:
# `test_help_sections.py` fails on a dest nothing classifies, because the old
# silent default to MODIFIERS is what shipped `-apexlang` as a modifier (`#160`)
# and left `connection`'s five verbs there beside the values they write.

ACTION_DESTS = {
    "add_env",
    "add_schema",
    "all_formats",
    "apexlang",
    "archive",
    "create",
    "deploy",
    "drop",
    "embedded",
    "files",
    "files_ws",
    "full",
    "install",
    "readable",
    "rekey",
    "rest",
    "set_pwd",
    "set_wallet_pwd",
    "split",
    "sql",
    "statements_file",
    # `patch -upload`, ADT #903. A verb beside `-install`, `-archive` and
    # `-drop`: it is what the run DOES, not a tuning knob on a build.
    "upload",
}
# `#894` emptied this set of every flag that was a mode or a tuning knob filed
# as a verb. The report flags of `recompile`, the reveal flags, `-groups`,
# `doctor -update`/`-sqlcl`/`-init` and `search`'s graph questions went to
# MODE_DESTS; `-offline`, `-delete`, `-refresh`, `-owners` and `-restore`
# tune a run rather than being one, so they went to MODIFIER_DESTS (`-stage`
# went with them, and left with `#897`, folded into `-restore`);
# `calendar -calendar` picks a month the way `-month` does, so it is a filter.
# Three dests left this set with ADT #345. `list` and `rebuild` went with the
# flags themselves, which parsed and did nothing. `rebuild_db` was already
# stale: the dependency command's `-rebuild-db` was retired when `-refresh` became the
# update path, and its dest outlived it here. A dest naming no live flag is
# inert, so nothing misrendered, which is exactly why it sat unnoticed.
#
# Five more left with `#362`, and the count is why that card stopped trusting
# the sweep and wrote a guard. `contents` and `deldiff` were withdrawn by `#353`
# and `#356` in the 2026-08-15 batch; `flow`'s `-dump` and `-remove` have been
# on `REMOVED_COMPATIBILITY_FLAGS` since `#292`; `with_plscope` names a flag no
# parser has ever declared. `test_help_sections.py` now fails on any dest here
# that no command declares, so the next withdrawal cannot leave one behind.
#
# Four more left with `#30`, when `dependencies` and `flow` were retired:
# `uses` and `used_by` were the former's query dests and `from_page` and
# `to_page` the latter's. `search` asks all four through `graph_from` and
# `graph_to`.

FILTER_DESTS = {
    "app",
    "branch",
    "input",
    "by",
    "calendar_offset",
    "commit",
    "commit_refs",
    "component",
    "file",
    "group",
    "hash",
    "ignore",
    "layer",
    "max_app_id",
    "month",
    "my",
    # `patch_code` sat beside this until ADT #465 renamed `patch -patch` to
    # `-name`, which is the dest already listed here. It stays, because `-name`
    # genuinely narrows a list on `ut`, `recompile`, `export_db` and
    # `search`; `patch` overrides it to ACTIONS below, where it names the
    # one thing the run acts on instead of filtering anything (ADT #494, moved
    # again by `#598`).
    "name",
    "page",
    "prefix",
    "recent",
    "schema",
    "search",
    "since",
    "source",
    "summary",
    "target",
    "target_schema",
    "type",
    "until",
    "workspace",
    "ws",
}
# `scope` and `warnings` are deliberately absent: they tune *how* PL/SQL compiles
# (PL/Scope settings, warning levels) rather than selecting *which* objects to act
# on, so they belong in MODIFIERS. Both dests are recompile-only, so no other
# command's help shifts.
#
# `page` and `component` joined with `#894`. Both narrow what an application
# run touches, and `-page` sat under MODIFIERS on `export_apex` and `validate`
# while `-app` sat under FILTERS, one screen apart from the flag it narrows.
# `since` and `until` bound the commits a run reads exactly as `-recent` does, so
# they read beside it. `format` left: it shapes the output, which is a modifier.

MODE_DESTS = {
    "apex",
    "baseline",
    "constraint",
    "data",
    "disabled",
    "graph_from",
    "graph_to",
    "groups",
    "impact",
    "init",
    "jobs",
    "mviews",
    "once",
    "reveal",
    "scan",
    "sqlcl",
    "switch",
    "synonyms",
    "term",
    "trailing",
    "update",
    "verify",
    "vpd",
}
# Jan, 2026-09-19, after `#893` gave `diff` the first MODES section: *"Create a
# task for other modules which have MODES to create dedicated section (patch
# -hash and others)"*. What each dest here switches the run to, by command:
# `recompile`'s six report modes (the name `docs/recompile.md` already used),
# `-reveal` on `export_apex` and `rebuild` with `rebuild`'s `-switch` and
# `-verify`, `-groups` on both exports, `export_db -baseline` and `patch`'s hash
# pair, `doctor`'s three upgrades, `search`'s graph questions, `validate -scan`,
# `patch -once` (ADT #903, was `live_upload -once`), and `diff`'s `-data` and
# `-apex`.
#
# `term` is `search TERM` (ADT #895), the one positional any command declares:
# it answers where a piece of text lives instead of searching history, which is
# question 4. Its `-layer` narrows which layers it reads, question 5, and is
# the one `search` filter that belongs to neither titled family, so it renders
# under the plain `FILTERS` title the other commands use.

MODIFIER_DESTS = {
    "compact",
    "continue_patch",
    "deep",
    "default",
    "delete",
    "encrypt",
    "folder",
    "force",
    "format",
    "gate",
    "go",
    "head",
    "host",
    "interpreted",
    "interval",
    "level",
    "like",
    "limit",
    "local",
    "mirror",
    "native",
    "new_key",
    "no_log",
    "nosnap",
    "offline",
    "old_key",
    "out",
    "owners",
    "port",
    "refresh",
    "release",
    "restore",
    "scope",
    "service",
    "show",
    "sid",
    "silent",
    # `doctor -sync` (ADT #938): only makes sense with `-init`, exactly like
    # `-force` above, and does not switch the run to a mode of its own.
    "sync",
    "thick",
    "user",
    "verbose",
    "wallet",
    "warnings",
}

# The four sets above key on `dest`, which is GLOBAL: a dest several commands
# declare cannot be grouped one way here and another way there. Where one
# dest genuinely carries different meanings in different commands, regrouping
# it by dest would move it for every command that shares it, so the override
# below is per COMMAND and is the only sanctioned way to disagree with the
# dest sets above.
COMMAND_SECTION_OVERRIDES = {
    # Exactly one of the five verbs is required (`docs/connection.md`), and
    # `-schema` is the entry most of them act on, so it closes ACTIONS the way
    # `-name` closes `patch`'s. The four "with -create, set ..." rows write a
    # value into the entry, beside `-host` and `-port`, and narrow nothing
    # (ADT #894).
    "connection": {
        "schema": "actions",
        "workspace": "modifiers",
        "app": "modifiers",
        "prefix": "modifiers",
        "ignore": "modifiers",
    },
    # The third use of the override, and the first that draws the line on
    # OPTIONALITY rather than on subject matter. Jan, 2026-09-11, reading the
    # four under `FILTERS:`: *"There are really not FILTERS, these are required
    # to function, so no FILTERS section, but ACTIONS"*, then, asked where the
    # other two go: *"-name -type ARE optional filters! -source and -target are
    # NOT"*.
    #
    # Which is the honest reading of the screen. A `diff` run IS these four
    # values, two environments and two schemas, and a flag the command cannot
    # run without narrows nothing; `-type` and `-name` stay below because they
    # are what a filter actually is, a thing you may leave out. Every dest here
    # is shared (`schema` alone is declared by six commands, where it really
    # does pick what to act on), which is why this is per-command rather than a
    # `FILTER_DESTS` move.
    "diff": {
        "source": "actions",
        "schema": "actions",
        "target": "actions",
        "target_schema": "actions",
        # The three flags that switch WHAT `diff` compares get a section of
        # their own, between the two sides and the filters. Asked where they
        # belong once there were three of them, Jan picked, 2026-09-19 (ADT
        # #893): *"New MODES section (Recommended)"*. `rest` had sat under
        # MODIFIERS since `#878`; it is an export ACTION on `export_apex`, which
        # is why this is per command. The flags a mode scopes stay FILTERS:
        # `-app`, `-ignore`, and `-page`. `data`, `apex` and `page` are also
        # classified by dest since `#894`; the entries stay as this command's
        # own record.
        "rest": "modes",
        "data": "modes",
        "apex": "modes",
        "page": "filters",
        # `-target-app` picks the target's application the way `-app` picks
        # the source's, so it reads beside it (ADT #893). `-branch` is a
        # filter by dest on the history commands; here it tunes where `-restore`
        # writes, so it joins `-restore` under MODIFIERS, where Jan asked for both.
        "target_app": "filters",
        "branch": "modifiers",
    },
    # `live_upload`'s own two-entry override lived here until ADT #903 folded the
    # command into `patch -upload`. Its `-app` and `-workspace` are `patch`'s own
    # `-app` and `-files_ws` now, both already classified below.
    "patch": {
        # `-target` and `-name` are the two things a `patch` run acts ON, so they
        # render beside the verbs that act on them. `#494` had lifted the pair
        # out of FILTERS into MODIFIERS; Jan moved them again on 2026-08-30 (ADT
        # #598), to ACTIONS, at the end.
        #
        # `-name` shipped under FILTERS in the first place because the dest is
        # shared with four commands where it really does narrow a list. On
        # `patch` it selects nothing: `#465` made it the single spelling of the
        # NOUN, with `-create`/`-deploy` as verbs acting on it, which is exactly
        # the relation `-target` has to a deploy. Sequence inside a section is
        # parser declaration order, so the pair renders `-target` then `-name`
        # off `parser_patch.py`'s own order rather than off this dict's.
        "target": "actions",
        "name": "actions",
        # `-app` came out of FILTERS the other way on the same day. The dest is
        # a genuine selector on `export_apex`, `rebuild` and `validate`, where it picks
        # applications; on `patch` `#592` made the value the id the tree LANDS
        # on, so the flag tunes how the patch is built rather than narrowing what
        # reaches it. It is declared last in `parser_patch.py`'s modifier run,
        # which is what renders it at the end of the section.
        "app": "modifiers",
        # `files_ws` is an ACTION on `export_apex`, where it selects what to
        # export; on `patch` it widens what a build carries (ADT #812).
        "files_ws": "modifiers",
        # `-hash` builds from the baseline instead of from commits. The dest is a
        # commit-hash FILTER on `search`, so `patch` claims it here; `-baseline`
        # is a mode on both commands that declare it.
        "hash": "modes",
        # `-once` was a MODE on `live_upload`, where it was the whole of what the
        # run did differently. Folded into `patch` (ADT #903) it reads only beside
        # `-upload`, and a row opening "with -X" is never a run of its own
        # (`test_a_flag_that_needs_another_is_never_an_action_or_a_mode`), so it
        # tunes upload mode from MODIFIERS instead.
        "once": "modifiers",
    },
    # `search` answers two families of question that never mix: a history
    # filter beside a graph question is refused, and so is a graph filter on a
    # history run. Jan, 2026-09-19, reading `-app` under ACTIONS and `-page`
    # under FILTERS, `-files` under ACTIONS and twelve rows under FILTERS: *"search
    # module help is total garbage"*. Asked with both screens rendered, he picked
    # the one that files each family's filters under its own title (ADT #894).
    # `-type` and `-name` narrow `-app` too, and read with history because that
    # is where most runs use them.
    "search": {
        "app": "graph_filters",
        "page": "graph_filters",
        "schema": "graph_filters",
        "branch": "history_filters",
        "commit_refs": "history_filters",
        "hash": "history_filters",
        "summary": "history_filters",
        "file": "history_filters",
        "type": "history_filters",
        "name": "history_filters",
        "by": "history_filters",
        "my": "history_filters",
        "recent": "history_filters",
        "since": "history_filters",
        "until": "history_filters",
        # `files` is an export ACTION on `export_apex`; here it prints each
        # commit's changed files, which tunes the output.
        "files": "modifiers",
    },
}

# An empty section is never printed, which is what lets one global order carry
# a section only some commands fill.
#
# MODES follows ACTIONS (Jan, `#893`). `patch` had the only mode section before
# it, `HASH MODE`, below MODIFIERS because Jan read a section one command owned
# as a wedge through the shape every screen shares (`#365`). With modes on ten
# commands it is part of that shape, so `#894` folded `HASH MODE` into it.
#
# The two titled filter sections are `search`'s alone and stand where FILTERS
# stands, which it never prints.
SECTION_ORDER = (
    ("ACTIONS", "actions"),
    ("MODES", "modes"),
    ("FILTERS", "filters"),
    ("GRAPH FILTERS", "graph_filters"),
    ("HISTORY FILTERS", "history_filters"),
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
        f"More details: docs/{canonical}.md",
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
    grouped: dict[str, list[argparse.Action]] = {key: [] for _title, key in SECTION_ORDER}
    overrides = COMMAND_SECTION_OVERRIDES.get(command or "", {})
    for action in actions:
        # A positional renders too, by its metavar: `search TERM` is the first
        # one any command declares (ADT #895), and a run it changes is a row
        # the screen owes the reader like any flag's.
        if isinstance(action, argparse._HelpAction):
            continue
        grouped[overrides.get(action.dest) or _section_key(action)].append(action)
    grouped["common"].sort(key=lambda action: COMMON_DEST_ORDER.index(action.dest))
    return grouped


def _section_key(action: argparse.Action) -> str:
    if action.dest in COMMON_DEST_ORDER:
        return "common"
    if action.dest in ACTION_DESTS:
        return "actions"
    if action.dest in MODE_DESTS:
        return "modes"
    if action.dest in FILTER_DESTS:
        return "filters"
    # MODIFIER_DESTS, and at runtime anything unclassified: a screen still
    # renders, while the contract test refuses to let one ship.
    return "modifiers"


def _format_action(action: argparse.Action) -> str:
    if action.option_strings:
        option_names = ", ".join(_display_option_strings(action.option_strings))
        left = f"  {option_names}{_display_argument_suffix(action)}"
    else:
        left = f"  {_usage_metavar(action)}"
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
    if not action.option_strings:
        # Always bracketed: a positional ADT.ai declares is optional, since a
        # required one would let argparse's usage dump replace the banner.
        return f"[{_usage_metavar(action)}]"
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
