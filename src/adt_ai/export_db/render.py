from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.shared.dates import recent_since
from adt_ai.shared.progress import DROPBOX_PATH_RE, print_adt_header


class ExportDbReporter:
    @property
    def reports_objects(self) -> bool:
        return True

    def overview(
        self,
        schema: str,
        objects: list[DatabaseObject],
        names: list[str] | None = None,
        recent_days: int | None = None,
        authors: list[str] | None = None,
        changed_since: str | None = None,
    ) -> None:
        pass

    def recent_note(self, message: str) -> None:
        pass

    def deleted_objects(self, schema: str, objects: list[DatabaseObject]) -> None:
        pass

    def start_export(self, schema: str, total: int) -> None:
        pass

    def export_object(
        self,
        database_object: DatabaseObject,
        duplicates: list[str] | None = None,
    ) -> None:
        pass

    def finish_type(self, schema: str, object_type: str) -> None:
        pass

def _adt_cell(value: object, width: int, numeric: bool) -> str:
    # coalesce only None to "" — a legitimate falsy value such as the int 0
    # (e.g. a sub-second duration) must still render. ``str(value or "")``
    # disagreed with the width/numeric detection and silently dropped the cell.
    cell = "" if value is None else str(value)
    text = DROPBOX_PATH_RE.sub("Dropbox/", cell)
    align = ">" if numeric else "<"
    return f"{text:{align}{width}}   "

@dataclass(frozen=True)
class _AdtTableLayout:
    """Pre-computed column geometry, shared by the batch and streaming renders.

    Holding the widths/alignment lets a single row be emitted in pieces — the
    object name first, the rest after an action runs — that rejoin byte-for-byte
    with the whole-row render. ``cells_segment`` carries the two-space table
    indent only on the leading segment (``start == 0``).
    """

    columns: tuple[str, ...]
    widths: tuple[int, ...]
    numeric: tuple[bool, ...]

    def cells_segment(self, values: Sequence[object], start: int, end: int) -> str:
        line = "  " if start == 0 else ""
        for index in range(start, end):
            line += _adt_cell(values[index], self.widths[index], self.numeric[index])
        return line

    def row_line(self, values: Sequence[object]) -> str:
        return self.cells_segment(values, 0, len(self.columns))

    def header_line(self) -> str:
        return self.row_line([column.upper().replace("_", " ") for column in self.columns])

    def separator_line(self) -> str:
        return self.row_line(["-" * width for width in self.widths])

def _compute_adt_layout(
    rows: list[dict[str, object]],
    columns: Sequence[str],
    min_widths: Mapping[str, int],
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
    numeric = [
        bool(rows)
        and all(
            str(row.get(column, "")).isnumeric() or row.get(column, "") in {None, ""}
            for row in rows
        )
        for column in columns
    ]
    return _AdtTableLayout(tuple(columns), tuple(widths), tuple(numeric))

def print_adt_table(
    rows: list[dict[str, object]],
    min_widths: Mapping[str, int] | None = None,
    columns: Sequence[str] | None = None,
    leading_blank: bool = True,
) -> None:
    # ``columns`` makes a requested section render even with zero rows: the
    # header and separator still print so the user sees the feature ran (an
    # empty table reads as "looked, found nothing", not "silently did nothing").
    if not rows and not columns:
        return
    min_widths = min_widths or {}
    columns = list(rows[0].keys()) if rows else list(columns)
    layout = _compute_adt_layout(rows, columns, min_widths)
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
    def __init__(self, silent: bool = False) -> None:
        self._last_type_by_schema: dict[str, str] = {}
        self._silent = silent

    @property
    def reports_objects(self) -> bool:
        return not self._silent

    def overview(
        self,
        schema: str,
        objects: list[DatabaseObject],
        names: list[str] | None = None,
        recent_days: int | None = None,
        authors: list[str] | None = None,
        changed_since: str | None = None,
    ) -> None:
        print_adt_header(
            _overview_header(
                names         = names,
                recent_days   = recent_days,
                authors       = authors,
                changed_since = changed_since,
            )
        )
        counts = Counter(database_object.object_type for database_object in objects)
        rows = [
            {"object_type": object_type, "count": counts[object_type]}
            for object_type in sorted(counts)
        ]
        print_adt_table(rows)

    def recent_note(self, message: str) -> None:
        print_adt_header(message)

    def deleted_objects(self, schema: str, objects: list[DatabaseObject]) -> None:
        if not objects:
            return
        grouped: dict[str, list[str]] = {}
        for database_object in sorted(objects, key=lambda item: (item.object_type, item.name)):
            grouped.setdefault(database_object.object_type, []).append(database_object.name)
        print_adt_header("DELETED OBJECTS:")
        print_adt_pipes(grouped)

    def start_export(self, schema: str, total: int) -> None:
        self._last_type_by_schema[schema] = ""
        print_adt_header("EXPORTING OBJECTS:", f"({total})")
        if self._silent:
            return
        print()

    def export_object(
        self,
        database_object: DatabaseObject,
        duplicates: list[str] | None = None,
    ) -> None:
        last_type = self._last_type_by_schema.get(database_object.schema, "")
        object_type = (
            database_object.object_type
            if database_object.object_type != last_type
            else ""
        )
        self._last_type_by_schema[database_object.schema] = database_object.object_type
        if self._silent:
            return
        if not duplicates:
            print(f"{object_type:>20} | {database_object.name:<54}")
            return
        # One row per stale clone, so the object's every location is visible and
        # the user can delete the wrong ones by hand. The name column is left
        # unpadded here: an over-long, deliberately ragged row is how a [DUPE]
        # stands out from the aligned object list around it.
        for index, location in enumerate(duplicates):
            label = object_type if index == 0 else ""
            print(f"{label:>20} | {database_object.name} | {location} [DUPE]")

    def finish_type(self, schema: str, object_type: str) -> None:
        if self._silent:
            return
        print(f"{'':>20} |")

def _overview_header(
    names: list[str] | None,
    recent_days: int | None,
    authors: list[str] | None = None,
    changed_since: str | None = None,
) -> str:
    if changed_since is not None:
        # Watermark mode: the cutoff is a real stored instant from the database
        # clock, so it is shown verbatim rather than re-derived from a day count.
        show_header = f"CHANGED SINCE {changed_since} (LAST EXPORT)"
    elif recent_days is None:
        show_header = "OVERVIEW"
    else:
        window_start = recent_since(recent_days)
        show_header = f"CHANGED SINCE {window_start}"
    show_filter = " ".join(names or ["%"])
    show_filter = f" {show_filter} ".replace(" % ", " ").strip()
    if show_filter:
        show_header = f"{show_header}, FILTER"
    if authors:
        show_header = f"{show_header}, CHANGED BY {' '.join(authors)}"
    return f"OBJECTS {show_header}: {show_filter}".rstrip()
