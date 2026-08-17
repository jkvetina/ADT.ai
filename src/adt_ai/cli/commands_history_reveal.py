"""`rebuild -reveal` and `-reveal -switch`: the read-only branch inspector.

Split out of `commands_history.py` at the 20 KB per-file context budget, along
the seam that file already had. Everything here answers "which branch", touches
no commit store, and is reached only through `-reveal`; everything left behind
runs a command against one.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from adt_ai.cli.constants import (
    REVEAL_DEFAULT_LIMIT,
    BranchInfo,
    _current_branch,
    branch_commits,
    print_adt_header,
    reveal_branches,
    switch_to_branch,
)

# `-reveal` shows branch names only, clipped to this width so long feature
# branch names don't wrap the report.
REVEAL_NAME_WIDTH = 78

# `-switch` prints the branch name and each commit line flush-left, every line
# capped at this width so a long branch name or commit subject can't wrap.
SWITCH_LINE_WIDTH = 78


def _run_rebuild_reveal(
    args: argparse.Namespace, root: Path, since_date: str | None = None
) -> int:
    # Read-only branch inspector: lists branches, never touches the store.
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
