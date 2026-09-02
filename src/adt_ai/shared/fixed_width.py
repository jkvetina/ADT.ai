"""The labelled fixed-width row: a label, a leader of dots, and a value.

Split out of ``progress.py`` when `#436` pushed that module past the repo's
20 KB per-file context budget, along the seam it already had. ``progress`` owns
the screen furniture a command prints, the headers, the banners and the crawling
dotted bar, and this owns the one row shape five modules stream their work
through.

**The grid is computed in exactly one place here**, :func:`fixed_width_row`, and
that is what the split is worth. It used to be computed in four: both helpers
below and both of :class:`FixedWidthProgressPrinter`'s closers each wrote
``"." * max(1, line_width - len(left) - len(right) - 2)``, which turns a
*negative* dot budget into a single dot and lets a long label overhang the grid
by however much it overran. The ``max`` reads as a guard and was the defect,
because nothing anywhere trimmed the label: Jan measured a `ut` test row at 83
columns on a 78-column grid, 2026-08-20.
"""

from __future__ import annotations

from collections.abc import Callable

from adt_ai.shared.progress import (
    ROW_INDENT,
    DottedProgressBar,
    commit_line,
    print_adt_header,
)

#: How short the leader run may get before the label yields instead.
#:
#: One dot is not a leader: it reads as the end of a sentence, and a reader
#: cannot tell a row that was trimmed from one that happened to stop there. The
#: dot run is what makes a column of values scannable at all, so it is a floor
#: the label gives way to, never the other way round.
LEADER_DOTS_MINIMUM = 2

#: What marks the piece of a label the grid could not hold.
#:
#: Three dots rather than a single character. It cannot be confused with the
#: leader run beside it: a leader is surrounded by spaces and this is surrounded
#: by the name's own characters.
LABEL_ELISION = "..."

#: What a streamed row assumes its value will need when nobody says.
#:
#: A streamed label reaches the terminal *before* the work that produces the
#: value runs, so the trim has to be decided against a reservation rather than
#: against the real text. Eleven is the widest thing
#: :func:`fixed_width_count_suffix` produces at the default count width
#: (``1 | 12002``), so the default never under-reserves for the callers that do
#: not care; one that wants the room back passes its own ``value_width``.
DEFAULT_VALUE_WIDTH = 11


def fit_label(label: str, available: int) -> str:
    """``label`` cut to ``available`` columns, keeping both of its ends.

    **A right-truncate hides the half a reader is scanning for.** A `ut` row is
    labelled ``<procedure>#<test name>``, and within one suite block every row
    shares the procedure, so cutting the tail leaves a column of rows that all
    read the same. Eliding the middle keeps the two ends that tell them apart.

    A label that already fits comes back untouched, which is what keeps this
    invisible on every row that never needed it.
    """
    if available <= 0:
        return ""
    if len(label) <= available:
        return label
    if available <= len(LABEL_ELISION):
        # No room to say anything was dropped, so say as much as fits and let
        # the caller's own width be the evidence.
        return label[:available]
    keep = available - len(LABEL_ELISION)
    head = keep - keep // 2
    tail = keep - head
    if not tail:
        return f"{label[:head]}{LABEL_ELISION}"
    return f"{label[:head]}{LABEL_ELISION}{label[-tail:]}"


def fixed_width_row(
    label: str,
    value: str,
    *,
    indent: str = ROW_INDENT,
    line_width: int = 78,
) -> str:
    """The one place a labelled fixed-width row is laid out.

    Both helpers below and both of :class:`FixedWidthProgressPrinter`'s closers
    come through here, so the grid cannot be computed four ways and disagree.
    That is what it did until `#436`: each site wrote
    ``"." * max(1, line_width - len(left) - len(right) - 2)``, which turns a
    *negative* dot budget into a single dot and lets the row overhang the grid
    by however much the label overran it. The `max` looked like a guard and was
    the defect: nothing anywhere trimmed the label. Jan measured it at 83
    columns on a 78-column grid, 2026-08-20.
    """
    room = line_width - len(indent) - len(value) - 2 - LEADER_DOTS_MINIMUM
    left = f"{indent}{fit_label(label, room)}"
    dots = "." * max(LEADER_DOTS_MINIMUM, line_width - len(left) - len(value) - 2)
    return f"{left} {dots} {value}"


def fixed_width_count_line(
    label: str,
    count: int,
    *,
    total: int | None = None,
    count_width: int = 1,
    indent: str = ROW_INDENT,
    line_width: int = 78,
) -> str:
    return fixed_width_row(
        label,
        fixed_width_count_suffix(count, total=total, count_width=count_width),
        indent     = indent,
        line_width = line_width,
    )


def fixed_width_status_line(
    label: str,
    status: str,
    *,
    indent: str = ROW_INDENT,
    line_width: int = 78,
) -> str:
    return fixed_width_row(label, status, indent=indent, line_width=line_width)


