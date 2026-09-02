from __future__ import annotations

import argparse
import re
from datetime import datetime
from pathlib import Path

# The month-grid renderer lives in the calendar package; aliased here so the
# `_print_calendar_grid` call site and its tests keep their existing name.
from adt_ai.calendar.render import render_calendar_grid as _print_calendar_grid
from adt_ai.cli.commands_history_reveal import _run_rebuild_reveal
from adt_ai.cli.constants import (
    CalendarError,
    CalendarRequest,
    CalendarRunner,
    ConfigLoader,
    RebuildRequest,
    RebuildRunner,
    SearchRepoError,
    SearchRepoRequest,
    SearchRepoRunner,
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
from adt_ai.patch.object_folders import object_folder_resolver
from adt_ai.rebuild.render import ConsoleRebuildReporter
from adt_ai.shared.commit_cache import DEFAULT_COMMITS_TEMPLATE, open_store
from adt_ai.shared.commit_window import resolve_history_floor
from adt_ai.shared.dates import resolve_since
from adt_ai.shared.file_list import file_rows, nested_files, print_file_rows
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

    if getattr(args, "verify", False):
        return _run_rebuild_verify(args, root)

    if since_date is not None and args.limit is not None:
        print("Error: -since and -limit cannot be combined")
        print()
        return 1

    try:
        config = ConfigLoader(
            _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
        ).load().data
        cache_file_template = str(
            config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE
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
    except Exception as exc:
        print(f"Error: {exc}")
        print()
        return 1

    return 0


# `-verify` prints one row per branch, capped like every other report line here
# so a long branch name cannot wrap the row onto a second line.
VERIFY_LINE_WIDTH = 78

# The branch column is what gives way when the row is too wide. A 40-wide column
# put `build/JANK  9451 commits, 75522-84972, CONTIGUOUS` at 83 characters and
# the cap took the verdict off the end, printing `CONTI`: the row's whole answer,
# clipped, on the one command whose job is to report it. Real numbers are what
# showed it, on a store whose floor is 75522 because the history window seeded it
# there; a fixture branch on a five-commit repo can never be wide enough.
VERIFY_BRANCH_WIDTH = 30


def _run_rebuild_verify(args: argparse.Namespace, root: Path) -> int:
    """Report each branch store's numbering, read-only.

    Contiguity is checkable precisely because allocation is additive: floor to
    ceiling with no gap is the only shape `allocate` and `backfill` can produce.
    So a gap is not a rounding error, it means something outside the store wrote
    it, or history was rewritten under it, and either way the operator wants to
    know before a patch cites a number that is not there.
    """
    branches = _flatten_arg_groups(args.branch) or [_current_branch(root)]
    template = _commits_template(args, root)
    problems: list[str] = []
    print_adt_header("COMMIT STORES:")
    print()
    for branch in branches:
        with open_store(root, branch, template) as store:
            found = store.verify(branch)
            floor, ceiling = store.floor(branch), store.ceiling(branch)
            count = len(store.numbers(branch))
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


def _run_search_repo(args: argparse.Namespace) -> int:
    print_module_banner("SEARCH_REPO")
    try:
        file_limit = _search_repo_file_limit(args)
        root = Path(args.root).resolve()
        # One read for both: the store location and the `object_types`
        # vocabulary `-type`/`-name` are matched against (ADT #471).
        config = _history_config(args, root)
        result = SearchRepoRunner().run(
            SearchRepoRequest(
                root          = root,
                branch        = args.branch,
                config        = config,
                cache_file_template = str(
                    config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE
                ),
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
                # search_repo). Grouping adds the folder line at 2 levels and the
                # files at 3, the same plus-one rule as everywhere else.
                print_file_rows(
                    record.files[:file_limit],
                    nested    = nested,
                    folder_of = folder_of,
                    decorate  = lambda path, leaf, statuses=record.file_statuses: (
                        f"{statuses.get(path, 'M')} | {leaf}"
                    ),
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
        print_adt_header("COULD NOT RESTORE:")
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


def _history_config(args: argparse.Namespace, root: Path) -> dict:
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


def _search_repo_file_limit(args: argparse.Namespace) -> int:
    if args.files is not None:
        return args.files
    if args.file or args.type or args.name:
        return 20
    return 0

def _display_commit_date(value: str) -> str:
    return value[:16].replace("T", " ")


def _rebuild_branch_label(root: Path, branches: list[str]) -> str:
    if branches:
        return ", ".join(branches)
    return _current_branch(root)


__all__ = [name for name in globals() if not name.startswith("__")]
