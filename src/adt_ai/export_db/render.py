"""What an `export_db` run puts on screen, and the reporter every mode drives.

The ADT table renderer this module used to carry lives in `export_db/table.py`
since `#437` split it out at the 20 KB context budget. It is re-exported below,
so `from adt_ai.export_db.render import print_adt_table` still resolves for the
five other modules that render one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

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
    print_adt_pipes,
    print_adt_table,
)
from adt_ai.shared.dates import recent_since
from adt_ai.shared.progress import print_adt_header
from adt_ai.shared.recent_state import parse_timestamp

__all__ = [
    "ADT_TABLE_GUTTER",
    "ADT_TABLE_INDENT",
    "ConsoleExportDbReporter",
    "ExportDbReporter",
    "GRANT_OVERVIEW_ROW",
    "OVERVIEW_COLUMNS",
    "_AdtTableLayout",
    "_adt_cell",
    "_commit_stdout",
    "_compute_adt_layout",
    "adt_table_line_width",
    "close_adt_table",
    "open_adt_table",
    "print_adt_header",
    "print_adt_pipes",
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

    def overview_grants(self, changed: bool) -> None:
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

# The overview table's two columns, named once so a run that discovers nothing
# still renders its header instead of collapsing to no table at all.
OVERVIEW_COLUMNS = ("object_type", "count")

# The overview row for the grant artifacts, measured into the table before the
# reads that decide whether it prints (`#437`). Read only, never mutated.
GRANT_OVERVIEW_ROW: Mapping[str, object] = {"object_type": GRANT_OBJECT_TYPE, "count": ""}

class ConsoleExportDbReporter(ExportDbReporter):
    def __init__(self, silent: bool = False, compact: bool = False) -> None:
        self._last_type_by_schema: dict[str, str] = {}
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
        # The overview table held open while the grant reads run, or None when
        # no table is open. See `overview` and `overview_grants`.
        self._overview_layout: _AdtTableLayout | None = None

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
        if not grants:
            print_adt_table(rows)
            return
        # **The table opens here and the GRANT row waits** (`#437`). Whether
        # that row is owed at all is a question only the four privilege reads
        # can answer, and they run under this open table, which is what keeps
        # them announced. Jan, 2026-08-20: *"You will print table header and
        # only AFTER you fetch grants and evaluate changes, you will print the
        # line"*. Sized for the row whichever way the answer goes, so the
        # decision can never move a column that has already printed.
        self._overview_layout = open_adt_table(
            rows,
            columns   = list(OVERVIEW_COLUMNS),
            sized_for = [GRANT_OVERVIEW_ROW],
        )

    def overview_grants(self, changed: bool) -> None:
        """Close the overview table, printing the `GRANT` row only if one moved.

        ``changed`` is what the four reads came back with: at least one artifact
        this export writes differs from the file on disk. A row on a run that
        rewrites the same bytes claims work nobody did, which is the console
        rule this correction was filed on, *"A row must not claim work it might
        not perform"*.

        **A row, and deliberately still no number** (`#382`): `grants_received`
        writes one file per owner, so the figure a reader would compare against
        the schema is not the count of anything on screen.

        A no-op when no table is open, which is every run narrowed by an exact
        `-name`: the overview is skipped outright there, so there is nothing to
        append a row to and nothing to close.
        """
        layout = self._overview_layout
        self._overview_layout = None
        if layout is None:
            return
        if changed:
            print(layout.row_line([GRANT_OVERVIEW_ROW[column] for column in OVERVIEW_COLUMNS]))
        close_adt_table()

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
