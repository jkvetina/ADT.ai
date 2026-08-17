"""What a SQLcl DIFF leftover is, and how to get rid of one (ADT #356).

The `DIFF` command materializes each side of a comparison as a working table
named `<something>$1` / `<something>$2` in the SOURCE schema, and it does not
always clean up after itself: a run that dies mid-way, or loses its connection,
leaves them behind. Old ADT answered this with `patch -deldiff`, a flag you had
to remember to type, on the wrong command, after you already had the mess.

Jan, 2026-08-15: *"Diff tables must be cleaned up right away after use, but also
in exception block since the run might fail, and since it might fail in a way you
are not able to cleaned it up right away (for example lost VPN connection) I want
to also cleaned up these diff leftovers on next export_db or next patch run!
These diff tables must be also ignored in export_db so they wont become part of
the repo!"*

So there are two jobs here and they are genuinely different:

* **Dropping** them, at the producer immediately and in every later connecting
  run as a sweep, which is `drop_diff_tables`.
* **Ignoring** them, so that a leftover sitting in the schema when `export_db`
  runs never becomes a committed `.sql` file, which is `is_diff_table`. Dropping
  alone would not cover this: the export can read the dictionary in the same
  second the sweep runs, and a table exported before it is dropped is a file in
  the repo forever.

One definition, three callers, so `export_db` cannot start disagreeing with the
sweep about which tables these are.
"""

from __future__ import annotations

import re
from typing import Any

from adt_ai.shared.queries.diff_tables import (
    DIFF_TABLES_QUERY,
    DROP_DIFF_TABLE_STATEMENT,
)
from adt_ai.shared.sql_identifiers import safe_identifier

# The Python-side mirror of the SQL `LIKE` above. Anchored, because a `search` is
# not a `LIKE`: without the `$` a name merely CONTAINING `$1` would match, and
# `$` is legal throughout an Oracle identifier.
DIFF_TABLE_RE = re.compile(r"\$[12]$")


def is_diff_table(object_name: str) -> bool:
    """Is this the name of a SQLcl DIFF working table?"""
    return bool(DIFF_TABLE_RE.search(str(object_name or "").upper()))


def drop_diff_tables(gateway: Any) -> list[str]:
    """Drop every diff leftover in the connected schema, and name what went.

    Returns the dropped names so the caller can print them. They ARE printed at
    every call site: this puts a `DROP TABLE` inside commands that were read-only
    exports until now, and a project that legitimately owns a `%$1` table would
    otherwise lose it in silence.

    `PURGE` because the recycle bin is the other half of the mess: a plain drop
    leaves `BIN$...` objects that `export_db` then has to filter separately.

    Never raises. Every caller runs this as cleanup beside its real work, most of
    them in an exception path, so a failure here must not replace the error the
    caller is already reporting, or mask a successful run behind a tidy-up that
    could not get a lock.
    """
    dropped: list[str] = []
    try:
        rows = gateway.fetch_all(DIFF_TABLES_QUERY)
    except Exception:
        return dropped
    for row in rows or []:
        table_name = _row_table_name(row)
        if not table_name or not is_diff_table(table_name):
            continue
        try:
            safe_identifier(table_name, role="table name")
            gateway.execute(DROP_DIFF_TABLE_STATEMENT.format(table_name=table_name))
        except Exception:
            # One table that will not drop, usually a lock held by the session
            # that abandoned it, must not stop the rest being cleared.
            continue
        dropped.append(table_name)
    return dropped


def _row_table_name(row: Any) -> str:
    if isinstance(row, dict):
        value = row.get("TABLE_NAME", row.get("table_name"))
    else:
        value = row[0] if row else None
    return str(value or "").upper()
