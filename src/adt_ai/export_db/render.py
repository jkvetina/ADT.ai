from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from adt_ai.export_db.grants import GRANT_OBJECT_TYPE
from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.export_db.progress import ObjectProgressBar, widest_row_label
from adt_ai.shared.dates import recent_since
from adt_ai.shared.progress import DROPBOX_PATH_RE, print_adt_header
from adt_ai.shared.recent_state import parse_timestamp


class ExportDbReporter:
    @property
    def reports_objects(self) -> bool:
        return True

    def begin_overview(
        self,
        names: list[str] | None = None,
        recent_days: int | float | None = None,
        authors: list[str] | None = None,
        changed_since: str | None = None,
        db_now: str | None = None,
    ) -> None:
        """The overview title, before the reads whose counts fill its table.

        Split from :meth:`overview` by `#372`: the title needs only what the
        request already said, so it goes up first and the schema's whole
        discovery runs under it. Printing both together left the diff-table
        sweep, the clock read and the object listing behind the connection
        block's closing blank.
        """

    def overview(
        self,
        schema: str,
        objects: list[DatabaseObject],
        names: list[str] | None = None,
        recent_days: int | float | None = None,
        authors: list[str] | None = None,
        changed_since: str | None = None,
        db_now: str | None = None,
        grants: bool = False,
    ) -> None:
        pass

    def recent_note(self, message: str) -> None:
        pass

    def diff_tables_dropped(self, tables: list[str]) -> None:
        pass

    def deleted_objects(self, schema: str, objects: list[DatabaseObject]) -> None:
        pass

    def start_export(
        self,
        schema: str,
        total: int,
        estimate: float = 0.0,
        widest_label: str = "",
        grants: bool = False,
    ) -> None:
        pass

    def export_object(
        self,
        database_object: DatabaseObject,
        duplicates: list[str] | None = None,
        changed_by: str | None = None,
    ) -> None:
        pass

    def finish_object(self, failed: bool = False) -> None:
        pass

    def start_grants(self, schema: str) -> None:
        pass

    def finish_type(self, schema: str, object_type: str) -> None:
        pass

    def finish_export(self, schema: str) -> None:
        pass

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

def print_adt_table(
    rows: list[dict[str, object]],
    min_widths: Mapping[str, int] | None = None,
    columns: Sequence[str] | None = None,
    leading_blank: bool = True,
    numeric: Sequence[str] | None = None,
) -> None:
    # ``columns`` makes a requested section render even with zero rows: the
    # header and separator still print so the user sees the feature ran (an
    # empty table reads as "looked, found nothing", not "silently did nothing").
    if not rows and not columns:
        return
    min_widths = min_widths or {}
    columns = list(rows[0].keys()) if rows else list(columns)
    layout = _compute_adt_layout(rows, columns, min_widths, numeric or ())
    if leading_blank:
        print()
    print(layout.header_line())
    print(layout.separator_line())
    for row in rows:
        print(layout.row_line([row.get(column, "") for column in columns]))
    print()
    _commit_stdout()

def _commit_stdout() -> None:
    commit_pending = getattr(sys.stdout, "commit_pending", None)
    if callable(commit_pending):
        commit_pending()
        return
    sys.stdout.flush()

def print_adt_pipes(rows: dict[str, list[str]]) -> None:
    for key in sorted(rows):
        for index, value in enumerate(rows[key]):
            label = key.upper() if index == 0 else ""
            print(f"  {label:>18} | {value}")
    print()

