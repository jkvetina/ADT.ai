"""The ADT console table: one renderer, shared by every module that prints one.

Split out of `export_db/render.py` at the 20 KB per-file context budget, along
the seam that module always had. Nothing here knows what `export_db` is: `ut`,
`recompile`, `patch` and `flow` all render through these helpers, and they lived
under `export_db` only because that is the command that needed a table first.
`export_db/render.py` re-exports the whole surface, so every existing import
path still resolves.

Two ways to draw one. `print_adt_table` renders a finished table in one call and
is what almost every caller wants. `open_adt_table` prints the header, the
separator and whatever rows are known, and leaves the table OPEN: the blank line
underneath is what ends a section and retires its header's claim on the screen
(`cli/constants._StdoutTracker`), so a table still open both holds the reader's
eye and announces the read running under it. `close_adt_table` ends it. That is
how `export_db` puts its `GRANT` row up only once the privilege reads have said
whether one is owed (`#437`).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from adt_ai.shared.progress import DROPBOX_PATH_RE

ADT_TABLE_INDENT = "  "
ADT_TABLE_GUTTER = "   "

def adt_table_line_width(widths: Sequence[int]) -> int:
    """Rendered width of a row whose columns are ``widths`` wide.

    The trailing gutter is excluded because ``row_line`` strips it. A caller
    that has to *budget* a column, fit the whole line inside 78 characters and
    spend whatever is left on the last one, needs this geometry, and copying
    the indent and gutter to the call site is how the copy drifts from the
    renderer (ADT #269). Pass ``0`` for the column being sized.
    """
    if not widths:
        return 0
    return len(ADT_TABLE_INDENT) + sum(widths) + len(ADT_TABLE_GUTTER) * (len(widths) - 1)

def _adt_cell(value: object, width: int, numeric: bool) -> str:
    # coalesce only None to "", a legitimate falsy value such as the int 0
    # (e.g. a sub-second duration) must still render. ``str(value or "")``
    # disagreed with the width/numeric detection and silently dropped the cell.
    cell = "" if value is None else str(value)
    text = DROPBOX_PATH_RE.sub("Dropbox/", cell)
    align = ">" if numeric else "<"
    return f"{text:{align}{width}}{ADT_TABLE_GUTTER}"

@dataclass(frozen=True)
class _AdtTableLayout:
    """Pre-computed column geometry, shared by the batch and streaming renders.

    Holding the widths/alignment lets a single row be emitted in pieces, the
    object name first, the rest after an action runs, that rejoin byte-for-byte
    with the whole-row render. ``cells_segment`` carries the two-space table
    indent only on the leading segment (``start == 0``).
    """

    columns: tuple[str, ...]
    widths: tuple[int, ...]
    numeric: tuple[bool, ...]

    def cells_segment(self, values: Sequence[object], start: int, end: int) -> str:
        line = ADT_TABLE_INDENT if start == 0 else ""
        for index in range(start, end):
            line += _adt_cell(values[index], self.widths[index], self.numeric[index])
        return line

    def row_line(self, values: Sequence[object]) -> str:
        # Stripped: every cell is padded to its column width and carries a
        # three-space gutter, the last column included, so an unstripped row ran
        # three characters (nine on the header) past its visible content. On an
        # 80-column terminal that invisible tail wrapped and printed as a blank
        # line under every row, the table read as perfectly aligned one column
        # wider and shredded one column narrower (ADT #237). Trailing padding
        # aligns nothing: a cell is placed by what sits to its left.
        return self.cells_segment(values, 0, len(self.columns)).rstrip()

    def header_line(self) -> str:
        return self.row_line([column.upper().replace("_", " ") for column in self.columns])

    def separator_line(self) -> str:
        return self.row_line(["-" * width for width in self.widths])

def _compute_adt_layout(
    rows: list[dict[str, object]],
    columns: Sequence[str],
    min_widths: Mapping[str, int],
    numeric_columns: Sequence[str] = (),
) -> _AdtTableLayout:
    columns = list(columns)
    widths = [
        max(
            len(column),
            min_widths.get(column, 0),
            *(len(DROPBOX_PATH_RE.sub("Dropbox/", str(row.get(column, "")))) for row in rows),
        )
        for column in columns
    ]
    # Detection reads the cells, so a column of quantities that carry a unit,
    # `75%`, `1.2s`, sniffs as text and prints left-aligned, which is exactly
    # where a reader most wants the digits to line up. ``numeric_columns`` is the
    # caller saying what the column *is*, and it wins over what the cells look
    # like; formatting the value to hide its unit would be the alternative, and
    # that trades a real alignment problem for an unreadable number.
    numeric = [
        column in numeric_columns
        or (
            bool(rows)
            and all(
                str(row.get(column, "")).isnumeric() or row.get(column, "") in {None, ""}
                for row in rows
            )
        )
        for column in columns
    ]
    return _AdtTableLayout(tuple(columns), tuple(widths), tuple(numeric))

def open_adt_table(
    rows: list[dict[str, object]],
    min_widths: Mapping[str, int] | None = None,
    columns: Sequence[str] | None = None,
    leading_blank: bool = True,
    numeric: Sequence[str] | None = None,
) -> _AdtTableLayout | None:
    """Header, separator and ``rows``, with the table left OPEN.

    Returns the layout those rows go through, or ``None`` when there is nothing
    to open at all, which is the caller's signal that no table is on screen.

    ``sized_for`` lived here between `#437` and `#442`: rows that might still be
    appended once the work running under an open table answered, measured with
    the rest so a late row could not widen a printed column. `#442` moved the
    whole overview table below the reads that decide its rows, which left the
    parameter with no caller anywhere in the repo, so it went with them.
    """
    # ``columns`` renders a requested section that found zero rows as a header
    # and separator over nothing. `#442` retired that reading for the
    # `export_db` overview, which now prints no table at all rather than an
    # empty one; the argument stays for a caller that wants the other answer.
    if not rows and not columns:
        return None
    min_widths = min_widths or {}
    columns = list(rows[0].keys()) if rows else list(columns)
    layout = _compute_adt_layout(rows, columns, min_widths, numeric or ())
    if leading_blank:
        print()
    print(layout.header_line())
    print(layout.separator_line())
    for row in rows:
        print(layout.row_line([row.get(column, "") for column in columns]))
    return layout

def close_adt_table() -> None:
    """The blank line that ends a table, and the flush that commits it."""
    print()
    _commit_stdout()

def print_adt_table(
    rows: list[dict[str, object]],
    min_widths: Mapping[str, int] | None = None,
    columns: Sequence[str] | None = None,
    leading_blank: bool = True,
    numeric: Sequence[str] | None = None,
) -> None:
    if open_adt_table(rows, min_widths, columns, leading_blank, numeric) is None:
        return
    close_adt_table()

def _commit_stdout() -> None:
    commit_pending = getattr(sys.stdout, "commit_pending", None)
    if callable(commit_pending):
        commit_pending()
        return
    sys.stdout.flush()

# `print_adt_pipes` stood here until ADT #506. It was the batch half of the
# `TYPE | NAME` object listing and the third spelling of a shape with only ever
# one meaning, so it moved to `shared/object_list.py` beside the other two. Two
# details went with it, both of them the fork rather than the feature: it wrote
# the type cell as `"  " + 18` where the streamed renderer wrote `20`, the same
# column reached two ways, and it left the name unpadded so a batch listing and
# a streamed one could not be compared byte for byte. Deleted rather than kept
# as a re-export, because `tests/contracts/shared_readers.txt` now fails on a
# second reader of that rule and a re-export is what a next author would copy.

__all__ = [name for name in globals() if not name.startswith("__")]
