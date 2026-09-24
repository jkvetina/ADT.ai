"""The dotted bar a `-compact` export watches, one row for a whole schema.

The default `export_db` screen prints a row per object, which is old-ADT parity
output and stays the default. On a real schema that is hundreds of rows, so the
`OBJECTS OVERVIEW:` table that opened the run leaves the terminal's scrollback
long before the export ends and the run is readable only while it is short.
`-compact` answers that the way `ut` already does: the overview, then one line
that moves.

**The row is labelled with the object type being pulled right now** (`TABLE`,
`MATERIALIZED VIEW`, `PACKAGE BODY`), so the label moves with the export
rather than restating the header. `#373` shipped it blank, arguing that
`EXPORTING 61 OBJECTS:` above already names the work and a `61 OBJECTS` label
would print the same number twice, the shape `#372` had just deleted from five
commands. That is true of a STATIC label, and it left the row saying nothing at
all for the length of a real export. Jan, 2026-08-16: *"we have 2 progress bars
which dont start with a text ... We need to add some text there so it does not
look like shit"*, and, picking this shape over a count and over a bare verb,
*"live label sounds cool"*.

**The label is the type as Oracle spells it, singular** (Jan, `#803`). `#383`
pluralised it on the ground that the row heads a batch, which put
`MATERIALIZED VIEWS` on the bar between an overview and a listing that both say
`MATERIALIZED VIEW`. The one spelling keys the per-type timer store, the `-type`
filter and every table the tool prints.

An object type is dictionary data rather than a minted string, so this mode still
adds nothing to `tests/contracts/console_surface.txt`. The object NAME is
deliberately not beside it: `MATERIALIZED VIEW | <30-char name>` leaves eleven
dots, which is a row that has stopped being a bar.

**The dot track is one constant for the whole segment**, sized from the widest
type this run will export, or the same percentage would draw a different number
of dots depending on which type was in flight. Jan, 2026-08-16: *"the dots should
always match available space to calculate 100%"*.

The two-space margin belongs to `shared/progress.row_left_margin`, not to the
label (`#378` for the headerless arm, `#380` for the labelled one), so nothing
here spells an indent of its own.

**The bar is bumped by exported objects, never by a clock.** An object is the
unit `export_db` reports and the unit the header counts, so the dots advance
when an object's DDL comes back and at no other moment.

**The seconds field is what is left, not what has passed**, the shape `ut` and
`export_apex` both close on, and since `#377` it is seeded from what the last
export of this schema cost (`export_db/timers.py`). Before the first object
returns the history is the whole estimate; from the first return onward this
run's own rate is blended in, weighted by the completed fraction, so early the
stored figure knows more and late the sample IS the run. With no history the row
opens on `0:00:00` and projects from the rate alone, which is what `#373`
shipped and what Jan measured as jumpy: two samples of an uneven population read
`0:10:12` on a 55 second run.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from adt_ai.shared.progress import DottedProgressBar

# What the row reads before the first object type is known and after the last one
# is written: the header is the newest thing on screen at both moments, so there
# is nothing for the row to name yet.
ROW_HEADER = ""

def object_type_label(object_type: str) -> str:
    """The Oracle type name, for the row that heads a batch of them.

    The same spelling keys `config/internal/recent.yaml`, answers `-type`, and
    names every row of the overview above the bar and the listing below it.

    **Singular, like every object type the tool prints** (Jan, `#803`). `#383`
    pluralised this label on the ground that the row heads a batch, which left
    `MATERIALIZED VIEWS` on the bar between two tables saying `MATERIALIZED VIEW`.
    """
    # Empty is `ROW_HEADER`, the frame that names no type at all.
    return str(object_type or "")


class ObjectProgressBar:
    """One redrawable row per schema segment, opened once and closed once."""

    def __init__(
        self,
        total: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
        bar: DottedProgressBar | None = None,
        previous_seconds: float = 0.0,
    ) -> None:
        self._total = max(0, int(total))
        self._clock = clock
        # The type currently being pulled, or ROW_HEADER before the first one.
        self._label = ROW_HEADER
        # What this schema's last export cost, priced for the objects THIS run
        # selected (`export_db/timers.estimate_seconds`). Zero on a first run, on
        # a root with no history, and on a run with no environment to key one by.
        self._previous = max(0.0, float(previous_seconds))
        # The segment began before this bar did: discovery, the overview and the
        # DBMS_METADATA setup all run first. The caller may pass its own origin
        # so the countdown measures the whole export rather than the part of it
        # that has rows.
        self._started_at = self._clock() if started_at is None else float(started_at)
        self._bar = bar or DottedProgressBar(single_row=True)
        self._done = 0

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def begin(self) -> None:
        """The row at 0%, before the first object's DDL is pulled.

        Drawn immediately under the dashed rule, where `export_apex` opens its
        first action row. A bar that appeared only once something had finished
        would leave the first pull, and the metadata setup ahead of it, with
        nothing on screen but a closed header.
        """
        self._draw()

    def start_object(self, object_type: str) -> None:
        """Name the type now being pulled, and repaint the row under it.

        Called before the DDL round trip, never after: the label exists to say
        what the terminal is waiting on, and a label printed once the wait is
        over explains a wait that has already ended (`#360`).

        Takes the dictionary spelling and prints it as it stands, singular like
        every other table in the tool (`#803`, taking back `#383`'s plural).
        """
        self._label = object_type_label(object_type) or ROW_HEADER
        self._draw()

    def advance(self) -> None:
        """One more object done: redraw the same row in place.

        A failed object counts as done too (`#917`): the export carries on past
        it, so the bar does, and the failure is listed under its own warning
        once the bar has closed.
        """
        self._done = min(self._total, self._done + 1)
        self._draw()

    def close(self) -> None:
        """100% with what the segment actually cost, then end the section.

        The countdown was an estimate and the export is over, so the measurement
        replaces it. The row itself carries no newline while it crawls, so the
        close owes one, plus the blank that separates this section from the
        `TIMER` footer under it.

        It closes this way with a failed object in it as well (`#917`): the
        elapsed time is the figure the reader watches for, and the objects that
        failed are listed under their own warning straight after.
        """
        self._bar.print_line(self._label, 100, int(self.elapsed), close=True)
        print()

    def fail(self) -> None:
        """Complete the crawling row with FAILED before the error reaches screen.

        Without it the error banner's own leading blank line is spent
        terminating the row and the banner lands flush against the bar (ADT
        `#232`).
        """
        self._bar.print_failed(self._label)

    def _draw(self) -> None:
        self._bar.print_line(self._label, self._percent(), self._remaining())

    def _percent(self) -> int:
        """Exported objects as a percentage, held below 100 until the last one.

        `199/200` rounds to 100 and would claim a finished export mid-flight;
        100% is the close, and only the close may print it.
        """
        if not self._total:
            return 0
        percent = int(self._done / self._total * 100 + 0.5)
        return percent if self._done >= self._total else min(percent, 99)

    def _remaining(self) -> int:
        return max(0, int(self._target() - self.elapsed + 0.5))

    def _target(self) -> float:
        """This segment's projected total: history, this run's rate, or a blend.

        With nothing exported there is no sample, so the stored figure is the
        whole estimate, and `0.0` when there is none: the row then reads
        `0:00:00` until the first object returns, which is honest, the command
        genuinely does not know yet.

        Once objects have returned, `elapsed / done * total` projects the rest at
        the rate observed so far. Objects are uneven, so that projection is a
        poor sample early and the true answer late; weighting it by the completed
        fraction collapses to the right end at both ends with no special case.
        This is `ut/progress.py::_target` and it is deliberately the same
        arithmetic, one bar, one estimator (`#377`).
        """
        if self._done == 0 or not self._total:
            return self._previous
        live = self.elapsed / self._done * self._total
        if self._previous <= 0:
            return live
        weight = self._done / self._total
        return self._previous * (1 - weight) + live * weight


__all__ = [name for name in globals() if not name.startswith("__")]
