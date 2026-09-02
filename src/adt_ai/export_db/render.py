"""What an `export_db` run puts on screen, and the reporter every mode drives.

The ADT table renderer this module used to carry lives in `export_db/table.py`
since `#437` split it out at the 20 KB context budget. It is re-exported below,
so `from adt_ai.export_db.render import print_adt_table` still resolves for the
five other modules that render one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from adt_ai.export_db.grants import GRANT_OBJECT_TYPE
from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.export_db.progress import ObjectProgressBar
from adt_ai.export_db.table import (
    ADT_TABLE_GUTTER,
    ADT_TABLE_INDENT,
    _adt_cell,
    _AdtTableLayout,
    _commit_stdout,
    _compute_adt_layout,
    adt_table_line_width,
    close_adt_table,
    open_adt_table,
    print_adt_table,
)
from adt_ai.shared.dates import recent_since
from adt_ai.shared.file_list import print_file_rows
from adt_ai.shared.object_list import (
    EMPTY_TYPE_CELL,
    NAME_WIDTH,
    ObjectRowFormatter,
    print_listing_gap,
    print_object_rows,
    type_separator,
)
from adt_ai.shared.progress import print_adt_header
from adt_ai.shared.recent_state import parse_timestamp

__all__ = [
    "ADT_TABLE_GUTTER",
    "ADT_TABLE_INDENT",
    "ConsoleExportDbReporter",
    "ExportDbReporter",
    "OVERVIEW_COLUMNS",
    "_AdtTableLayout",
    "_adt_cell",
    "_commit_stdout",
    "_compute_adt_layout",
    "adt_table_line_width",
    "close_adt_table",
    "open_adt_table",
    "print_adt_header",
    "print_adt_table",
]


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

    def overview_grants(self, changed: bool, count: int) -> None:
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

    def finish_type(self, schema: str, object_type: str) -> None:
        pass

    def finish_export(self, schema: str) -> None:
        pass

# The overview table's two columns, named once so every render of it agrees.
# It used to exist so a run discovering nothing still printed its column
# headers; `#442` retired that reading, and a table with no rows in it now
# prints nothing at all.
OVERVIEW_COLUMNS = ("object_type", "count")

class ConsoleExportDbReporter(ExportDbReporter):
    def __init__(self, silent: bool = False, compact: bool = False) -> None:
        # The shared `TYPE | NAME` builder (`#506`). Scoped per schema because a
        # run renders one listing per schema and each has to open by naming its
        # own type, whatever the segment above it ended on.
        self._rows = ObjectRowFormatter()
        self._silent = silent
        # `-silent` outranks `-compact`, the precedence `ut` already carries
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
        # The counted object rows waiting on the grant reads, or None when this
        # segment has no overview to render. See `overview` and
        # `overview_grants`.
        self._overview_rows: Sequence[Mapping[str, object]] | None = None
        # Whether this segment printed an overview section header. It is what
        # `start_export` consults before suppressing itself on a zero run: the
        # header is the thing that already said the run found nothing, and
        # without one there would be nothing on screen at all.
        self._overview_section = False

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
        self._overview_section = True

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
        if not grants:
            print_adt_table(rows)
            return
        # **Nothing below the section header prints yet** (`#442`). Whether the
        # table owes a `GRANT` row is a question only the four privilege reads
        # can answer, and until they have, neither is whether it owes anything
        # at all: a schema where no object moved still prints a row when a
        # privilege did. Jan, 2026-08-21: *"Drop both empty tables if you can.
        # But you cant tell, you have to run at least grants before you print
        # the content below header..."* The reads run under the section header
        # `begin_overview` already put up, which is what keeps them announced,
        # and the whole table renders in `overview_grants` once the answer is
        # in. Every row is in hand at print time, so `sized_for` has nothing
        # left to size for: a late row can no longer widen a printed column
        # because there is no longer such a thing as a late row.
        self._overview_rows = rows

    def overview_grants(self, changed: bool, count: int) -> None:
        """Render the overview table, now that the `GRANT` row is settled.

        ``changed`` is what the four reads came back with: at least one artifact
        this export writes differs from the file on disk. A row on a run that
        rewrites the same bytes claims work nobody did, which is the console
        rule `#437` was filed on, *"A row must not claim work it might not
        perform"*.

        ``count`` is how many artifacts this schema exports, filling the cell
        `#382` left blank because `grants_received` writes one file per owner
        and no figure compares against the schema. Jan, 2026-08-26: *"You dont
        list count for grants, cant you do that?"* That objection was to the
        wrong number, not to a number, since a `VIEW` row counts what the
        export writes rather than what the schema holds, and one
        `DatabaseObject` per file is the formula on both (`#565`). The two
        arguments stay separate: the row PRINTS on what moved and SAYS what is
        written.

        Two ways this prints nothing. No rows and no `GRANT` row means the run
        found nothing at all, and `#442` retired the empty two-line table that
        used to stand for that answer. And no overview was started, which is
        every run narrowed by an exact `-name`: the section is skipped outright
        there, so there is no table to render.
        """
        rows = self._overview_rows
        self._overview_rows = None
        if rows is None:
            return
        if changed:
            rows = [*rows, {"object_type": GRANT_OBJECT_TYPE, "count": count}]
        if not rows:
            return
        print_adt_table(rows, columns=list(OVERVIEW_COLUMNS))

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
        # Flat, and not a file list: a SQLcl DIFF leftover is a TABLE name, so
        # there is no folder to group it under (ADT #504).
        print_file_rows(tables, nested=False)
        print()

    def deleted_objects(self, schema: str, objects: list[DatabaseObject]) -> None:
        # Through the shared renderer since `#506`, so this listing and the
        # `EXPORTING <n> OBJECTS:` one above it cannot drift apart: they are the
        # same rows built by the same code, one batched and one streamed. The
        # local `print_adt_pipes` this used to call left the name unpadded and
        # closed no type group, which is two of the three ways the shape had
        # already forked.
        if not objects:
            return
        print_adt_header("DELETED OBJECTS:")
        print_object_rows(
            (database_object.object_type, database_object.name)
            for database_object in objects
        )
        print()

    def start_export(
        self,
        schema: str,
        total: int,
        estimate: float = 0.0,
        widest_label: str = "",
    ) -> None:
        self._rows.reset(schema)
        # **A section header must not claim work it will not perform** (`#442`).
        # At zero there is nothing to export, so the header and its dashed rule
        # stood over an empty screen; the overview header above has already said
        # the run found nothing, and saying it twice with no rows either time is
        # what Jan called stupid. Guarded on that header actually being up: an
        # exact `-name` run skips the overview outright, and suppressing this
        # there would leave the whole segment silent.
        announced_empty = self._overview_section
        self._overview_section = False
        if total == 0 and announced_empty:
            return
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
            # percentage is the same number of dots whichever type is in flight.
            # **The bar counts objects and nothing else** (`#437`). It carried
            # one extra unit for the grant reads while those ran under it, which
            # is what stopped the row reading 100% mid-work (`#379`); the reads
            # happen under the overview table now, so a unit for them would be
            # one this bar never waits on.
            self._bar = ObjectProgressBar(
                total,
                previous_seconds = estimate,
                widest_label     = widest_label,
            )
            self._bar.begin()
            return
        # This bare `print()` was the ONLY object listing in the tool that
        # opened on a blank line, and the three that go through `object_rows`
        # had none, which is the defect ADT #524 was filed on. The gap is the
        # renderer's now; this call is the streamed half of it.
        print_listing_gap()

    def export_object(
        self,
        database_object: DatabaseObject,
        duplicates: list[str] | None = None,
        changed_by: str | None = None,
    ) -> None:
        # The type cell is drawn by the shared builder (`#506`), which owns the
        # width and the repeat suppression for every object listing in the CLI.
        # It is taken before the `-compact`/`-silent` returns below so the state
        # advances identically in every mode, exactly as the local copy did.
        type_cell = self._rows.type_cell(
            database_object.object_type,
            scope = database_object.schema,
        )
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
                row = f"{type_cell} | {database_object.name} [{changed_by}]"
                print(row, end="", flush=True)
                return
            print(f"{type_cell} | {database_object.name:<{NAME_WIDTH}}", end="", flush=True)
            return
        # One row per stale clone, so the object's every location is visible and
        # the user can delete the wrong ones by hand. The name column is left
        # unpadded here: an over-long, deliberately ragged row is how a [DUPE]
        # stands out from the aligned object list around it.
        for index, location in enumerate(duplicates):
            label = type_cell if index == 0 else EMPTY_TYPE_CELL
            row = f"{label} | {database_object.name} | {location} [DUPE]"
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

    def finish_type(self, schema: str, object_type: str) -> None:
        if self._silent or self._compact:
            return
        print(type_separator())

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
