"""One exported value as the CSV cell it is written as.

Split out of `export_data/runner.py` by card `#923`. That card taught the cell
writer Oracle's own spelling of an INTERVAL DAY TO SECOND, and the runner was
already past 20 000 bytes, the budget the card holds every file it touches to.
The seam was already there: the runner reaches the database and decides which
files a table writes, and everything here answers one smaller question, what
text a single value becomes, and refuses the values that have no honest answer.

`from adt_ai.export_data.runner import _csv_cell` still works and is the spelling
`diff -data` compares rows with; `runner.py` re-exports it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from adt_ai.export_data.csv_formulas import neutralized
from adt_ai.export_data.intervals import ds_interval_text, is_ym_interval, ym_interval_text
from adt_ai.export_data.sidecars import _read_lob_value


class _ExactNumber(Decimal):
    """A NUMBER the CSV writer prints in full rather than in exponent notation.

    `csv.QUOTE_NONNUMERIC` leaves a Decimal unquoted, which is what keeps a
    number a number in the file, and prints it with `str()`, which switches to
    `1E+3` once the exponent leaves a narrow window. The CSV is read back by
    the MERGE builder and by people, so the plain form is pinned here (`#670`).
    """

    def __str__(self) -> str:
        return format(self, "f")


#: Every Python type `csv.writer` renders as the value itself rather than as a
#: description of the object holding it. A type outside this list has no CSV form
#: this export can vouch for, and `csv_cell` refuses it rather than guessing.
#: `timedelta` left the list with `#923`: its `str()` is Python's own spelling,
#: so it is rendered in Oracle's before the writer sees it, as `IntervalYM` is.
_CSV_SCALARS = (str, int, float, Decimal, datetime, date, time)


def csv_cell(value: Any, column_name: str = "") -> Any:
    """One CSV cell, rendered by its type or refused (`#670`, `#695`).

    A RAW arrives as `bytes`, whose `str()` is Python's `b'\\x01\\xffA'` repr, so
    it is hex-encoded the way `HEXTORAW` reads it back. A NUMBER arrives as a
    `Decimal` (see `shared/db.fetch_all`) and keeps every digit it was stored
    with, unquoted. A LOB arrives as a handle and is read, which is how the
    spatial columns' WKT (a CLOB, built by the SELECT itself) reaches the row.
    Text is neutralized so a spreadsheet cannot run it, and `merge_script` takes
    the prefix back off on the way in (`#707`, `export_data/csv_formulas.py`).

    An INTERVAL DAY TO SECOND arrives as a `timedelta` and an INTERVAL YEAR TO
    MONTH as `oracledb.IntervalYM`, and each is written the way Oracle writes
    one, `+1 02:03:04.000005` and `+01-02`, which the MERGE reads back with
    `TO_DSINTERVAL` and `TO_YMINTERVAL` (`#923`, `export_data/intervals.py`).
    The leading sign is Oracle's, not something a person typed, so it is not
    neutralized: the same reasoning that leaves a negative NUMBER bare.

    Everything else raises, and that is the point of the function (`#695`). This
    used to end in `return value`, so a driver object the writer had no rendering
    for was handed to `str()` and became its `repr()`: an `SDO_GEOMETRY` column
    exported as `<oracledb.DbObject MDSYS.SDO_GEOMETRY at 0x10c9b2e40>` on every
    row, the geometry never left the database, and the hex is CPython's `id()`,
    so two runs of one export wrote different bytes for identical data. A memory
    address that reloads as a text literal is worse than a stopped export, which
    is the same stance `_json_ready` takes in `sidecars.py` for a JSON scalar.
    """
    value = _read_lob_value(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex().upper()
    if isinstance(value, Decimal):
        return _ExactNumber(value)
    if isinstance(value, str):
        return neutralized(value)
    if isinstance(value, timedelta):
        return ds_interval_text(value)
    if is_ym_interval(value):
        return ym_interval_text(value)
    if value is None or isinstance(value, _CSV_SCALARS):
        return value
    raise ValueError(
        f"COLUMN {column_name or '?'} HAS NO TEXT FORM\n\n"
        f"It holds a {type(value).__name__} this export cannot write back; a spatial\n"
        "column is exported as WKT, and anything else has to be left out with the\n"
        "ignored_columns setting."
    )
