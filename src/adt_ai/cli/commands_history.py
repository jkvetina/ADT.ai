from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

# The month-grid renderer lives in the calendar package; aliased here so the
# `_print_calendar_grid` call site and its tests keep their existing name.
from adt_ai.calendar.render import render_calendar_grid as _print_calendar_grid
from adt_ai.cli.constants import (
    _current_branch,
    branch_commits,
    BranchInfo,
    CalendarError,
    CalendarRequest,
    CalendarRunner,
    ConfigLoader,
    print_adt_header,
    print_module_banner,
    RebuildRequest,
    RebuildRunner,
    reveal_branches,
    REVEAL_DEFAULT_LIMIT,
    SearchRepoError,
    SearchRepoRequest,
    SearchRepoRunner,
    switch_to_branch,
)
from adt_ai.cli.context import _config_search_paths, _display, _flatten_arg_groups, _repo_root
from adt_ai.rebuild.render import ConsoleRebuildReporter
from adt_ai.shared.dates import resolve_since
from adt_ai.shared.recent_state import is_bare_recent




def _run_rebuild(args: argparse.Namespace) -> int:
    root = Path(args.root).expanduser().resolve()
    print_module_banner("REBUILD")

    since_value = getattr(args, "since", None)
    since_date: str | None = None
    if since_value is not None:
        try:
            since_date = resolve_since(since_value)
        except ValueError as exc:
            print(f"Error: {exc}")
            print()
            return 1

    if getattr(args, "reveal", None) is not None:
        # In reveal mode `-since` is a date filter on each branch's tip commit and
        # `-limit` caps the rows, they are orthogonal, so (unlike normal mode)
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
        # is the branch-row count, see _run_rebuild_reveal.)
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
    print_module_banner("SEARCH_REPO")
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
                object_types  = _flatten_arg_groups(args.type) or [],
                object_names  = _flatten_arg_groups(args.name) or [],
                authors       = args.by or [],
                commit_refs   = _flatten_arg_groups(args.commit_refs),
                hash_refs     = _flatten_arg_groups(args.hash),
                since         = resolve_since(args.since, option="-since") if args.since else None,
                until         = resolve_since(args.until, option="-until") if args.until else None,
                # search_repo reads git history, which has no export watermark:
                # bare -recent keeps its documented 1-day meaning here. The
                # sentinel exists so `-recent` has ONE parser shape across every
                # module; only the modules with a watermark resolve it further.
                recent        = 1 if is_bare_recent(args.recent) else args.recent,
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
    if result.failed_restores:
        # A stale cache entry can make `git show` miss; a partial restore must
        # never look like a full one.
        print_adt_header("COULD NOT RESTORE:")
        for file_path in result.failed_restores:
            print(f"  - {file_path}")
    return 0


def _run_calendar(args: argparse.Namespace) -> int:
    print_module_banner("CALENDAR")
    root = Path(args.root).resolve()
    try:
        config = ConfigLoader(
            _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
        ).load().data
    except Exception as exc:
        # The calendar works without a config (no jira_prefix, default cache
        # path), but a broken config must not be indistinguishable from none.
        print(f"Warning: could not read config ({exc}); using defaults", file=sys.stderr)
        config = {}
    jira_prefix = config.get("jira_prefix") or None
    cache_file_template = config.get("repo_commits_file") or "./config/commits/#BRANCH#.yaml"
    try:
        result = CalendarRunner().run(
            CalendarRequest(
                root                = root,
                branch              = args.branch,
                month               = _resolve_calendar_month(args.month) if args.month else None,
                offset              = args.calendar_offset or 0,
                authors             = args.by or [],
                jira_prefix         = jira_prefix,
                list_mode           = args.list,
                cache_file_template = cache_file_template,
            )
        )
    except (CalendarError, ValueError) as exc:
        print(f"Error: {exc}")
        print()
        return 1

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
        raise ValueError(f"-month: '{value}' must be YYYY-MM")
    datetime.strptime(f"{value}-01", "%Y-%m-%d")
    return value


def _search_repo_file_limit(args: argparse.Namespace) -> int:
    if args.files is not None:
        return args.files
    if args.file or args.type or args.name:
        return 20
    return 0

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
    # resolves regardless of `-limit`, which here caps the COMMITS section, not
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

    # Skip the checkout entirely when we're already on the branch, no git ops,
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
    print()  # second blank line above the header, print_adt_header adds one, we want two
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

__all__ = [name for name in globals() if not name.startswith("__")]
