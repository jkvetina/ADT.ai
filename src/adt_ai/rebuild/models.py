from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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
    include_full_exports: bool = False
    cache_file_template: str = "./config/commits/#BRANCH#.yaml"
    update_only: bool = False
    # Resolved ISO date (YYYY-MM-DD) bounding the window for `-since`. When set,
    # the per-branch window is "every commit since this date" instead of a fixed
    # commit count; runs a full bounded rebuild like commit_limit (never update).
    since_date: str | None = None

@dataclass(frozen=True)
class RebuildResult:
    cache_paths: dict[str, Path]
    branches: list[str]
    record_counts: dict[str, int]
