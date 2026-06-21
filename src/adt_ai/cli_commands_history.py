from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from adt_ai.cli_constants import (
    _current_branch,
    branch_commits,
    BranchInfo,
    ConfigLoader,
    DottedProgressBar,
    print_adt_header,
    RebuildRequest,
    RebuildRunner,
    reveal_branches,
    REVEAL_DEFAULT_LIMIT,
    SearchRepoError,
    SearchRepoRequest,
    SearchRepoRunner,
    switch_to_branch,
)
from adt_ai.cli_context import _config_search_paths, _display, _flatten_arg_groups, _repo_root




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

__all__ = [name for name in globals() if not name.startswith("__")]
