from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from adt_ai.shared.commit_cache import DEFAULT_COMMITS_TEMPLATE


class RebuildError(Exception):
    """Rebuild failed for a reason worth showing the user verbatim (no traceback)."""


class RebuildReporter(Protocol):
    def on_count(
        self,
        total_commits: int,
        branch_count: int,
        commit_limit: int | None = None,
        missing_commits: int | None = None,
    ) -> None: ...
    def on_commit_start(self, index: int, total: int) -> None: ...
    def on_commit(self, index: int, total: int) -> None: ...


@dataclass(frozen=True)
class RebuildRequest:
    root: Path
    commit_limit: int | None = None
    branches: list[str] | None = None
    # `include_full_exports` used to sit here and was never set by anything, so
    # a `patch -fullapp` run reading the cache lost `apex/<app>/f<id>.sql`
    # silently. The store keeps every changed file and the reading run applies
    # its own policy, which is where a per-run flag belongs.
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE
    update_only: bool = False
    # Resolved ISO date (YYYY-MM-DD) bounding the window for `-since`. When set,
    # the per-branch window is "every commit since this date" instead of a fixed
    # commit count; runs a full bounded rebuild like commit_limit (never update).
    since_date: str | None = None
    # `patch_history_bottom_days` from project config: where history starts on a
    # branch that has no cache yet. Unlike `since_date` this is a floor, not a
    # mode, so it never turns an incremental run into a bounded one, and an
    # explicit `-limit`/`-since` outranks it. None means the whole history.
    history_bottom_days: int | None = None

@dataclass(frozen=True)
class RebuildResult:
    cache_paths: dict[str, Path]
    branches: list[str]
    record_counts: dict[str, int]
