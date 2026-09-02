"""How far back a from-scratch commit store reaches, and where it starts counting.

`patch_history_bottom_days` bounds the walk. A real project root is large,
APEXDEV_JANK carries 85,108 commits on HEAD, and the expensive half of a rebuild
is one `changed_files` call per commit, so walking years nobody will query is
work paid for and thrown away. Jan, 2026-08-15: *"if the repo is huge, we dont
need it all, last couple of months should be perfectly fine."*

**The seed is the part that is easy to get wrong.** A bounded first build must
not start numbering at 1. Raising the key later pulls older commits in, and a
number is never re-derived, so those older commits have to fit *below* what is
already stored. Seeding the bottom commit at its true position in history leaves
exactly that room: with 85,108 commits and a one-year window starting at 82,000,
the range 1 to 81,999 is reserved for a backfill that may never come and costs
nothing if it does not.

The position is read once, here, when the branch is empty. Everything after that
is allocation, never derivation, which is what keeps a later merge from moving a
number that was already handed out.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from adt_ai.shared.git_files import run_git

#: Shipped default for `patch_history_bottom_days`, in days.
DEFAULT_HISTORY_BOTTOM_DAYS = 365

_FIELD = "\x1f"


def resolve_history_floor(config: dict[str, Any]) -> int | None:
    """`patch_history_bottom_days` from project config, or the shipped default.

    Returns None when the project asks for the whole history (0 or negative),
    which is the same shape every other "no bound" value in the rebuild request
    already uses, so the caller needs no special case.
    """
    raw = config.get("patch_history_bottom_days", DEFAULT_HISTORY_BOTTOM_DAYS)
    if raw is None or raw == "":
        return DEFAULT_HISTORY_BOTTOM_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"patch_history_bottom_days must be a whole number of days, got {raw!r}"
        ) from None
    return days if days > 0 else None


@dataclass(frozen=True)
class HistoryWindow:
    """The slice of a branch a from-scratch build will walk."""

    #: Oldest commit inside the window, or None when the branch has no commits.
    bottom_commit: str | None
    #: Its subject, for the console line that says where history now starts.
    bottom_summary: str
    #: The number that bottom commit takes, its true position in history.
    seed: int
    #: How many commits the window holds (the walk's real size).
    commits: int
    #: How many commits the branch holds in total.
    total: int

    @property
    def skipped(self) -> int:
        """Commits below the window, left unwalked and unnumbered."""
        return self.total - self.commits


def resolve_history_window(
    root: Path,
    branch: str,
    bottom_days: int | None = DEFAULT_HISTORY_BOTTOM_DAYS,
    *,
    today: str | None = None,
) -> HistoryWindow:
    """Resolve `patch_history_bottom_days` against a branch's real history.

    ``today`` is injectable so the window is testable without freezing the
    clock; production leaves it None and the real date is read here.
    """
    total = _count(root, branch)
    if total == 0:
        return HistoryWindow(
            bottom_commit=None, bottom_summary="", seed=1, commits=0, total=0
        )

    lines = _log(root, branch, since=_since(bottom_days, today))
    if not lines:
        # Every commit predates the window. An empty store is not a usable
        # answer, so keep the newest commit: history starts there and a wider
        # key backfills the rest later.
        lines = _log(root, branch, since=None)[-1:]

    bottom_hash, bottom_summary = lines[0]
    seed = position_of(root, bottom_hash)
    return HistoryWindow(
        bottom_commit  = bottom_hash,
        bottom_summary = bottom_summary,
        seed           = seed,
        commits        = len(lines),
        total          = total,
    )


def position_of(root: Path, commit: str) -> int:
    """``commit``'s own position in history, counting from 1 at the root commit.

    This is the seed a bounded first build starts from, and it is read exactly
    once per branch, while the store is empty. `rev-list --count <commit>`
    counts that commit and every ancestor, which is its position when numbering
    starts at 1, so the whole range underneath stays reserved for a backfill
    that may never come and costs nothing if it does not.

    It is deliberately NOT `_branch_commit_count`: that one counts a branch tip
    and feeds the console total, and a test measures that it runs once per
    branch. Two different questions, two different functions.
    """
    return _count(root, commit)


def _since(bottom_days: int | None, today: str | None) -> str | None:
    if not bottom_days or bottom_days <= 0:
        return None
    anchor = date.fromisoformat(today) if today else date.today()
    return (anchor - timedelta(days=bottom_days)).isoformat()


def _count(root: Path, revision: str) -> int:
    # A branch with no commits makes `rev-list` exit non-zero ("unknown
    # revision"), which is a fact about the repo rather than a failure: a fresh
    # `git init` is a legitimate root to point `rebuild` at.
    try:
        out = run_git(root, ["rev-list", "--count", revision])
    except subprocess.CalledProcessError:
        return 0
    return int(out.strip() or "0")


def _log(root: Path, branch: str, since: str | None) -> list[tuple[str, str]]:
    args = ["log", "--reverse", f"--format=%H{_FIELD}%s"]
    if since is not None:
        args.append(f"--since={since} 00:00:00")
    args.append(branch)
    try:
        out = run_git(root, args)
    except subprocess.CalledProcessError:
        return []
    rows: list[tuple[str, str]] = []
    for line in out.split("\n"):
        if not line.strip():
            continue
        commit_hash, _, summary = line.partition(_FIELD)
        rows.append((commit_hash, summary))
    return rows