class ConsoleExportDbReporter(ExportDbReporter):
    def __init__(self, silent: bool = False, compact: bool = False) -> None:
        self._last_type_by_schema: dict[str, str] = {}
        self._silent = silent
        # `-silent` outranks `-compact`, the precedence `ut3` already carries
        # between `-silent` and `-verbose`: silent suppresses the very rows the
        # bar stands in for, so a run asking for both gets the quieter one and
        # no flag changes meaning depending on the flag beside it.
        self._compact = compact and not silent
        # An object row is left unterminated while its DDL is pulled; this is
        # what stops finish_object printing a stray newline under -silent, or
        # after a row nobody opened.
        self._row_open = False
        # The bar for the schema being exported, or None outside `-compact` and
        # between segments.
        self._bar: ObjectProgressBar | None = None

    @property
    def reports_objects(self) -> bool:
        # True under `-compact`: the runner only calls `export_object` when this
        # is set, and that call is where the bar learns an object has started.
        return not self._silent

    def begin_overview(
        self,
        names: list[str] | None = None,
        recent_days: int | float | None = None,
        authors: list[str] | None = None,
        changed_since: str | None = None,
        db_now: str | None = None,
    ) -> None:
        print_adt_header(
            _overview_header(
                names         = names,
                recent_days   = recent_days,
                authors       = authors,
                changed_since = changed_since,
                db_now        = db_now,
            )
        )

    def overview(
        self,
        schema: str,
        objects: list[DatabaseObject],
        names: list[str] | None = None,
        recent_days: int | float | None = None,
        authors: list[str] | None = None,
        changed_since: str | None = None,
        db_now: str | None = None,
        grants: bool = False,
    ) -> None:
        counts = Counter(database_object.object_type for database_object in objects)
        rows = [
            {"object_type": object_type, "count": counts[object_type]}
            for object_type in sorted(counts)
        ]
        if grants:
            # **A row, and deliberately no number** (`#382`): how many files the
            # four reads write is not knowable here (`grants_received` produces
            # one per owner) and they run after this table. Appended rather than
            # counted in, so these rows and `EXPORTING <n> OBJECTS:` below stay a
            # dictionary total.
            rows.append({"object_type": GRANT_OBJECT_TYPE, "count": ""})
        print_adt_table(rows)

    def recent_note(self, message: str) -> None:
        # The title is the header; the sentence explaining it is body text. The
        # whole note used to be the header, so a dashed rule ran the width of a
        # sentence and the line could not end on a colon (ADT #237).
        print_adt_header("NO PREVIOUS EXPORT RECORDED:")
        print(f"  {message}")

    def diff_tables_dropped(self, tables: list[str]) -> None:
        # Every dropped table is named. This is the one place `export_db` issues
        # a `DROP`, and a project that legitimately owns a `%$1` table would
        # otherwise lose it without a word on screen (ADT #356). Silent when the
        # schema was clean, which is almost every run.
        if not tables:
            return
        print_adt_header("DROPPING DIFF TABLES:")
        for table_name in tables:
            print(f"  - {table_name}")
        print()

    def deleted_objects(self, schema: str, objects: list[DatabaseObject]) -> None:
        if not objects:
            return
        grouped: dict[str, list[str]] = {}
        for database_object in sorted(objects, key=lambda item: (item.object_type, item.name)):
            grouped.setdefault(database_object.object_type, []).append(database_object.name)
        print_adt_header("DELETED OBJECTS:")
        print_adt_pipes(grouped)

    def start_export(
        self,
        schema: str,
        total: int,
        estimate: float = 0.0,
        widest_label: str = "",
        grants: bool = False,
    ) -> None:
        self._last_type_by_schema[schema] = ""
        # The count reads as part of the sentence rather than a parenthetical
        # after the colon: `EXPORTING OBJECTS: (61)` put the rule under the label
        # and left the number dangling past it (ADT #237, Jan's wording).
        print_adt_header(f"EXPORTING {total} OBJECTS:")
        if self._silent:
            return
        if self._compact:
            # Straight under the dashed rule, where `export_apex` opens its own
            # first action row, so the metadata setup and the first DDL pull run
            # with a moving row on screen instead of a closed header.
            # `estimate` is what the runner priced this run at from the stored
            # rates; the reporter never reads or writes that history itself, a
            # console class that touches the filesystem is the wrong seam.
            # `widest_label` sizes the dot track once for the whole segment, so a
            # percentage is the same number of dots whichever type is in flight;
            # `widest_row_label` folds in the GRANT row when one will be drawn.
            # **The bar counts the grants; the header does not** (`#382`). They
            # are one more unit of work, and leaving them out let the row read
            # 100% while they were still running, the `#379` defect exactly.
            self._bar = ObjectProgressBar(
                total + (1 if grants else 0),
                previous_seconds = estimate,
                widest_label     = widest_row_label(widest_label, grants),
            )
            self._bar.begin()
            return
        print()

    def export_object(
        self,
        database_object: DatabaseObject,
        duplicates: list[str] | None = None,
        changed_by: str | None = None,
    ) -> None:
        last_type = self._last_type_by_schema.get(database_object.schema, "")
        object_type = (
            database_object.object_type
            if database_object.object_type != last_type
            else ""
        )
        self._last_type_by_schema[database_object.schema] = database_object.object_type
        if self._compact and self._bar is not None:
            # The bar IS this row under `-compact`, and it names the type it is
            # about to wait on. Set before the DDL round trip, so the label
            # explains the wait rather than reporting it afterwards (`#360`).
            self._bar.start_object(database_object.object_type)
            return
        if self._silent or self._compact:
            # The per-object row is the thing `-compact` was asked to replace,
            # and `-silent` suppresses it outright.
            return
        # Every branch leaves its last row open, with no newline: the object's
        # DDL is pulled straight after this call, and that pull is the wait the
        # row exists to explain. `finish_object` closes the line once the DDL is
        # back, so the bytes are exactly what the completed row always printed
        # and only the moment the newline lands has moved (`#360`).
        self._row_open = True
        if not duplicates:
            if changed_by:
                # Same deliberately ragged shape as [DUPE] below: the object is in
                # the export because the requested author worked on it, but someone
                # else changed it last, and an aligned row would hide that.
                row = f"{object_type:>20} | {database_object.name} [{changed_by}]"
                print(row, end="", flush=True)
                return
            print(f"{object_type:>20} | {database_object.name:<54}", end="", flush=True)
            return
        # One row per stale clone, so the object's every location is visible and
        # the user can delete the wrong ones by hand. The name column is left
        # unpadded here: an over-long, deliberately ragged row is how a [DUPE]
        # stands out from the aligned object list around it.
        for index, location in enumerate(duplicates):
            label = object_type if index == 0 else ""
            row = f"{label:>20} | {database_object.name} | {location} [DUPE]"
            if index == len(duplicates) - 1:
                print(row, end="", flush=True)
            else:
                print(row)

    def finish_object(self, failed: bool = False) -> None:
        """Close the row the DDL pull was running under.

        Under `-compact` the row is the bar, so a successful pull bumps it and a
        failed one completes it with `FAILED` and hands the screen to the error
        banner. The bar is dropped on failure: nothing after this may redraw a
        row the error has already printed under.
        """
        if self._bar is not None:
            if failed:
                self._bar.fail()
                self._bar = None
            else:
                self._bar.advance()
            return
        if not self._row_open:
            return
        self._row_open = False
        print()

    def start_grants(self, schema: str) -> None:
        """Name `GRANTS` on the compact row, and print nothing in any other mode.

        The four grant reads run after the object loop, so under `-compact` this
        is what the reader watches while they block. Every other mode prints no
        row for them at all (Jan, 2026-08-16: *"dont list grants ... in non
        compact mode in the list of objects"*); the overview row above is what
        says the type is coming.
        """
        if self._bar is None:
            return
        self._bar.start_object(GRANT_OBJECT_TYPE)

    def finish_type(self, schema: str, object_type: str) -> None:
        if self._silent or self._compact:
            return
        print(f"{'':>20} |")

    def finish_export(self, schema: str) -> None:
        """Close this schema's bar at 100%; a no-op in every other mode."""
        if self._bar is None:
            return
        self._bar.close()
        self._bar = None

