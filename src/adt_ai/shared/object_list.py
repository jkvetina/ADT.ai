"""The one renderer for a `TYPE | NAME` list of database objects (ADT #506).

Jan, 2026-08-24, reading a `patch -create` run beside an `export_db` one: the
patch warning printed `  - TABLE CORE_LOCKS` while the export printed the same
objects as a piped, type-grouped list. Two shapes for one thing, and the reason
they could drift is that the shape existed three times:

- `export_db/render.py::export_object`, streamed, with its own per-schema
  last-type state and its own `finish_type` separator.
- `export_db/table.py::print_adt_pipes`, batched, sorted, name unpadded.
- `recompile/render.py::_TrailingRowFormatter`, streamed and batched, padded,
  unsorted, no separator.

All three drew the same 20-character type column and disagreed on everything
else. This module is that column, once. `export_db`'s shape wins on each of the
three disagreements, which is Jan's call on the same day: the name pads, a
separator row closes every type group, and a batch render sorts.

**The unit is the object, never the file that holds it.** A group move is a
delete at one path plus an add at another, so a listing keyed on paths reports a
move as a deletion (`#498`, `#499`). A caller that holds paths resolves them to
`(TYPE, NAME)` before it gets here.

Nothing here connects, reads the repo or writes a file: it turns pairs into
strings, and one convenience function prints them.
"""

from __future__ import annotations

from collections.abc import Iterable

# The type column, right-aligned, and the name column it pads out to. Old ADT's
# widths, preserved because the listing they produce is a compatibility contract
# (SOP §Legacy parity) and because two of the three implementations already
# agreed on them.
TYPE_WIDTH = 20
NAME_WIDTH = 54

# The type cell a continuation row draws: what `type_cell` returns for a repeat,
# spelled once so a caller building a ragged row of its own never re-formats it.
EMPTY_TYPE_CELL = " " * TYPE_WIDTH


def type_separator() -> str:
    """The bare `                     |` row that closes a type group.

    `export_db` has always printed one after every type group, the last one
    included: the runner calls `finish_type` whenever the next object carries a
    different type or there is no next object. A batch render reproduces that
    rather than dropping the trailing one, so a listing built in one go is the
    same bytes as one streamed row by row.
    """
    return f"{EMPTY_TYPE_CELL} |"


class ObjectRowFormatter:
    """Builds `TYPE | NAME` rows, naming the type only when it changes.

    Stateful by design, it has to remember the previous type. The state is keyed
    on a caller-supplied ``scope`` because `export_db` renders one listing per
    schema in the same run and a schema's first row must name its type even when
    the schema before it ended on the same one. A caller with one listing passes
    no scope and gets a single bucket.
    """

    def __init__(self) -> None:
        self._last_type: dict[str, str] = {}

    def type_cell(self, object_type: str, scope: str = "") -> str:
        """The right-aligned type cell, blank when the type repeats in ``scope``.

        Exposed on its own because two rows are deliberately ragged and build
        their own tail: `export_db`'s `[DUPE]` row, which names every stale
        location of one object, and its `-recent` row, which trails the author
        who overtook the export. Both open on this cell.
        """
        last = self._last_type.get(scope, "")
        self._last_type[scope] = object_type
        return f"{object_type if object_type != last else '':>{TYPE_WIDTH}}"

    def row(self, object_type: str, object_name: str, scope: str = "") -> str:
        """One full row: the type cell, the pipe, and the padded name."""
        return f"{self.type_cell(object_type, scope)} | {object_name:<{NAME_WIDTH}}"

    def stream_rows(self, object_type: str, object_name: str, scope: str = "") -> list[str]:
        """What one object contributes to a listing being printed as it goes.

        A separator first when this object opens a new type group, then its own
        row. A streamed listing closes its LAST group itself, once the caller
        knows there is no next object, which is the same split `export_db`'s
        runner already makes between `export_object` and `finish_type`.
        """
        rows: list[str] = []
        if scope in self._last_type and self._last_type[scope] != object_type:
            rows.append(type_separator())
        rows.append(self.row(object_type, object_name, scope))
        return rows

    def reset(self, scope: str = "") -> None:
        """Forget ``scope``'s last type, so its next row names its own.

        `export_db` calls this as each schema's listing opens: a segment that
        follows one ending on the same type would otherwise open on a blank type
        cell, and a reader arriving at a new section would have nothing telling
        them what they are looking at.
        """
        self._last_type.pop(scope, None)


def object_rows(objects: Iterable[tuple[str, str]]) -> list[str]:
    """A whole listing from `(TYPE, NAME)` pairs, sorted, each group closed.

    Sorted on both fields because the reader is looking a name up rather than
    reading a log, which is the same argument `print_adt_pipes` made when it
    sorted its keys and `_emit_planned_moves` makes for its files.

    Built by streaming through the same formatter a live listing uses, so the
    batch and streamed renders are one body rather than two that agree today. A
    caller whose objects already arrive ordered (`recompile -trailing` reads
    them `ORDER BY s.type, s.name`) streams instead, and gets the same bytes
    without a second sort disagreeing with the database's collation.
    """
    rows: list[str] = []
    formatter = ObjectRowFormatter()
    for object_type, object_name in sorted(objects):
        rows.extend(formatter.stream_rows(object_type, object_name))
    if rows:
        rows.append(type_separator())
    return rows


def print_object_rows(objects: Iterable[tuple[str, str]]) -> None:
    """`object_rows` straight to the screen, silent on an empty listing."""
    for row in object_rows(objects):
        print(row)


__all__ = [name for name in globals() if not name.startswith("__")]
