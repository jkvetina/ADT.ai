"""`RUNNING TESTS:`, the dotted bar a default `ut` run watches.

Under `-name` the heading names the patterns, `RUNNING TESTS FOR ICT_INT%:`, so
the one line on screen while the suites run says which part of the schema is
running. See `section_title`.

The section it replaced printed a status row per test, which on Jan's
`ICT_OWNER` is 1156 rows: `SUMMARY PER SUITE:` landed past the end of the terminal's
scrollback and the report was correct and unreadable. `-dense` answered that
with 35 counted lines, and those counts turned out to be `SUMMARY PER SUITE:` again, one
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
the one discovery `UNIT TESTS SUITES:` tabulates under `-verbose`, which is what
keeps them in step under `-name` with no second query to maintain. On a default
run the label is the only place that count appears, which is card `#348`'s whole
argument for taking the table out of this mode.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from adt_ai.shared.progress import ROW_INDENT as _ROW_INDENT
from adt_ai.shared.progress import DottedProgressBar, print_adt_header

SECTION_TITLE = "RUNNING TESTS:"

# The word the suites table heads its count column with, so the label reads as
# that column's total rather than as a second vocabulary. Re-exported from the
# shared module rather than spelled again here: `#380` made the renderer prepend
# the indent, and a second literal is how the two drift apart.
ROW_INDENT = _ROW_INDENT
ROW_UNIT = "TESTS"


def section_title(names: tuple[str, ...] = ()) -> str:
    """`RUNNING TESTS:`, or `RUNNING TESTS FOR <PATTERNS>:` under `-name`.

    **The run is the section that owes the filter.** It is the first thing on
    screen that `-name` changed, and it stays there for as long as the suites
    take, so a bar crawling over part of a schema says which part in its own
    heading rather than leaving the reader to remember the command they typed.
    The two tables below then carry no filter of their own: they say what they
    group, and this line has already said what the run covered.

    Upper-cased because every other word in an ADT section header is, and the
    patterns are Oracle identifiers-with-wildcards where case carries no meaning:
    `-name ict_int%` and `-name ICT_INT%` select the same suites, so they must
    not print two different headings.

    `-name` is repeatable and multi-value, so the heading joins what was passed
    rather than naming the first pattern and silently dropping the rest.
    """
    if not names:
        return SECTION_TITLE
    return f"RUNNING TESTS FOR {', '.join(name.upper() for name in names)}:"


def print_section_header(names: tuple[str, ...] = ()) -> None:
    """The run's heading, printed before discovery rather than after it.

    It is built from `-name` alone, so nothing the dictionary returns is needed
    to write it, while the row underneath needs the suite and test counts only
    discovery can supply. Splitting the two there is what puts the discovery
    wait under a heading instead of under the finished connection block, and it
    costs no string: this is the line the bar used to print itself, one read
    earlier (`#379`).

    A run that matches nothing therefore prints the heading and no row. That is
    the honest shape rather than a regression: the command did go looking, under
    exactly this filter, and the empty `SUMMARY PER SUITE:` below says what it
    found.
    """
    print_adt_header(section_title(names))


def row_header(tests: int) -> str:
    """``1145 TESTS``, the label the bar crawls under for the whole run.

    The two-space margin is not in here: `row_left_margin` prepends it to every
    labelled row, so the printed line is unchanged and the indent has one owner
    (`#380`).
    """
    return f"{max(0, int(tests))} {ROW_UNIT}"


class SuiteProgressBar:
    """One redrawable row for the whole run, opened once and closed once."""

    def __init__(
        self,
        total: int,
        *,
        tests: int = 0,
        names: tuple[str, ...] = (),
        previous_seconds: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
        bar: DottedProgressBar | None = None,
    ) -> None:
        self._total = max(0, int(total))
        # Rendered once: the label is fixed for the run, and rebuilding it on
        # every redraw would invite a later edit to make it move. `names` is
        # still taken, and still only for the heading, which `print_section_header`
        # now prints ahead of discovery, so the bar draws the row and no more.
        self._header = row_header(tests)
        self._names = tuple(names)
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
        """The row at 0%, before the first suite runs.

        A bar that appeared only once something had finished would leave the
        longest silence of the run, the first suite, with nothing on screen.

        The heading above it belongs to the section rather than to the row, and
        `print_section_header` printed it one read earlier so that discovery had
        something to block under (`#379`). The row opens on the line directly
        under the dashed rule, which is where `export_apex` opens its first
        action row.
        """
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
