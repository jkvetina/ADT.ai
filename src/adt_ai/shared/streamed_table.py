"""A table drawn as the work happens: header up front, each row in two halves.

The console SHAPE behind SOP §Console output contract's `progress-rows-stream-
label-first`: everything knowable before a blocking call is flushed first and
stops mid-line, the call runs, and its result completes that same line. On a
terminal the open row is additionally repainted in place, so a single long row
shows it is alive rather than sitting half-written.

`patch -deploy` grew all of this under `#273`/`#434`/`#444` and owned it alone.
`#678` is the card where `patch -drop` needed the identical mechanism, and the
`one-renderer-per-console-shape` rule says what happens then: the shape moves
here and both commands render through it, rather than the second command copying
the first. The tell that it was one shape and not two conventions is that the
hard parts already agreed, the two-half seam, the `\\r` repaint, the erase that
lets a short closing row cover a long paint, while nothing kept them agreeing.

What stays with the caller is what genuinely differs: its section header (a
reviewed console string, `tests/contracts/console_surface.txt`), its column
geometry, and whatever it computes for a repaint. This module holds no timer, no
counter and no domain word.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Sequence

from adt_ai.shared.tables import _AdtTableLayout, _commit_stdout

# ANSI erase-to-end-of-line, written only on the live branch, which `isatty()`
# already gates. It is what lets a closing row be SHORTER than the paint it
# covers without leaving that paint's tail behind, and it is why a redirected run
# keeps the exact bytes the batch render writes (ADT #444).
ERASE_TO_END_OF_LINE = "\033[K"


class StreamedTable:
    """One table, opened before the work and closed after it.

    `open_table` prints the column header and separator from a layout sized
    BEFORE any row exists, which is what stops a late row widening a column that
    is already on screen. `begin_row` prints the leading cells and stops
    mid-line; `end_row` completes that line. `repaint` is the live-only redraw
    of an open row, and it is safe to call from a ticker thread.

    **The live render is chosen by `isatty`, never by a flag**, so a redirected
    run, a CI job and a piped `adtai` keep printing exactly one line per row. A
    log carrying every intermediate paint is worse than the batch render.
    """

    def __init__(
        self,
        layout: _AdtTableLayout,
        *,
        split: int,
        live: bool | None = None,
    ) -> None:
        #: Where a row breaks: everything left of this is knowable BEFORE the
        #: blocking call, everything from here on is its result. A caller's
        #: named index rather than a literal, because it moves with the column
        #: order and the two halves have to keep meeting at the same seam or
        #: they stop rejoining byte-for-byte with the batch render.
        self.split = split
        self.layout = layout
        self.live = sys.stdout.isatty() if live is None else live
        self._row_open = False
        # Paints arrive from the caller and from whatever ticker it runs, so the
        # two are serialized: a half-written row interleaved with another is
        # unreadable and the carriage returns no longer line up (ADT #670).
        self._paint_lock = threading.Lock()

    @property
    def row_open(self) -> bool:
        """Is a row mid-line right now? Read without the lock on purpose.

        A ticker uses this to decide whether to bother computing a repaint; the
        decision that matters is re-made inside `repaint` under the lock, so a
        stale read here costs one wasted computation and never a stray paint.
        """
        return self._row_open

    def open_table(self, leading_blank: bool = True) -> None:
        """The column header and separator, mirroring `print_adt_table`'s opening.

        The section header above this is the caller's: it is a reviewed console
        string and this module has no business minting one.
        """
        if leading_blank:
            print()
        print(self.layout.header_line())
        print(self.layout.separator_line())
        _commit_stdout()

    def begin_row(self, values: Sequence[object]) -> None:
        """Open a row on everything known before the work.

        ``values`` is a full-width row whose trailing cells are whatever the
        caller wants an open row to claim; the quiet render prints only the
        cells left of `split`, so those trailing cells reach the screen solely
        on the live branch.
        """
        self._row_open = True
        if not self.live:
            print(
                self.layout.cells_segment(values, 0, self.split),
                end  = "",
                flush= True,
            )
            return
        self.repaint(values)

    def repaint(self, values: Sequence[object]) -> None:
        """Redraw the open row in place. A no-op unless a row is open and live.

        `_row_open` is read INSIDE the lock because `end_row` clears it inside
        the same one: a ticker that had already passed its own check would
        otherwise repaint over a row that has just printed its verdict, and the
        run would end on a status it had already retracted (ADT #670).
        """
        if not self.live:
            return
        with self._paint_lock:
            if not self._row_open:
                return
            print(
                "\r" + self.layout.cells_segment(
                    values, 0, len(self.layout.columns)
                ).rstrip(),
                end  = "",
                flush= True,
            )

    def end_row(self, values: Sequence[object]) -> None:
        """Complete the open row with its result, closing the line.

        A row that was never opened (a caller reporting an outcome for work that
        never started) owes its whole line here, so it gets one.
        """
        if not self._row_open:
            print(self.layout.row_line(values), flush=True)
            return
        if not self.live:
            self._row_open = False
            # rstrip only on the half that closes the line: the leading segment
            # is mid-line and must keep its gutter, or the two halves stop
            # rejoining byte-for-byte with the batch `row_line` (ADT #237).
            print(
                self.layout.cells_segment(
                    values, self.split, len(self.layout.columns)
                ).rstrip(),
                flush=True,
            )
            return
        # The whole row over the last paint, then ERASE TO END OF LINE (ADT
        # #444). A finished row can be shorter than the paint it covers, and a
        # bare `\r` rewrite would leave that paint's tail on screen. An erase
        # rather than trailing spaces, so the LOG never changes: this suffix
        # exists only on the branch `isatty()` already gated.
        #
        # Under `_paint_lock`, and closing the row inside it: a tick already past
        # its own wait still repaints, and a `print` is two writes, so the
        # closing row could otherwise be split down the middle and land as two
        # half-rows on one terminal line.
        with self._paint_lock:
            self._row_open = False
            print(
                "\r" + self.layout.row_line(values) + ERASE_TO_END_OF_LINE,
                flush=True,
            )

    def close_table(self) -> None:
        """The trailing blank `print_adt_table` emits, and the flush.

        The section closes the same way whichever render produced it, which is
        also what retires its header's claim on the screen
        (`cli/stream_tracker.py`).
        """
        print()
        _commit_stdout()


__all__ = ["ERASE_TO_END_OF_LINE", "StreamedTable"]
