from __future__ import annotations

import argparse
import re
import time
from collections.abc import Mapping
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any

# The month-grid renderer lives in the calendar package; aliased here so the
# `_print_calendar_grid` call site and its tests keep their existing name.
from adt_ai.calendar.render import render_calendar_grid as _print_calendar_grid

# `diff` moved to its own module at the 24 KB context cap (`#780`); re-exported
# so `runtime.py` and the announce tests keep the import they have always had.
from adt_ai.cli.commands_history_reveal import GIT_LOOKUP_FAILURES, _run_rebuild_reveal
from adt_ai.cli.commands_search_data import _run_search_data
from adt_ai.cli.commands_search_graph import _graph_requested, _run_search_graph
from adt_ai.cli.commands_search_term import _run_search_term
from adt_ai.cli.constants import (
    REVEAL_DEFAULT_LIMIT,
    CalendarError,
    CalendarRequest,
    CalendarRunner,
    ConfigError,
    ConfigLoader,
    GatewayFactory,
    RebuildRequest,
    RebuildRunner,
    SearchError,
    SearchRequest,
    SearchRunner,
    _current_branch,
    print_adt_header,
    print_module_banner,
)
from adt_ai.cli.context import (
    _config_search_paths,
    _flatten_arg_groups,
    _project_relative,
    _repo_root,
)
from adt_ai.cli.rebuild_refresh import plan_database_refresh, run_database_refresh
from adt_ai.cli.search_term_autobuild import level_commit_store
from adt_ai.patch.object_folders import object_folder_resolver
from adt_ai.rebuild.models import RebuildError
from adt_ai.rebuild.render import ConsoleRebuildReporter
from adt_ai.shared.author_aliases import author_aliases
from adt_ai.shared.commit_cache import DEFAULT_COMMITS_TEMPLATE, open_store
from adt_ai.shared.commit_store import problems_in
from adt_ai.shared.commit_window import resolve_history_floor
from adt_ai.shared.dates import resolve_since
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.file_list import file_rows, nested_files, print_file_rows
from adt_ai.shared.git_files import WorkInProgressError
from adt_ai.shared.recent_state import is_bare_recent


def _report_history_failure(error: Exception) -> int:
    """Print one `calendar`/`diff`/`rebuild`/`search` refusal, return its exit code.

    The handlers below catch a deliberately wide tuple (`#670`), and the classes
    in it are not one kind of failure: a config value ADT.ai cannot use is
    something the reader fixes in a file, while a `-branch` that is not in the
    repo or a commit store that was never built is an input that is not there
    yet. Branching on the CLASS rather than on the message is the shape
    `cli/context_errors.py` already uses for the configuration screens.
    """
    code = "CONFIGURATION INVALID" if isinstance(error, ConfigError | ValueError) else (
        "INPUT NOT FOUND"
    )
    print_adt_error(code, str(error))
    return exit_code_for(code)


