from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from multiprocessing.pool import ThreadPool
from typing import Any, Protocol

from adt_ai.shared.progress import DottedProgressBar

# Assumed action duration when no timing history exists yet: keeps the progress
# bar crawling instead of jumping to 99% on the first-ever run of an action.
FALLBACK_TARGET_SECONDS = 999.0


class ApexProgressReporter(Protocol):
    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], None],
        app_id: int | None = None,
    ) -> float:
        ...


class ConsoleApexProgressReporter:
    line_width = 78

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._progress = DottedProgressBar(line_width=self.line_width)

    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], None],
        app_id: int | None = None,
    ) -> float:
        # `app_id` is the compact bar's business. Here the application already
        # owns the block this row sits in (`APP <id>/<alias>, EXPORTING:`), so
        # repeating it on the row would print the same fact twice (`#372`).
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

def segment_budget(timers: Mapping[Any, Any], planned: list[tuple[int, str]]) -> float:
    """What this segment is expected to cost, in seconds.

    The sum of every planned pair's stored time, which is what makes the bar
    **time-weighted rather than item-counted**: a `FULL APP EXPORT` and a
    `REST SERVICES` slice differ by an order of magnitude, so a bar advanced one
    step per action would sit at 50% with 90% of the work still to come. A pair
    with no history falls back rather than counting as free, for the same reason
    `FALLBACK_TARGET_SECONDS` exists on the per-action bar.
    """
    return sum(
        _timer_value(timers, app_id, action) or FALLBACK_TARGET_SECONDS
        for app_id, action in planned
    )


class CompactApexProgressReporter:
    """One row for a whole schema segment, labelled with the slice in flight.

    Drop-in for `ConsoleApexProgressReporter`: same `run(header, target_seconds,
    operation, app_id)` protocol, so `actions.py` hands it each pair's stored
    estimate with no change at the call sites. What differs is that the row is
    opened once for the segment instead of once per action.

    **The label names what is running right now**, `APP 133 | SPLIT COMPONENTS`,
    built from the `ACTION_HEADERS` string the default screen already prints, so
    the mode mints no console string of its own and `console_surface.txt` is
    unchanged by it. `#376` shipped this row blank on the argument that the
    header above already named the work; a header names the segment, and a
    segment that runs eight slices across two applications leaves the reader
    watching dots with no idea which one is slow. Jan, 2026-08-16: *"we have 2
    progress bars which dont start with a text ... We need to add some text there
    so it does not look like shit"*.

    A schema-level slice (`-rest`, `-files_ws`, timer slot `0`) carries no `APP`
    prefix, because it belongs to no application: the missing prefix is what says
    so, which is cheaper than a second word for the same idea.

    **The dot track is one constant for the segment** (`segment_dot_capacity`),
    not sized per label, or a percentage would draw a different number of dots
    depending on which slice happened to be running.

    **The seconds field is what is left, not what has passed**, the shape `ut`
    and the per-action bar both close on, and it opens on real history rather
    than on nothing, which is the whole reason Jan asked for apex.db here.
    `export_db` seeds its own bar from stored seconds per object TYPE (`#377`);
    the stores stay separate because the units are. An APEX slice has no
    comparable sub-unit to average over, so the (app, action) pair is itself the
    thing worth timing, which is exactly what `apex.db` already recorded.
    """

    line_width = 78

    def __init__(
        self,
        total_seconds: float,
        interval: float = 1.0,
        clock: Callable[[], float] | None = None,
        bar: DottedProgressBar | None = None,
        dot_capacity: int | None = None,
    ) -> None:
        self._total = total_seconds if total_seconds > 0 else FALLBACK_TARGET_SECONDS
        self.interval = interval
        self._clock = clock or time.monotonic
        self._progress = bar or DottedProgressBar(
            line_width    = self.line_width,
            dot_capacity  = dot_capacity,
            single_row    = True,
        )
        # The slice in flight, or `ROW_HEADER` before the first one opens and
        # after the last one closes.
        self._label = ROW_HEADER
        # Estimated seconds of the pairs that have FINISHED. The in-flight pair
        # is interpolated on top of this, so the row keeps moving inside a slice
        # instead of jumping once per action.
        self._done = 0.0
        self._started_at = self._clock()

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def begin(self) -> None:
        """The row at its opening estimate, before the first action starts.

        Drawn before any work, for the reason `#360` established on the
        per-action bar: `result.ready()` is checked first, so a fast action drew
        no frame at all and its export ran with the header as the newest thing
        on screen.
        """
        self._started_at = self._clock()
        self._draw(0.0, 0.0)

    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], None],
        app_id: int | None = None,
    ) -> float:
        """Run one slice, redrawing the segment row while it blocks."""
        self._label = segment_row_label(app_id, header)
        pair_started_at = self._clock()
        pair_target = target_seconds if target_seconds > 0 else FALLBACK_TARGET_SECONDS
        self._draw(pair_started_at, pair_target)
        with ThreadPool(processes=1) as pool:
            result = pool.apply_async(operation)
            while not result.ready():
                time.sleep(self.interval)
                self._draw(pair_started_at, pair_target)
            try:
                result.get()
            except BaseException:
                # The visible failure has to sit on the row being worked, and the
                # row has to end, or the error banner below it loses its blank
                # line to the unterminated progress write (ADT #232).
                self._progress.print_failed(self._label)
                raise
        elapsed = self._clock() - pair_started_at
        # The pair is over: its ESTIMATE, not its measurement, is what the bar
        # banks. The budget is the sum of estimates, so banking the measurement
        # would make the parts stop summing to the whole and a fast run would
        # close short of 100%.
        self._done += pair_target
        self._draw(self._clock(), 0.0)
        return elapsed

    def close(self) -> None:
        """100% with what the segment actually cost, and end the row.

        The countdown was an estimate and the export is over, so the measurement
        replaces it.
        """
        self._progress.print_line(self._label, 100, int(self.elapsed), close=True)

    def _draw(self, pair_started_at: float, pair_target: float) -> None:
        self._progress.print_line(self._label, self._percent(pair_started_at, pair_target),
                                  self._remaining(pair_started_at, pair_target))

    def _visible(self, pair_started_at: float, pair_target: float) -> float:
        """How far along the segment reads right now, in budget seconds.

        The estimate carries the row while the run keeps up with it, and the
        wall clock takes over when it does not — the same blend the per-action
        bar uses (`min(max(progress, elapsed), target)`), lifted from one action
        to the whole segment. Without the clock floor, a run twice as slow as
        its history would freeze mid-slice with a countdown that never moved.
        """
        pair_elapsed = max(0.0, self._clock() - pair_started_at)
        progressed = self._done + min(pair_elapsed, pair_target)
        return min(max(progressed, self.elapsed), self._total)

    def _percent(self, pair_started_at: float, pair_target: float) -> int:
        """Held below 100 until the close, which is the only thing that prints it.

        `99.6/100` rounds to 100 and would claim a finished export mid-flight.
        """
        visible = self._visible(pair_started_at, pair_target)
        return min(int(visible / self._total * 100 + 0.5), 99)

    def _remaining(self, pair_started_at: float, pair_target: float) -> int:
        return max(0, int(self._total - self._visible(pair_started_at, pair_target) + 0.5))


