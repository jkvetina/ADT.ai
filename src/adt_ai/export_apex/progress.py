from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from multiprocessing.pool import ThreadPool
from typing import Any, Protocol

from adt_ai.shared.progress import DottedProgressBar
from adt_ai.shared.timed_bar import FALLBACK_TARGET_SECONDS, TimedProgressBar

__all__ = [
    "FALLBACK_TARGET_SECONDS",
    "ApexProgressReporter",
    "CompactApexProgressReporter",
    "ConsoleApexProgressReporter",
    "row_key",
]


class ApexProgressReporter(Protocol):
    def run(
        self,
        header: str,
        target_seconds: float,
        # `object`, not `None`: the reporter times the call and discards whatever
        # comes back, so an action that returns its rows is as valid here as one
        # that returns nothing.
        operation: Callable[[], object],
        app_id: int | None = None,
    ) -> float:
        ...


class ConsoleApexProgressReporter(TimedProgressBar):
    """The shared timed bar, plus the signature `export_apex` reports through.

    The driver moved to `shared/timed_bar.py` for `#763`: `diff` blocks on one
    opaque SQLcl call and had no way to reuse this loop, so it shipped with no
    row at all. Nothing about the drawing changed, only where it lives.
    """

    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], object],
        app_id: int | None = None,
    ) -> float:
        # `app_id` is the compact bar's business. Here the application already
        # owns the block this row sits in (`APP <id>/<alias>, EXPORTING:`), so
        # repeating it on the row would print the same fact twice (`#372`).
        return super().run(header, target_seconds, operation)