def _overview_header(
    names: list[str] | None,
    recent_days: int | float | None,
    authors: list[str] | None = None,
    changed_since: str | None = None,
    db_now: str | None = None,
) -> str:
    if changed_since is not None:
        # Watermark mode: the cutoff is a real stored instant from the database
        # clock, so it is shown verbatim rather than re-derived from a day count.
        # Reads as a sentence, what the cutoff *is* comes before the timestamp,
        # instead of trailing it as a parenthetical gloss (ADT #237).
        show_header = f"CHANGED SINCE LAST EXPORT AT {changed_since}"
    elif recent_days is None:
        show_header = "OVERVIEW"
    else:
        window_start = recent_since(recent_days, now=parse_timestamp(db_now))
        show_header = f"CHANGED SINCE {window_start}"
    show_filter = " ".join(names or ["%"])
    show_filter = f" {show_filter} ".replace(" % ", " ").strip()
    if show_filter:
        # The pattern sits beside the word it qualifies rather than after the
        # colon. It used to trail the whole header (`OBJECTS OVERVIEW, FILTER:
        # MY_TABLE%`), which put the dashed rule under the label and left the
        # pattern past its end, and left this header the one that did not
        # close on a colon (ADT #237).
        show_header = f"{show_header}, FILTER {show_filter}"
    if authors:
        show_header = f"{show_header}, CHANGED BY {' '.join(authors)}"
    return f"OBJECTS {show_header}:"
