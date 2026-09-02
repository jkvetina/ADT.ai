"""Console reporting for the rebuild command (mirrors ``recompile/render.py``)."""

from __future__ import annotations

import time

from adt_ai.shared.progress import DottedProgressBar


class ConsoleRebuildReporter:
    # The label alone. `shared/progress.row_left_margin` prepends the two-space
    # indent every streamed row carries, so this string stopped spelling its own
    # (`#380`) and the printed row is byte-identical to what it always was.
    PROGRESS_HEADER = "REBUILDING"

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
        if self._started_at is None:
            self._started_at = time.monotonic()

    def on_commit(self, index: int, total: int) -> None:
        # Nothing to rebuild (e.g. -update with no new commits) -> instant 100%.
        if total <= 0:
            self._progress.print_line(self.PROGRESS_HEADER, 100, 0, close=True)
            return

        fraction = index / total
        percent  = min(int((fraction * 100) + 0.5), 100)
        # `on_commit_start` runs before the first `on_commit`, so the clock is
        # always set by the time a row is drawn; a caller that skips it gets a
        # zero-length run rather than a crash.
        elapsed  = time.monotonic() - (self._started_at or time.monotonic())
        remaining = (elapsed / index) * (total - index) if index else 0.0
        seconds  = int(elapsed if index == total else remaining)

        self._progress.print_line(self.PROGRESS_HEADER, percent, seconds, close=index == total)
