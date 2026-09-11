"""The dotted bar driven over one blocking call, in one place (`#763`).

`DottedProgressBar` draws a row; something has to decide when to redraw it while
work that reports no progress of its own is running. That decider lived only in
`export_apex/progress.py`, so a command whose wait is a single opaque call had
nowhere to reuse it and `diff` shipped with a naked 37-second wait under a closed
connection block instead.

The console contract's rule is that a second module drawing this row itself is
the anti-pattern, which cuts both ways: the fix for the second caller is to move
the driver here, not to write another loop beside it.

**The countdown's target comes from the caller's own recorded history.** Nothing
inside a SQLcl process or an APEX export reports its own progress, so the figure
a bar counts down through is what the same job cost last time, folded through a
rolling average and stored beside the project (`apex_timers.yaml`,
`ut_timers.yaml`, `diff_timers.yaml`). A first-ever run has no figure and falls
back to `FALLBACK_TARGET_SECONDS`, which keeps the bar crawling instead of
jumping to 99%. The bar holds at 99 until the call actually returns and prints
the true elapsed time when it closes, so a wrong target makes the row early or
late and never makes it lie about being finished.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from multiprocessing.pool import ThreadPool

from adt_ai.shared.progress import DottedProgressBar

# Assumed duration when no timing history exists yet: keeps the progress bar
# crawling instead of jumping to 99% on the first-ever run of an action.
FALLBACK_TARGET_SECONDS = 999.0


class TimedProgressBar:
    """Run one blocking call under a crawling row, and time it."""

    line_width = 78

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._progress = DottedProgressBar(line_width=self.line_width)

    def run(
        self,
        header: str,
        target_seconds: float,
        # `object`, not `None`: the driver times the call and discards whatever
        # comes back, so an action that returns its rows is as valid here as one
        # that returns nothing.
        operation: Callable[[], object],
    ) -> float:
        started_at = time.monotonic()
        progress = 0.0
        # The 0% frame is drawn BEFORE the worker starts, not by the first turn
        # of the loop below. `result.ready()` is checked first, so an action that
        # finished quickly drew no frame at all and its whole export ran with the
        # previous row as the newest thing on screen; even a slow one raced its
        # own first frame. Label first, then the blocking call (`#360`).
        self._draw(header, progress, target_seconds, started_at)
        with ThreadPool(processes=1) as pool:
            result = pool.apply_async(operation)
            while not result.ready():
                progress = self._print_progress(header, progress, target_seconds, started_at)
            try:
                result.get()
            except BaseException:
                # The visible failure has to sit on the row being worked, and
                # the row has to end, or the error banner below it loses its
                # blank line to the unterminated progress write (ADT #232).
                self._print_failed(header)
                raise
        elapsed = time.monotonic() - started_at
        self._print_done(header, elapsed)
        return elapsed

    def _print_progress(
        self,
        header: str,
        progress: float,
        target_seconds: float,
        started_at: float,
    ) -> float:
        next_progress = self._draw(header, progress, target_seconds, started_at)
        time.sleep(self.interval)
        return next_progress

    def _draw(
        self,
        header: str,
        progress: float,
        target_seconds: float,
        started_at: float,
    ) -> float:
        """One frame, with no sleep, so the first one can precede the work."""
        target = target_seconds if target_seconds > 0 else FALLBACK_TARGET_SECONDS
        elapsed = time.monotonic() - started_at
        visible_progress = min(max(progress, elapsed), target)
        percent = min(int((visible_progress / target * 100) + 0.5), 99)
        remaining = max(0, int((target - visible_progress) + 0.5))
        self._print_line(header, percent, remaining)
        return min(max(progress + self.interval, elapsed), target)

    def _print_done(self, header: str, elapsed: float) -> None:
        self._print_line(header, 100, int(elapsed), close=True)

    def _print_failed(self, header: str) -> None:
        self._progress.print_failed(header)

    def _print_line(
        self,
        header: str,
        percent: int,
        seconds: int,
        close: bool = False,
    ) -> None:
        self._progress.print_line(header, percent, seconds, close=close)

    def _line_text(self, header: str, percent: int, seconds: int) -> str:
        return self._progress.line_text(header, percent, seconds)
