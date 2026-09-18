"""The `diff` result screen (ADT #763, trimmed by #769, re-answered by #790).

Before `#763` the screen ended on a zip path and a timer, which answered
everything except the question `diff` exists to answer. `#769` cut everything
that was neither, and the counts table took `export_db`'s two-column
`OBJECT TYPE` / `COUNT` shape so a reader meets one table shape across the tool.
`#773` finished that job on the listings: no header on this screen carries a
count, and the object listing carries no schema column.

`#790` answers the question the listings still dodged, **which side is short of
what**. Three things changed, all of them Jan's wording:

* **`ACTION` is gone and `STATUS` replaces it**, reading `MISSING`, `CHANGED` or
  `EXTRA`. The verb the artifact would run was never the question: *"COMMENT
  COMMENT will tell me what? SHIT. JOBS NULL tells me what? NOTHING."*
  No cell says `ON <env>`: the target is the same for every row, so spelling it
  per row is width spent on a repeat. The direction is stated once, in the
  `COMPARING SCHEMAS:` row above, and nowhere else.
* **`CHANGED GRANTS:` holds two tables, split by DIRECTION rather than status.**
  The first cut split them missing/extra and named the far end `PARTY`: *"PARTY
  ??? WTF is this? IT IS OWNER. I was wrong, we have incoming and outgoing
  grants."* So the incoming table leads with `OWNER`, whose object we were
  granted, the outgoing one carries `GRANTEE` after the object name, who we
  granted ours to, and `STATUS` is a column in both. The compared schema is still
  never printed. The privilege cell shows the first privilege and ` + N` for the
  rest, so several privileges on one object cannot break the row.
* **`LEGEND:` closes the screen, and only when something was listed.** A status
  word on a table is a word the reader has to already know; the legend is the one
  place they are spelled out, so it sits under everything rather than above it,
  and it explains only the statuses that actually appeared.
* **No table on this screen exceeds `MAX_LINE`**, *"We have to make sure all
  tables dont exceed 80 chars!"* Oracle names run to 128 characters, so a cap is
  a budget rather than a hope: `_fit` measures the line through the renderer's
  own geometry and trims the widest flexible column until it fits.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from adt_ai.cli.constants import print_adt_header, print_adt_table
from adt_ai.diff.inventory import CHANGED, EXTRA, MISSING, GrantRow
from adt_ai.diff.summary import DiffSummary
from adt_ai.shared.tables import adt_table_line_width

IN_SYNC_HEADER = "NO DIFFERENCES:"
SUMMARY_HEADER = "CHANGES BY OBJECT TYPE:"
OBJECTS_HEADER = "CHANGED OBJECTS:"
GRANTS_HEADER = "CHANGED GRANTS:"
LEGEND_HEADER = "LEGEND:"
NO_MATCH_HEADER = "NO MATCHING CHANGES:"

#: What each status means, in one line each. Worded from Jan's own reading of the
#: question (`#790`): *"I need to know that this object is MISSING on TEST (and
#: we have it on DEV), or which CHANGED (the versions dont match), or object
#: which was DELETED from dev, but we have it on TEST"*. Source and target rather
#: than environment names, because the header above already names the pair once.
_LEGEND = {
    CHANGED : "on both sides, and the two do not match",
    MISSING : "the source has it, the target does not",
    EXTRA   : "the target has it, the source does not",
}

#: The widest line any table on this screen may render. Jan asked for 80; the
#: renderer's own geometry note (`#269`) budgets a line to 78 so the row still
#: clears an 80-column terminal with the cursor on it, and a rule that holds at
#: 78 holds at 80 by construction.
MAX_LINE = 78

#: Which columns give up characters when a line will not fit, widest first and in
#: that order of preference. The names go first because a name is still
#: recognisable from its first forty characters; `object_type` is last because a
#: trimmed type reads as a different type, and it is only reached when the names
#: are already at `_MIN_CELL`. `STATUS` and `PRIVILEGE` never give way: both are
#: short, fixed vocabulary, and a trimmed one would be a guess rather than a
#: shortened fact.
_FLEXIBLE = ("object_name", "owner", "grantee")
_LAST_RESORT = ("object_type", "table_name")

#: What a trimmed cell ends on, so a shortened name can never be mistaken for a
#: real one.
_ELLIPSIS = "..."


#: The line under `NO DIFFERENCES:` for each kind of comparison. The object
#: comparison leaves an artifact behind and says it is empty; `-rest` writes
#: none, so its line must not mention one (ADT #878).
IN_SYNC_OBJECTS = "the two schemas already match, the artifact carries no changes"
IN_SYNC_REST = "the two schemas publish the same REST modules, privileges and roles"
IN_SYNC_DATA = "the compared tables hold the same rows on both sides"

#: The section the modes that replace the object comparison open on, and the
#: first section of the `diff -data -out` file (`#886`).
COMPARING_HEADER = "COMPARING SCHEMAS:"


def report_summary(
    summary: DiffSummary | None,
    *,
    verbose: bool = False,
    in_sync_note: str = IN_SYNC_OBJECTS,
) -> None:
    """Print what differs between the two schemas, or say that nothing does.

    Two screens, and the flag picks (Jan, ADT #763): *"Without -verbose I want
    just overview what changed. With verbose I want to list changes without any
    limit."* So the default ends at the counts table, and `-verbose` prints
    every object with no cap at all.

    A `None` summary means the comparison could not be read. The run still
    succeeded, so this stays quiet rather than claiming either outcome.
    """
    if summary is None:
        return
    if summary.in_sync:
        print_adt_header(IN_SYNC_HEADER)
        print(f"  {in_sync_note}")
        print()
        return
    # A filter that matched nothing is NOT two matching schemas (`#773`). The
    # schemas may differ in forty ways and simply not in the way that was asked
    # about, and saying otherwise would be the one wrong answer this screen can
    # give.
    if not summary.total:
        print_adt_header(NO_MATCH_HEADER)
        print("  nothing matched -type / -name; the schemas may still differ elsewhere")
        print()
        return

    # No count beside this header (Jan, `#769`). The table under it IS the
    # count, row by row, and a total repeated above it is a figure a reader has
    # to reconcile against the rows rather than read.
    print_adt_header(SUMMARY_HEADER)
    _print_fitted(
        [
            {"object_type": object_type, "count": count}
            for object_type, count in summary.counts()
        ]
    )

    if not verbose:
        print()
        return

    if summary.objects:
        # Bare header and no schema column (Jan, `#773`): *"Remove these stupid
        # numbers from here"*, *"Remove SCHEMA column from CHANGED OBJECT
        # table."* Every row that remains belongs to the one schema pair the run
        # compared, named once in `COMPARING SCHEMAS:` above.
        print_adt_header(OBJECTS_HEADER)
        _print_fitted(
            [
                {
                    "object_type" : change.object_type,
                    "object_name" : change.object_name,
                    "status"      : change.status,
                }
                for change in summary.objects
            ]
        )
    _report_grants(summary)
    _report_legend(summary)


def _report_grants(summary: DiffSummary) -> None:
    """One header, two tables, split by which way the grant points.

    Jan, ADT #773, on the five rows the object listing printed for them: *"Show
    GRANTS in dedicated table below with proper columns. This is pure shit."*
    `#790` gave them their own section; its second pass fixed what that section
    was split BY. Incoming and outgoing grants answer different questions, so
    each gets the counterparty column its question needs, and the missing/extra
    distinction moves into a `STATUS` column that both tables carry.

    **One header over both, and nothing between them** (Jan: *"create two tables
    inside (without any extra labels/headers/notes)"*). The leading column says
    which table a reader is in, so a label would be a third way to say it.

    A `STATUS` column is only honest because `diff/inventory.py` compares grants
    one PRIVILEGE at a time. A grant on both sides with different privileges
    would otherwise be a third state; split by privilege, each half is squarely
    missing or extra.
    """
    tables = (
        (summary.incoming_grants, _incoming_row),
        (summary.outgoing_grants, _outgoing_row),
    )
    printed = False
    for rows, render in tables:
        if not rows:
            continue
        if not printed:
            print_adt_header(GRANTS_HEADER)
        # The second table skips its own leading blank. The first already closed
        # on one, and TWO blank lines is exactly what separates two sections on
        # this screen, so paying both made the pair read as a second section with
        # its header missing: the reading the absent label was avoiding.
        _print_fitted([render(grant) for grant in rows], leading_blank=not printed)
        printed = True


def _incoming_row(grant: GrantRow) -> dict[str, object]:
    """A grant INTO the compared schema: whose object it is comes first."""
    return {
        "owner"       : grant.owner,
        "object_type" : grant.object_type,
        "object_name" : grant.object_name,
        "privilege"   : grant.privilege_label,
        "status"      : grant.status,
    }


def _outgoing_row(grant: GrantRow) -> dict[str, object]:
    """A grant OUT of it: our object, so the grantee sits after the name."""
    return {
        "object_type" : grant.object_type,
        "object_name" : grant.object_name,
        "grantee"     : grant.grantee,
        "privilege"   : grant.privilege_label,
        "status"      : grant.status,
    }


def _report_legend(summary: DiffSummary) -> None:
    """What the status words mean, under everything, only when there are rows.

    Jan: *"Below all sections you should show LEGEND section explaining statuses.
    Only if there are any changes."* It is the last thing on the screen because a
    reader meets the tables first and the definition when a cell puzzles them, and
    it lists only the statuses this run produced.
    """
    statuses = summary.statuses
    if not statuses:
        return
    print_adt_header(LEGEND_HEADER)
    width = max(len(status) for status in statuses)
    for status in statuses:
        print(f"  {status.ljust(width)}   {_LEGEND[status]}")
    print()


def _print_fitted(
    rows: Sequence[Mapping[str, object]], leading_blank: bool = True
) -> None:
    print_adt_table(_fit(rows), leading_blank=leading_blank)


def _fit(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """The same rows, with the flexible columns trimmed until the line fits.

    The width is measured through `adt_table_line_width`, the renderer's own
    geometry, rather than by counting indents and gutters here, copying that
    arithmetic to a call site is exactly how it drifts (`#269`).

    **The cap is absolute, and that is the whole requirement** (Jan: *"We have to
    make sure all tables dont exceed 80 chars!"*). So the trim never gives up: it
    spends the name columns down to `_MIN_CELL` first, then the object type, and
    then keeps taking from whichever is widest even below that floor. A guarantee
    that holds for ordinary rows and breaks on a wide one is not a guarantee, and
    the grant tables carry five columns of somebody else's identifiers, two of
    them names, so the budget genuinely runs out.

    `_MIN_CELL` is therefore a preference rather than a floor: it decides WHICH
    column gives way next, not whether one has to.
    """
    if not rows:
        return [dict(row) for row in rows]
    columns = list(rows[0].keys())
    widths = {column: _natural_width(rows, column) for column in columns}
    while _line_width(columns, widths) > MAX_LINE:
        widest = _next_to_trim(widths)
        if widest is None:
            break
        widths[widest] -= 1
    return [
        {column: _trim(row.get(column, ""), widths[column]) for column in columns}
        for row in rows
    ]


def _next_to_trim(widths: Mapping[str, int]) -> str | None:
    """The column to take a character from, or `None` when none is left to take.

    Three tiers, in order: a name column still above the comfortable floor, the
    object type above it, then anything still trimmable at all. Stepping down a
    tier only once the tier above is exhausted is what keeps the type intact on
    every table whose names can pay for the line on their own.
    """
    for candidates, floor in (
        (_FLEXIBLE, _MIN_CELL),
        (_LAST_RESORT, _MIN_CELL),
        (_FLEXIBLE + _LAST_RESORT, len(_ELLIPSIS) + 1),
    ):
        available = [
            column
            for column in candidates
            if column in widths and widths[column] > floor
        ]
        if available:
            return max(available, key=lambda column: widths[column])
    return None


#: Below this a trimmed name stops being recognisable, so a column already here
#: is asked for characters only once every other column has been asked first.
_MIN_CELL = 16


def _natural_width(rows: Sequence[Mapping[str, object]], column: str) -> int:
    return max(
        len(column),
        *(len("" if row.get(column) is None else str(row.get(column, ""))) for row in rows),
    )


def _line_width(columns: Sequence[str], widths: Mapping[str, int]) -> int:
    return adt_table_line_width([widths[column] for column in columns])


def _trim(value: object, width: int) -> object:
    text = "" if value is None else str(value)
    if len(text) <= width:
        return value
    return text[: max(0, width - len(_ELLIPSIS))] + _ELLIPSIS


__all__ = [name for name in globals() if not name.startswith("_")]
