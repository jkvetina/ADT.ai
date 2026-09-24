"""Where a `rest export` transcript is structure, and where it is someone's text.

A handler's `p_source` is one quoted literal running over as many lines as its
developer wrote, and it holds whatever they wrote. A `plsql/block` handler that
commits prints a line reading exactly `COMMIT;`, the line that also closes the
export, and the split stopped there: the modules after that handler, the rest of
the handler and the schema's roles and privileges were dropped while the run
reported success (ADT #923). A source line starting `ORA-` failed the whole
export the same way. The export's own structure only ever sits at the top level,
so a line is read as structure only when it starts outside every literal and
comment.

Where the literals and comments are is the shared scanner's answer, `sql_spans`,
never a second reading of PL/SQL quoting here: a `''` inside a literal, the
alternative quoting `q'[...]'` and a quote inside a comment are read there once
for every command (`tests/contracts/shared_readers.txt`).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence

from adt_ai.export_db.normalizers import sql_spans


def top_level_lines(lines: Sequence[str]) -> list[bool]:
    """For each line, whether it starts outside every literal and block comment.

    A line opening with a quote starts outside the literal it opens. A closed
    literal or comment never ends on a line break, so one that ends exactly
    where a line starts was never closed.
    """
    opaque = [
        (start, end)
        for kind, start, end in sql_spans("\n".join(lines))
        if kind != "code"
    ]
    opened = [start for start, _ in opaque]
    top: list[bool] = []
    offset = 0
    for line in lines:
        before = bisect_left(opened, offset) - 1
        top.append(before < 0 or opaque[before][1] < offset)
        offset += len(line) + 1
    return top