def fixed_width_count_suffix(
    count: int,
    *,
    total: int | None = None,
    count_width: int = 1,
) -> str:
    if total is None:
        return f"{count:>{count_width}}"
    return f"{count:>{count_width}} | {total:>7}"


class FixedWidthProgressPrinter:
    """Rows whose label is streamed before the work and completed after it.

    ``value_width`` is how much room the right-hand side is promised. It has to
    be declared up front rather than measured, because :meth:`begin` puts the
    label on the terminal before the call that produces the value has run, and
    bytes already on screen cannot be taken back to make room (`#436`). A caller
    that knows its own widest value passes it and gets the rest of the grid for
    the label; one that does not keeps :data:`DEFAULT_VALUE_WIDTH`.
    """

    def __init__(
        self,
        *,
        line_width: int = 78,
        indent: str = ROW_INDENT,
        value_width: int = DEFAULT_VALUE_WIDTH,
    ) -> None:
        self.line_width = line_width
        self.indent = indent
        self.value_width = value_width
        self._active_left: str | None = None

    def _left(self, label: str, indent: str | None = None) -> str:
        """The label on its margin, trimmed to what the reservation leaves it."""
        margin = self.indent if indent is None else indent
        room = self.line_width - len(margin) - self.value_width - 2 - LEADER_DOTS_MINIMUM
        return f"{margin}{fit_label(label, room)}"

    def bar(self) -> DottedProgressBar:
        """A crawling row on this console, for work with a count but no rows.

        The reporter is what a runner already holds to answer "is anything being
        printed", so it is also what answers "what do I crawl under". A reporter
        that prints nothing returns None instead, and the caller draws nothing
        (`#381`).
        """
        return DottedProgressBar(line_width=self.line_width)

    def begin(self, label: str, *, indent: str | None = None) -> None:
        self._active_left = self._left(label, indent)
        # No mark_announced() here, nor on the dotted bar, nor on a streamed
        # table half-row: all three stop mid-line, and an open line is what the
        # runtime reads as an announcement (cli/constants.py _StdoutTracker).
        print(self._active_left, end="", flush=True)

    def finish(
        self,
        label: str,
        count: int,
        *,
        total: int | None = None,
        count_width: int = 1,
        indent: str | None = None,
    ) -> None:
        left = self._active_left or self._left(label, indent)
        right = fixed_width_count_suffix(count, total=total, count_width=count_width)
        dots_len = self.line_width - len(left) - len(right) - 2
        dots = "." * max(LEADER_DOTS_MINIMUM, dots_len)
        print(f" {dots} {right}")
        # A row with its value on it is finished, so its newline goes out now
        # rather than waiting for whatever prints next to flush it (`#670`);
        # `DottedProgressBar` closes its own rows this way since `#323`.
        commit_line()
        self._active_left = None

    def fail(
        self,
        label: str,
        *,
        status: str = "FAILED",
        indent: str | None = None,
    ) -> None:
        self.status(label, status, indent=indent)

    def status(
        self,
        label: str,
        status: str,
        *,
        indent: str | None = None,
    ) -> None:
        """Complete the active row with an arbitrary status word, not a count.

        For blocking work with no natural count (a scan, a session setup
        call) that would otherwise leave the row's label sitting alone with
        no visible progress until the whole thing resolves.
        """
        left = self._active_left or self._left(label, indent)
        dots_len = self.line_width - len(left) - len(status) - 2
        dots = "." * max(LEADER_DOTS_MINIMUM, dots_len)
        print(f" {dots} {status}")
        commit_line()  # as in `finish` above, and `fail` comes through here
        self._active_left = None

    def line(self, text: str) -> None:
        # Deliberately NOT committed. This prints whatever it is handed, a
        # trailing blank included, and the shared `TIMER` footer normalizes
        # exactly those retractable blanks at the end of a run (`#269`). Only a
        # row carrying a VALUE is finished; a free line is still the caller's.
        print(text)


def open_section(title: str) -> FixedWidthProgressPrinter:
    """A section header, and the printer its rows will stream through.

    The pair every announced phase needs, so a caller cannot print the header
    and then forget the label under it, which is the half-fix `#360` was written
    to stop.
    """
    print_adt_header(title)
    return FixedWidthProgressPrinter()


def streamed[T](progress: FixedWidthProgressPrinter, label: str, read: Callable[[], T]) -> T:
    """Stream `label`, run `read`, close the row with how much came back.

    The whole shape of the console contract in one call, for the commonest case
    it applies to: a read that blocks, produces a countable result and has no
    rows of its own to stream. Shared rather than re-written per module because
    `#360` needed it in five of them, and a per-module copy is how the
    label-then-work order drifts out of one of them again.
    """
    progress.begin(label)
    try:
        result = read()
    except Exception:
        progress.fail(label)
        raise
    progress.finish(label, len(result) if hasattr(result, "__len__") else 1)
    return result

__all__ = [name for name in globals() if not name.startswith("_")]
