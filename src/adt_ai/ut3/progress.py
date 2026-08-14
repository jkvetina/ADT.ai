"""`RUNNING TESTS:`, the dotted bar a default `ut3` run watches.

The section it replaced printed a status row per test, which on Jan's
`ICT_OWNER` is 1156 rows: `SUMMARY:` landed past the end of the terminal's
scrollback and the report was correct and unreadable. `-dense` answered that
with 35 counted lines, and those counts turned out to be `SUMMARY:` again, one
section early. So the whole axis moved (Jan, 2026-08-13): the per-test listing
became `-verbose`, and what a default run watches is one line that moves.

**The bar is bumped by finished suites, never by a clock.** `#301` shipped an
elapsed-seconds ticker on the per-suite line and Jan rejected it the same day,
*"You will print the package name, when you have a result you will print the
rest. There is nothing in between."* The same reasoning binds here: a suite is
the unit of progress because it is the unit utPLSQL reports, so the dots advance
when a suite returns and at no other moment. Nothing runs in a background
thread, which is also why this module needs none of `export_apex/progress.py`'s
`ThreadPool` machinery, only its `DottedProgressBar` renderer, so the two bars
are the same row.

**The seconds field is what is left, not what has passed.** Before the first
suite returns, the only thing the command knows is what the last run of this
schema-and-variant cost (`timers.py`); from the first return onward this run
measures itself, and the two are blended by the completed fraction, early the
history knows more, late the sample *is* the run.

**The row is labelled and indented like an `export_apex` action row**, because
what Jan asked for is one bar, not two dialects of one. `#317` shipped it
headerless and with a blank line under the rule, and both were read straight off
the module it was meant to match (2026-08-13). The label counts *tests* while
the bar counts *suites*, deliberately: a suite is the only unit utPLSQL reports,
so it is the only thing that can move a bar honestly, while a test is the unit a
run is sized in and it is knowable before anything runs. Both figures come from
the one discovery the `UNIT TESTS SUITES:` table above prints, which is what
keeps them in step under `-name` with no second query to maintain.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from adt_ai.shared.progress import DottedProgressBar, print_adt_header

SECTION_TITLE = "RUNNING TESTS:"

# The two spaces every `export_apex` action row carries (`export_apex/actions.py`
# `ACTION_HEADERS`), and the word the suites table heads its count column with,
# so the label reads as that column's total rather than a second vocabulary.
ROW_INDENT = "  "
ROW_UNIT = "TESTS"


def row_header(tests: int) -> str:
    """``  1145 TESTS``, the label the bar crawls under for the whole run."""
    return f"{ROW_INDENT}{max(0, int(tests))} {ROW_UNIT}"


class SuiteProgressBar:
    """One redrawable row for the whole run, opened once and closed once."""

    def __init__(
        self,
        total: int,
        *,
        tests: int = 0,
        previous_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
        bar: DottedProgressBar | None = None,
    ) -> None:
        self._total = max(0, int(total))
        # Rendered once: the label is fixed for the run, and rebuilding it on
        # every redraw would invite a later edit to make it move.
        self._header = row_header(tests)
        self._previous = max(0.0, float(previous_seconds))
        self._clock = clock
        # The run began before this bar did, discovery has already happened by
        # the time the suites are known. The caller passes its own start so the
        # countdown and the figure it will store are measured from one origin;
        # a bar timing itself would drift from the history it seeds by one
        # discovery round trip every run.
        self._started_at = self._clock() if started_at is None else float(started_at)
        self._bar = bar or DottedProgressBar()
        self._done = 0

    @property
    def elapsed(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    def begin(self) -> None:
        """The section header, then the row at 0%, before the first suite runs.

        A bar that appeared only once something had finished would leave the
        longest silence of the run, the first suite, with nothing on screen.

        The row opens on the line directly under the dashed rule, which is where
        `export_apex` opens its first action row: `print_adt_header` already
        prints this section's own blank line above the title, so a second one
        here spaced the section against nothing.
        """
        print_adt_header(SECTION_TITLE)
        self._draw()

    def advance(self) -> None:
        """One more suite returned: redraw the same row in place."""
        self._done = min(self._total, self._done + 1)
        self._draw()

    def close(self) -> None:
        """100% with what the run actually cost, then end the section.

        The countdown was an estimate and the run is over, so the measurement
        replaces it, the shape `export_apex` closes on. The row itself carries
        no newline (it is rewritten with a carriage return), so the close owes
        one, plus the blank that leaves two empty lines before the next header,
        the spacing every ADT section is separated by.

        It carries the same label every earlier draw did: `\\r` returns the
        cursor and clears nothing, so a closing row that dropped the header
        would print over the label's own characters and end short of the row it
        replaced, leaving that row's tail on screen behind it.
        """
        self._bar.print_line(self._header, 100, int(self.elapsed), close=True)
        print()

    def _draw(self) -> None:
        self._bar.print_line(self._header, self._percent(), self._remaining())

    def _percent(self) -> int:
        """Finished suites as a percentage, held below 100 until the run ends.

        `199/200` rounds to 100 and would claim a finished run mid-flight, the
        same cap `export_apex`'s bar applies, for the same reason: 100% is the
        close, and only the close may print it.
        """
        if not self._total:
            return 0
        percent = int(self._done / self._total * 100 + 0.5)
        return percent if self._done >= self._total else min(percent, 99)

    def _remaining(self) -> int:
        return max(0, int(self._target() - self.elapsed + 0.5))

    def _target(self) -> float:
        """This run's projected total: history, this run's own rate, or a blend.

        With nothing finished there is no sample, so the stored figure is the
        whole estimate (and `0.0` when there is none, the row then reads
        `0:00:00` until the first suite returns, which is honest: the command
        genuinely does not know yet).

        Once suites have returned, `elapsed / done * total` projects the rest at
        the rate observed so far. Suites are uneven, so that projection is a poor
        sample early and the true answer late, weighting it by the completed
        fraction collapses to the right end at both ends with no special case.
        """
        if self._done == 0 or not self._total:
            return self._previous
        live = self.elapsed / self._done * self._total
        if self._previous <= 0:
            return live
        weight = self._done / self._total
        return self._previous * (1 - weight) + live * weight


__all__ = [name for name in globals() if not name.startswith("__")]