def _run_rebuild(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    handler_started_at = time.monotonic()
    root = Path(args.root).expanduser().resolve()
    print_module_banner("REBUILD")

    # Every argument refusal (a `-since` that is not a date, `-switch` without
    # `-reveal`, `-since` beside `-limit`) was made before the banner, by
    # `rebuild_refresh._rebuild_argument_error` on the dispatcher's screen.
    since_value = getattr(args, "since", None)
    since_date = resolve_since(since_value) if since_value is not None else None

    if getattr(args, "reveal", None) is not None:
        # In reveal mode `-since` is a date filter on each branch's tip commit and
        # `-limit` caps the rows, they are orthogonal, so (unlike normal mode)
        # they compose instead of conflicting.
        return _run_rebuild_reveal(args, root, since_date)

    if getattr(args, "verify", False):
        return _run_rebuild_verify(args, root)

    # The database half is planned before the walk and run after it (`#30`):
    # every read that decides what to refresh happens under the banner, and the
    # commits a run always rebuilds come first on the screen.
    plan = plan_database_refresh(args, root, gateway_factory)
    if isinstance(plan, int):
        return plan

    try:
        config = ConfigLoader(
            _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
        ).load().data
        cache_file_template = str(
            config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE
        )
        branches = _flatten_arg_groups(args.branch) or []
        branch_label = _rebuild_branch_label(root, branches)
        # Default mode is an incremental update since the last cached commit.
        # An explicit -limit window runs a full bounded rebuild instead. (In
        # normal mode `-limit` is the per-branch commit cap; in reveal mode it
        # is the branch-row count, see _run_rebuild_reveal.)
        # A `-since` window is a full bounded rebuild, never an incremental
        # update (same as an explicit -limit).
        update_only = args.limit is None and since_date is None
        # `patch_history_bottom_days` is where history starts when a branch has
        # no cache yet. It is a floor on the walk, not a mode: an incremental run
        # still resumes from the cached tip, and an explicit `-limit`/`-since`
        # still wins, because a window the operator typed is more specific than
        # a project default. Without it a first build on a real root walks every
        # commit it has, and the expensive half of that walk is one file scan per
        # commit (APEXDEV_JANK: 85,108 on HEAD).
        history_floor = resolve_history_floor(config)
        request = RebuildRequest(
            root               = root,
            commit_limit       = args.limit,
            branches           = branches,
            cache_file_template= cache_file_template,
            update_only        = update_only,
            since_date         = since_date,
            history_bottom_days= history_floor,
        )
        RebuildRunner().run(
            request,
            reporter=ConsoleRebuildReporter(branch_label, since_label=since_date),
        )
    # What this block can legitimately fail with, and nothing else (`#670`):
    # the runner's own refusal (a `-branch` that is not in the repo), a config
    # value that is not a number (`resolve_history_floor`), a config file that
    # cannot be read, and the git failures the walk shares with `-reveal`. A
    # bare `except Exception` swallowed `TypeError` and friends too, so a defect
    # in this file printed the same one-line `Error:` a missing branch does,
    # with no traceback and no `-debug` hint.
    except (RebuildError, ConfigError, ValueError, *GIT_LOOKUP_FAILURES) as exc:
        return _report_history_failure(exc)

    return run_database_refresh(plan, first_started_at=handler_started_at)


# `-verify` prints one row per branch, capped like every other report line here
# so a long branch name cannot wrap the row onto a second line.
VERIFY_LINE_WIDTH = 78

# The branch column is what gives way when the row is too wide. A 40-wide column
# put `build/DEMO  9451 commits, 75522-84972, CONTIGUOUS` at 83 characters and
# the cap took the verdict off the end, printing `CONTI`: the row's whole answer,
# clipped, on the one command whose job is to report it. Real numbers are what
# showed it, on a store whose floor is 75522 because the history window started
# there; a fixture branch on a five-commit repo can never be wide enough.
VERIFY_BRANCH_WIDTH = 30


def _run_rebuild_verify(args: argparse.Namespace, root: Path) -> int:
    """Report each branch store's numbering, read-only.

    Contiguity is checkable because a number is its commit's position on the
    branch's first-parent line (ADT #895) and `rebuild` fills every position
    from the store's floor to the tip. So a gap is not a rounding error, it
    means something outside ADT.ai wrote the store, and the operator wants to
    know before a patch cites a number that is not there.
    """
    branches = _flatten_arg_groups(args.branch) or [_current_branch(root)]
    template = _commits_template(args, root)
    problems: list[str] = []
    print_adt_header("COMMIT STORES:")
    print()
    for branch in branches:
        # One aggregate over the key answers the row and the verdict both.
        with open_store(root, branch, template) as store:
            floor, ceiling, count = span = store.span()
        found = problems_in(span, branch)
        label = f"{branch[:VERIFY_BRANCH_WIDTH]:<{VERIFY_BRANCH_WIDTH}}"
        if count == 0:
            print(f"  {label} EMPTY"[:VERIFY_LINE_WIDTH])
            continue
        state = "CONTIGUOUS" if not found else "BROKEN"
        row = f"  {label} {count:>7} commits, {floor}-{ceiling}, {state}"
        print(row[:VERIFY_LINE_WIDTH])
        problems.extend(found)
    print()
    if problems:
        print_adt_header("PROBLEMS:")
        print()
        # Flat, and not a file list: a problem is a sentence about the commit
        # store, so there is no folder to group it under (ADT #504).
        for line in file_rows(problems, nested=False):
            print(line[:VERIFY_LINE_WIDTH])
        print()
        return 1
    return 0


def _run_search(
    args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    # A TERM is its own mode, and it comes first because `-app` beside it
    # narrows the text search rather than asking the graph (ADT #895). A graph
    # question is its own mode with its own banner routing, since a machine
    # `-format` sends the banner to stderr; history is what is left.
    if getattr(args, "term", None) is not None:
        # `-data` reads the database's table rows instead of the offline
        # layers (ADT #879).
        if getattr(args, "data", False):
            return _run_search_data(args, gateway_factory)
        return _run_search_term(args, gateway_factory)
    if _graph_requested(args):
        return _run_search_graph(args, gateway_factory)
    print_module_banner("SEARCH")
    try:
        file_limit = _search_file_limit(args)
        root = Path(args.root).resolve()
        # One read for both: the store location and the `object_types`
        # vocabulary `-type`/`-name` are matched against (ADT #471).
        config = _history_config(args, root)
        # Levelled with git first, as `patch` does, rather than naming the
        # `rebuild` to run (ADT #900).
        level_commit_store(args, root, config)
        result = SearchRunner().run(
            SearchRequest(
                root          = root,
                branch        = args.branch,
                config        = config,
                cache_file_template = str(
                    config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE
                ),
                commit_limit  = _search_commit_limit(args.limit),
                show_files    = file_limit > 0,
                file_limit    = file_limit,
                summary_terms = args.summary or [],
                file_terms    = args.file or [],
                object_types  = _flatten_arg_groups(args.type) or [],
                object_names  = _flatten_arg_groups(args.name) or [],
                authors       = args.by or [],
                commit_refs   = _flatten_arg_groups(args.commit_refs),
                hash_refs     = _flatten_arg_groups(args.hash),
                since         = resolve_since(args.since, option="-since") if args.since else None,
                until         = resolve_since(args.until, option="-until") if args.until else None,
                # search reads git history, which has no export watermark:
                # bare -recent keeps its documented 1-day meaning here. The
                # sentinel exists so `-recent` has ONE parser shape across every
                # module; only the modules with a watermark resolve it further.
                recent        = 1 if is_bare_recent(args.recent) else args.recent,
                my            = args.my,
                restore       = args.restore,
            )
        )
    except WorkInProgressError as exc:
        # `-restore` saves uncommitted work as `WIP` before writing, and git
        # refused, so nothing was written (ADT #897). Jan: *"ERROR header with
        # proper name and desc"*.
        print_adt_error("GIT COMMIT FAILED", exc.description, details=exc.reason)
        return exit_code_for("GIT COMMIT FAILED")
    except (SearchError, ValueError) as exc:
        return _report_history_failure(exc)
    nested = nested_files(config)
    folder_of = object_folder_resolver(config)
    if result.records:
        print_adt_header("COMMITS:")
        for record in result.records:
            print(f"{record.number}) {record.summary}")
            print(
                f"  {record.author} | {_display_commit_date(record.date)} | "
                f"{record.commit_hash[:8]}"
            )
            if file_limit > 0:
                # `depth=2` rather than the default 1: this is a per-record
                # stanza, not a section list. The subject sits at column 0 and
                # the author line at 2, so the files open one level below that
                # pair, which is the `    - M | <path>` shape `#504` inherited
                # and kept (`PROJECTS/ADT.ai/DELIVERABLES/SOP` §rebuild and
                # search). Grouping adds the folder line at 2 levels and the
                # files at 3, the same plus-one rule as everywhere else.
                print_file_rows(
                    record.files[:file_limit],
                    nested    = nested,
                    folder_of = folder_of,
                    decorate  = partial(_status_cell, statuses=record.file_statuses),
                    depth     = 2,
                )
            print()
    else:
        print("No commits found.")

    if result.restored_files:
        print_adt_header("RESTORED FILES:")
        root = Path(args.root).resolve()
        print_file_rows(
            [_project_relative(path, root) for path in result.restored_files],
            nested    = nested,
            folder_of = folder_of,
        )
    if result.failed_restores:
        # A stale cache entry can make `git show` miss; a partial restore must
        # never look like a full one.
        print_adt_header("WARNING - COULD NOT RESTORE:")
        print_file_rows(result.failed_restores, nested=nested, folder_of=folder_of)
    return 0


def _run_calendar(args: argparse.Namespace) -> int:
    print_module_banner("CALENDAR")
    root = Path(args.root).resolve()
    config = _history_config(args, root)
    jira_prefix = config.get("jira_prefix") or None
    cache_file_template = config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE
    try:
        result = CalendarRunner().run(
            CalendarRequest(
                root                = root,
                branch              = args.branch,
                month               = _resolve_calendar_month(args.month) if args.month else None,
                offset              = args.calendar_offset or 0,
                authors             = args.by or [],
                jira_prefix         = jira_prefix,
                cache_file_template = cache_file_template,
                author_aliases      = author_aliases(config),
            )
        )
    except (CalendarError, ValueError) as exc:
        return _report_history_failure(exc)

    overview = f"MONTHLY OVERVIEW {result.month}"
    if jira_prefix:
        overview += f" ({jira_prefix})"
    print_adt_header(f"{overview}:")
    if not result.authors:
        print("No commits found.")
        return 0
    for author in result.authors:
        print(f"  {author.author:<49} {author.commit_count}")

    for author in result.authors:
        print()
        print_adt_header(
            f"{author.commit_count} COMMITS BY {author.author} "
            f"({author.ticket_count} tickets, {author.pr_count} PRs):"
        )
        _print_calendar_grid(result.month, author.days)
    return 0


def _resolve_calendar_month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError(f"-month: '{value}' MUST BE YYYY-MM")
    datetime.strptime(f"{value}-01", "%Y-%m-%d")
    return value


def _history_config(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    """Load history configuration; malformed configuration is never downgraded."""
    return ConfigLoader(
        _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
    ).load().data


def _commits_template(args: argparse.Namespace, root: Path) -> str:
    """`repo_commits_file`, or the shipped default when there is no config.

    `rebuild` already falls back to the default, so a reader that raised here
    would disagree with the writer about where the store is.
    """
    config = _history_config(args, root)
    return str(config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE)


def _search_commit_limit(limit: int | None) -> int | None:
    """`-limit` as the runner takes it: the default when absent, `None` for all.

    The parser leaves an absent `-limit` as `None` so a graph question can tell
    a typed one apart from the default; the default is applied here instead.
    """
    resolved = REVEAL_DEFAULT_LIMIT if limit is None else limit
    return None if resolved == 0 else resolved


def _search_file_limit(args: argparse.Namespace) -> int:
    if args.files is not None:
        return int(args.files)
    if args.file or args.type or args.name:
        return 20
    return 0

def _display_commit_date(value: str) -> str:
    return value[:16].replace("T", " ")


def _status_cell(path: str, leaf: str, *, statuses: Mapping[str, str]) -> str:
    """One `<STATUS> | <leaf>` file row for the rebuild listing.

    A named function rather than the lambda-with-a-default this replaced: the
    default was there to bind `record.file_statuses` per commit, which is what
    `partial` says outright, and a lambda carrying an extra parameter cannot
    be read against the two-argument `decorate` hook it is passed to.
    """
    return f"{statuses.get(path, 'M')} | {leaf}"


def _rebuild_branch_label(root: Path, branches: list[str]) -> str:
    if branches:
        return ", ".join(branches)
    return _current_branch(root)


__all__ = [name for name in globals() if not name.startswith("__")]
