"""The `RESTORED FILES:` section `diff -restore` prints above the legend (ADT #893, #897).

Jan, 2026-09-19: *"if this is passed, you will resurrect these target versions
into their location in current/requested branch, so user can see all the
changes there!"* The screen for it is one section, after the listings and
before `LEGEND:`:

* **Uncommitted work is saved first** (`#897`), as one `WIP` commit on the
  branch the run started on, and a clean checkout gets none. Jan: *"before we
  do -pull, we should commit what we have as "WIP" and dont push"*, and *"No
  line for either of the commits."* A save git refuses is the one restore
  failure that is not `ERROR - DIFF FAILED:`: it prints `ERROR - GIT COMMIT FAILED:`
  with git's own line in place of the section, since nothing was restored.
* **The header goes up before the work**, and the restore runs under one
  countdown row naming the target and the branch it writes to, the
  `COMPARING SCHEMAS:` shape: an export of a whole application is seconds of
  work, and a header alone over them is the silence the console contract bans.
  The exporters' own screens are kept off it, since the row is what reports
  them; `-debug` shows them and skips the row, as it does for the comparison.
* **What changed is git's answer**, one row per file, `FILE` and `STATUS`
  (`NEW`, `MODIFIED`, `DELETED`), fitted to the screen like every listing
  here and capped by `-limit`. A restore that moved nothing says so in one line.
* `BRANCH:` closes it, in the register of `LOG:` and `LIMIT:`, so the reader
  knows where to run `git diff`.
"""
from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from adt_ai.cli.constants import print_adt_header
from adt_ai.cli.diff_reporters import print_listing
from adt_ai.diff.pull import PullSides
from adt_ai.diff.pull_git import pulled_files, switch_branch
from adt_ai.diff.timers import pair_key, previous_seconds, record_seconds, timers_path
from adt_ai.shared.connections import Connection
from adt_ai.shared.error_screen import print_adt_error
from adt_ai.shared.git_files import WorkInProgressError, save_work_in_progress
from adt_ai.shared.timed_bar import FALLBACK_TARGET_SECONDS, TimedProgressBar

PULLED_HEADER = "RESTORED FILES:"
BRANCH_NOTE = "  BRANCH: {branch}"
NOTHING_PULLED = "  nothing changed on disk, the checkout already holds the target's versions"

#: Only the path gives way when a row will not fit; the status never does.
_FILE_TIERS = (("file",),)


@dataclass
class PullScreen:
    """Where `-restore` writes and how its section reads; `failed` once a restore raised."""

    root        : Path
    branch      : str | None
    connections : tuple[Connection, Connection]
    limit       : int | None = None
    debug       : bool = False
    failed      : bool = False

    def run(self, mode: str, pull: Callable[[], object]) -> None:
        """Save any WIP, switch the branch, run `pull` under a row, then list what git sees."""
        try:
            save_work_in_progress(self.root)
        except WorkInProgressError as error:
            if self.debug:
                raise
            self.failed = True
            print_adt_error("GIT COMMIT FAILED", error.description, details=error.reason)
            return
        print_adt_header(PULLED_HEADER)
        print()
        try:
            branch = switch_branch(self.root, self.branch)
            self._pull(mode, branch, pull)
        except (RuntimeError, OSError, ValueError) as error:
            if self.debug:
                raise
            self.failed = True
            print_adt_error("DIFF FAILED", str(error))
            return
        files = pulled_files(self.root)
        if files:
            print_listing(
                [{"file": name, "status": status} for name, status in files],
                limit = self.limit,
                tiers = _FILE_TIERS,
            )
        else:
            print()
            print(NOTHING_PULLED)
            print()
        print(BRANCH_NOTE.format(branch=branch))
        print()

    def _pull(self, mode: str, branch: str, pull: Callable[[], object]) -> None:
        if self.debug:
            pull()
            return
        source, target = self.connections
        path = timers_path(self.root)
        # Keyed apart from the comparison of the same pair and mode: writing an
        # application's export costs nothing like comparing its fingerprint. And
        # apart per environment, since UAT and PROD may hold one schema (#923).
        key = pair_key(
            source.schema,
            target.schema,
            types        = ("PULL", mode),
            environments = (source.environment, target.environment),
        )
        # The bar draws its row from this thread and runs `pull` on a worker,
        # and the exporters fan out further, so this thread is the one kept.
        with _muted_except(threading.get_ident()):
            elapsed = TimedProgressBar().run(
                f"{target.environment}.{target.schema} -> {branch}",
                previous_seconds(path, key) or FALLBACK_TARGET_SECONDS,
                pull,
            )
        record_seconds(path, key, elapsed)


@dataclass(frozen=True)
class Pull:
    """A `-restore` run: the section it prints and the sides it writes between."""

    screen : PullScreen
    sides  : PullSides

    def tail(self, mode: str, work: Callable[[PullSides], object]) -> Callable[[], None]:
        """The section, run where the screen asks for it, with `work` as the pull."""
        return lambda: self.screen.run(mode, lambda: work(self.sides))

    @property
    def failed(self) -> bool:
        return self.screen.failed


class _Muted:
    """A stream that keeps one thread's writes and drops every other thread's.

    The kept thread is the one drawing the countdown row. It was the other way
    round until the live SANDBOX run, 2026-09-19: only the bar's worker was
    dropped, and the exporters, which fan out over threads of their own and
    write to stderr, split the row with a stray newline between two frames.
    """

    def __init__(self, stream: TextIO, kept: int) -> None:
        self._stream = stream
        self._kept = kept

    def write(self, text: str) -> int:
        if threading.get_ident() != self._kept:
            return len(text)
        return self._stream.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


@contextmanager
def _muted_except(kept: int) -> Iterator[None]:
    """`sys.stdout` and `sys.stderr` with every write but `kept`'s dropped.

    A failure still propagates, since only output is dropped, so it reaches
    `ERROR - DIFF FAILED:` like any other.
    """
    stdout, stderr = sys.stdout, sys.stderr
    # `setattr`, since the stand-in is a stream by what it answers rather than
    # by the class it is.
    setattr(sys, "stdout", _Muted(stdout, kept))  # noqa: B010
    setattr(sys, "stderr", _Muted(stderr, kept))  # noqa: B010
    try:
        yield
    finally:
        sys.stdout, sys.stderr = stdout, stderr


__all__ = [name for name in globals() if not name.startswith("_")]