class CompactApexProgressReporter:
    """One row per unit of a schema segment, labelled with the slice in flight.

    Drop-in for `ConsoleApexProgressReporter`: same `run(header, target_seconds,
    operation, app_id)` protocol, so `actions.py` hands it each pair's stored
    estimate with no change at the call sites. What differs is that a row covers
    a whole unit of work instead of one action.

    **The unit that owns a row is the thing a reader can name.** `#376` gave the
    whole segment one row, on the argument that the segment is what `-compact`
    compacts. It is not what a reader tracks: an application that finished left
    nothing on screen, and a run that also exported workspace artifacts closed on
    a single line reading whichever slice happened to be last. Jan, 2026-09-10:
    *"You should have dedicated line for REST and workspace files! And when you
    are done with the app, strip the `| ........`"* (`#772`). So the row breaks
    at each unit boundary (application to application, and application to
    schema-level slice), which is `row_key` below, and each row counts down its
    own budget rather than a share of the segment's.

    **The label names what is running right now**, `APP 133 | SPLIT COMPONENTS`,
    built from the `ACTION_HEADERS` string the default screen already prints, so
    the mode mints no console string of its own and `console_surface.txt` is
    unchanged by it. `#376` shipped this row blank on the argument that the
    header above already named the work; a header names the segment, and a
    segment that runs eight slices across two applications leaves the reader
    watching dots with no idea which one is slow. Jan, 2026-08-16: *"we have 2
    progress bars which dont start with a text ... We need to add some text there
    so it does not look like shit"*.

    **A row that CLOSES drops the slice half of that label** and reads `APP 133`.
    The suffix answers "what is running", and at 100% nothing is: the closing row
    is a result about the application, so `APP 133 | READABLE COMPONENTS` at 100%
    reads as that one format having finished rather than the app. A schema-level
    slice (`-rest`, `-files_ws`, timer slot `0`) carries no `APP` prefix in
    either state, because it belongs to no application: the missing prefix is
    what says so, which is cheaper than a second word for the same idea.

    **The dot track is sized against the slice being drawn**, so a full row
    reaches the right margin whatever it is labelled. `#380` fixed one track for
    the whole segment instead, sized to its widest label, and every shorter slice
    then stopped its leader short: Jan read a `REST SERVICES` row with twenty
    blank columns in front of its clock and asked for the margin back (`#767`).
    The track is the renderer's business now and this mode passes nothing.

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
        row_budgets: Mapping[tuple[str, Any], float],
        interval: float = 1.0,
        clock: Callable[[], float] | None = None,
        bar: DottedProgressBar | None = None,
    ) -> None:
        # Insertion-ordered, so the first key is the first row the segment opens
        # and `begin` can draw it before any work starts.
        self._budgets = dict(row_budgets)
        self.interval = interval
        self._clock = clock or time.monotonic
        self._progress = bar or DottedProgressBar(
            line_width    = self.line_width,
            single_row    = True,
        )
        # The slice in flight, or `ROW_HEADER` before the first one opens it.
        self._label = ROW_HEADER
        self._open_key: tuple[str, Any] | None = None
        # Whether a slice has actually run on the open row. A row that never ran
        # one is not closed at 100%: it would print a result for no work.
        self._row_ran = False
        # Estimated seconds of the pairs that have FINISHED **on this row**. The
        # in-flight pair is interpolated on top of it, so the row keeps moving
        # inside a slice instead of jumping once per action.
        self._done = 0.0
        self._row_started_at = self._clock()

    @property
    def row_elapsed(self) -> float:
        """What the row currently open has cost so far."""
        return max(0.0, self._clock() - self._row_started_at)

    def begin(self) -> None:
        """The first row at its opening estimate, before the first action starts.

        Drawn before any work, for the reason `#360` established on the
        per-action bar: `result.ready()` is checked first, so a fast action drew
        no frame at all and its export ran with the header as the newest thing
        on screen.
        """
        self._open_row(next(iter(self._budgets), None))
        self._draw(0.0, 0.0)

    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], object],
        app_id: int | None = None,
    ) -> float:
        """Run one slice, redrawing its row while it blocks."""
        key = row_key(app_id, header)
        if key != self._open_key:
            self.close()
            self._open_row(key)
        self._row_ran = True
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
                # line to the unterminated progress write (ADT #232). The label
                # keeps its slice half here: what failed is that one slice.
                self._progress.print_failed(self._label)
                self._open_key = None
                self._row_ran = False
                raise
        elapsed = self._clock() - pair_started_at
        # The pair is over: its ESTIMATE, not its measurement, is what the row
        # banks. The budget is the sum of estimates, so banking the measurement
        # would make the parts stop summing to the whole and a fast run would
        # close short of 100%.
        self._done += pair_target
        self._draw(self._clock(), 0.0)
        return elapsed

    def close(self) -> None:
        """100% with what the open row actually cost, and end the line.

        The countdown was an estimate and that unit's work is over, so the
        measurement replaces it. A no-op on a row nothing ran on, which is the
        opening frame's row when the first slice belongs to a different unit and
        the segment's own tail once the last row has closed itself.
        """
        if not self._row_ran or self._open_key is None:
            return
        kind, unit = self._open_key
        # The unit, never the slice that ran last.
        label = f"APP {unit}" if kind == _APP_ROW else str(unit)
        self._progress.print_line(label, 100, int(self.row_elapsed), close=True)
        self._open_key = None
        self._row_ran = False

    def _open_row(self, key: tuple[str, Any] | None) -> None:
        self._open_key = key
        self._row_ran = False
        self._done = 0.0
        self._row_started_at = self._clock()

    @property
    def _total(self) -> float:
        budget = self._budgets.get(self._open_key) if self._open_key else None
        return budget if budget and budget > 0 else FALLBACK_TARGET_SECONDS

    def _draw(self, pair_started_at: float, pair_target: float) -> None:
        self._progress.print_line(self._label, self._percent(pair_started_at, pair_target),
                                  self._remaining(pair_started_at, pair_target))

    def _visible(self, pair_started_at: float, pair_target: float) -> float:
        """How far along the open row reads right now, in budget seconds.

        The estimate carries the row while the run keeps up with it, and the
        wall clock takes over when it does not, the same blend the per-action
        bar uses (`min(max(progress, elapsed), target)`), lifted from one action
        to the whole row. Without the clock floor, a run twice as slow as its
        history would freeze mid-slice with a countdown that never moved.
        """
        pair_elapsed = max(0.0, self._clock() - pair_started_at)
        progressed = self._done + min(pair_elapsed, pair_target)
        return min(max(progressed, self.row_elapsed), self._total)

    def _percent(self, pair_started_at: float, pair_target: float) -> int:
        """Held below 100 until the close, which is the only thing that prints it.

        `99.6/100` rounds to 100 and would claim a finished export mid-flight.
        """
        visible = self._visible(pair_started_at, pair_target)
        return min(int(visible / self._total * 100 + 0.5), 99)

    def _remaining(self, pair_started_at: float, pair_target: float) -> int:
        return max(0, int(self._total - self._visible(pair_started_at, pair_target) + 0.5))


# What the row reads before the first slice opens it: the segment's own header is
# the newest thing on screen at that moment, so there is nothing to name yet.
ROW_HEADER = ""

# Between the app and the slice it is running. The same separator every other
# two-part row in the tool uses (`BRANCH | `, `COMMITS | `, the object rows).
APP_LABEL_SEPARATOR = " | "

# The two kinds of unit a row can cover. An application is keyed by its id; a
# schema-level slice by the action header itself, because `-rest` and
# `-files_ws` are two separate rows and share timer slot `0`.
_APP_ROW = "app"
_SCHEMA_ROW = "schema"


def row_key(app_id: int | None, action_header: str) -> tuple[str, Any]:
    """Which ROW this slice belongs to, and therefore where the row breaks.

    App `0` is the workspace timer slot `-rest` and `-files_ws` are already
    recorded under, so a falsy id means schema-level work. Those two do not share
    a row: they are unrelated artifacts that happen to be keyed on the workspace,
    and Jan asked for a line each, so the action header is the key.
    """
    return (_APP_ROW, app_id) if app_id else (_SCHEMA_ROW, action_header)


def segment_row_label(app_id: int | None, action_header: str) -> str:
    """``APP 133 | SPLIT COMPONENTS``, the row's text while that slice runs.

    The app id alone, never `APP <id>/<alias>` as the section header spells it:
    an alias is unbounded, and a long one would squeeze the leader down to
    nothing for as long as that application is exporting.

    A schema-level slice is the action alone. The absent prefix is the signal; a
    `SCHEMA | ` prefix would say what the header two lines above already says
    (`#372`).
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
