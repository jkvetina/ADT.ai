"""The `-verbose` table that says what moved since the previous run (`#251`).

Split from ``render.py`` rather than squeezed into it: that module reached the
repo's 20 KB per-file context budget with this table in it, and the SOP's answer
to a file at the cap is to split along a seam, never to shave comments off until
it fits. The seam is the one ``coverage.py`` already uses one layer down: the
summaries report the run, this reports the difference between two runs.

**It is a table of its own rather than a column on the ones below.** There is no
room left on a `SUMMARY PER SUITE:` row, and a delta is only interesting for the
few suites that moved. Jan, 2026-08-17: *"There is not room in current reports. I
want you to add a new table above summaries but only if -verbose mode is enabled
and in this table I would like you to list only the suites which changed the
ratio since last time."*
"""

from __future__ import annotations

# Through ``render`` rather than straight from ``export_db.runner``: this module
# is imported by ``reporter`` ahead of ``render``, and a second edge into the
# export_db package from here re-enters ``adt_ai.cli`` while it is still
# initialising. One module owns that dependency and this one borrows it.
from typing import TYPE_CHECKING

from adt_ai.ut.cells import percent_cell
from adt_ai.ut.render import print_adt_header, print_adt_table

if TYPE_CHECKING:
    # `store` imports this module's package siblings at runtime, so the
    # change record is named for the checker only.
    from adt_ai.ut.store import CoverageChange

CHANGES_TITLE = "COVERAGE CHANGED SINCE LAST RUN:"

_CHANGES_COLUMNS = ("SUITE_PACKAGE", "WAS", "NOW", "DELTA")


def print_coverage_changes_header() -> None:
    """The heading, printed before the coverage read it announces.

    Whichever table prints first owns the announcement `#379` put above that
    wait, so under `-verbose` with a history this is it and `SUMMARY PER SUITE:`
    follows behind the rows. The announcement moves rather than being duplicated.
    """
    print_adt_header(CHANGES_TITLE)


def print_coverage_changes_rows(changes: tuple[CoverageChange, ...]) -> None:
    """One row per suite whose target package's ratio moved.

    **Only what moved.** Two full summaries already list every suite, so a third
    table repeating them would be the second and worse telling of something
    already told. What this adds is the diff.

    `WAS` and `DELTA` blank together for a package the previous run did not
    measure, the same pairing the `COVERAGE` cells already use, because a package
    appearing for the first time has no comparison rather than a gain of its whole
    figure.

    **Nothing moved prints no table, not an empty one.** The header still stands,
    because it went up before the coverage read to announce it and because an
    empty section is a readable answer, but ``print_adt_table`` on an empty list
    renders a column header and a rule with nothing under them, which reads as a
    table whose rows failed to arrive rather than as a run where nothing changed.
    Measured on ``APP_ADM_NUMBERING%`` against ``APP_OWNER``, where every unit
    test for the empty case was green: they asserted no suite row was present and
    never looked at what the empty table renders as.
    """
    if not changes:
        # The section still owes the blank line a table would have left behind
        # it, or the next header lands one line high and reads as part of this
        # section. Measured live: the empty case printed
        # `COVERAGE CHANGED SINCE LAST RUN:` with `SUMMARY PER SUITE:` directly
        # under its rule.
        print()
        return
    print_adt_table(
        [
            {
                "SUITE_PACKAGE" : change.suite,
                "WAS"           : percent_cell(change.was),
                "NOW"           : percent_cell(change.now),
                "DELTA"         : delta_cell(change.delta),
            }
            for change in changes
        ],
        columns = list(_CHANGES_COLUMNS),
        numeric = _CHANGES_COLUMNS[1:],
    )


def delta_cell(delta: float | None) -> str:
    """A signed change, or blank when there is nothing to compare against.

    Signed always, ``+2.1`` as well as ``-0.4``: the sign is the whole message,
    and an unsigned gain beside a signed drop reads as two different kinds of
    number.
    """
    if delta is None:
        return ""
    return f"{delta:+.1f}"


__all__ = [name for name in globals() if not name.startswith("_")]