# What the row reads before the first slice opens it and after the last one
# closes: the segment's own header is the newest thing on screen at both moments,
# so there is nothing for the row to name yet.
ROW_HEADER = ""

# Between the app and the slice it is running. The same separator every other
# two-part row in the tool uses (`BRANCH | `, `COMMITS | `, the object rows).
APP_LABEL_SEPARATOR = " | "


def segment_row_label(app_id: int | None, action_header: str) -> str:
    """``APP 133 | SPLIT COMPONENTS``, the row's text while that slice runs.

    The app id alone, never `APP <id>/<alias>` as the section header spells it:
    an alias is unbounded, and since the whole segment sizes its dot track to the
    widest label, one long alias would squeeze the bar down to nothing on every
    row of the run.

    App `0` is the workspace timer slot, which is how `-rest` and `-files_ws` are
    already recorded, so a falsy id means schema-level work and the label is the
    action alone. The absent prefix is the signal; a `SCHEMA | ` prefix would say
    what the header two lines above already says (`#372`).
    """
    if not app_id:
        return action_header
    return f"APP {app_id}{APP_LABEL_SEPARATOR}{action_header}"


def _timer_value(timers: Mapping[Any, Any], app_id: int, action: str) -> float:
    app_timers = timers.get(app_id) or timers.get(str(app_id)) or {}
    if not isinstance(app_timers, Mapping):
        return 0.0
    value = app_timers.get(action) or 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

def _update_timer(timers: dict[Any, Any], app_id: int, action: str, elapsed: float) -> None:
    key: Any = app_id if app_id in timers or str(app_id) not in timers else str(app_id)
    app_timers = timers.get(key)
    if not isinstance(app_timers, dict):
        app_timers = {}
        timers[key] = app_timers
    previous = _timer_value(timers, app_id, action)
    timer = (elapsed + previous) / 2 if previous > 0 else elapsed
    app_timers[action] = round(timer, 2)
