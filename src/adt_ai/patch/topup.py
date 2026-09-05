"""The progress `patch` prints while it tops the commit store up.

`patch` reads the shared store now, and on a root that has never been rebuilt
that read starts with a real walk: one file scan per commit, tens of thousands
of commits on a project root. That is a wait, and the block below is what covers
it.

**The section names the module that actually runs.** `#372` deleted a
`REFRESHING COMMIT STORE:` header from here and left the rows bare, on Jan's
*"I did not asked you to ADD NEW HEADERS, I asked you to print PRECEEDING
header!"*, and the reasoning recorded at the time was parity with `rebuild`,
which prints the same `BRANCH |` pair and the same dotted bar. That parity
argument was half of one: `rebuild` prints those rows directly under
`APEX DEPLOYMENT TOOL - REBUILD`, which owns them, while here the same rows sat
under `- PATCH` owning nothing, three lines above a connection block. Jan,
2026-08-21 (`#465`): *"I dont think we printed this dotted refresh line before,
it looks weird ... The original module is called REBUILD, not refresh, so the
header should reflect that and the dotted line should specify how many commits
are we processing (left part before the dots)"*. So the header is his rather
than invented, and it says REBUILDING because `RebuildRunner` in `update_only`
mode is literally what runs under it.

**The count is in the bar, so it is not also a row.** `COMMITS | 1` above a bar
labelled `1 COMMIT` is one number told twice, which is the same defect `#372`
removed a header for. `BRANCH |` stays: neither the header nor the label says
it.

**The block writes its own closing blank.** Every other section on a `-create`
screen ends with one (`_print_connection_versions`, directly under this, is the
nearest), so the section after it opens on two blank lines; this block ended on
the bar's own newline and the next header's single leading blank was all there
was. Jan, same run: *"there is 1 empty line missing below it"*.

The reporter still stays silent end to end when there is nothing to do, header
included: a store already level is the common case.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from adt_ai.shared.commit_cache import current_branch
from adt_ai.shared.progress import DottedProgressBar, print_adt_header

#: The section this block opens. Named `*_SECTION` so the console-surface scan
#: records it whether or not it can fold the call site (`#372`'s review step).
PROGRESS_SECTION = "REBUILDING COMMITS:"

#: Singular; the plural is this plus `S`, which is all English asks of this word.
ROW_UNIT = "COMMIT"


def commit_row_label(total: int) -> str:
    """``1 COMMIT`` / ``12 COMMITS``, the label the bar crawls under.

    The two-space margin is not in here: `row_left_margin` prepends it to every
    labelled row, so the indent has one owner (`#380`).

    The plural is handled rather than ignored because ONE is the common case
    here, not the rare one. `ut`'s `row_header` renders `1145 TESTS` off a bare
    count for the opposite reason, its number being a suite total; a store one
    commit behind is what a working repo looks like all day, so `1 COMMITS` is
    the reading Jan would get on most runs.
    """
    count = max(0, int(total))
    return f"{count} {ROW_UNIT}" if count == 1 else f"{count} {ROW_UNIT}S"


class ConsoleTopUpReporter:
    """A `RebuildReporter` that prints only when the top-up has work to do."""

    def __init__(self, root: Path, branch: str | None = None) -> None:
        self.root = root
        # Resolved lazily, in `on_count`. Asking git for the branch while
        # building the reporter would run before the scan and raise on a root
        # that is not a checkout, which `patch` degrades on rather than refuses
        # (`#352`): the failure would surface here instead of where it belongs.
        self._branch = branch
        self._total = 0
        self._started_at: float | None = None
        self._progress = DottedProgressBar()

    def on_count(
        self,
        total_commits: int,
        branch_count: int,
        commit_limit: int | None = None,
        missing_commits: int | None = None,
    ) -> None:
        # `missing_commits` is set when the branch resumed from a stored tip, and
        # it, not the display total, is how many commits this run will hash.
        self._total = missing_commits if missing_commits is not None else total_commits
        if self._total <= 0:
            return
        print_adt_header(PROGRESS_SECTION)
        print(f"    BRANCH | {self._resolved_branch()}")
        print()

    def _resolved_branch(self) -> str:
        if self._branch is None:
            try:
                self._branch = current_branch(self.root)
            except (subprocess.CalledProcessError, OSError):
                self._branch = "HEAD"
        return self._branch

    def on_commit_start(self, index: int, total: int) -> None:
        if self._started_at is None:
            self._started_at = time.monotonic()

    def on_commit(self, index: int, total: int) -> None:
        if self._total <= 0 or total <= 0:
            return
        percent = min(int(((index / total) * 100) + 0.5), 100)
        elapsed = time.monotonic() - (self._started_at or time.monotonic())
        remaining = (elapsed / index) * (total - index) if index else 0.0
        seconds = int(elapsed if index == total else remaining)
        closing = index == total
        self._progress.print_line(
            commit_row_label(self._total), percent, seconds, close=closing
        )
        if closing:
            # The block's own trailing blank, so the section under it opens on
            # two like every other section on the screen. Closing frame only: a
            # redraw mid-crawl still owns its line.
            print()
