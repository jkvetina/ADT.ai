"""The run-wide roll-ups: `SUMMARY PER MODULE:`, and `-compact`'s one row.

Split from ``render.py`` when the compact report pushed that module past the
repo's 20 KB per-file context budget, the same cap that put ``cells.py``,
``grouping.py``, ``problems.py`` and ``changes.py`` beside it. The seam is the
one the two shapes here already share and nothing else in ``render`` does: both
are built by :func:`_module_row` over the whole run rather than over one suite,
so a change to what a total means lands in one file. ``render`` keeps the shapes
whose unit is a suite or a test.

**The row and the table are one calculation, not two.** `-compact` prints the
table's own last row with its verdict counts dropped, so the pair cannot report
two different figures for one run, which is the whole reason a reader can trust
the short form without opening the long one. `_module_row` builds a named group,
the unnamed total, and the compact row alike, exactly as ``coverage_percent``
one layer down builds a group's figure and the total's.
"""

from __future__ import annotations

from adt_ai.ut.cells import (
    SUMMARY_NUMERIC,
    count_cell,
    module_cell,
    percent_cell,
    seconds_cell,
    status_cell,
)
from adt_ai.ut.grouping import (
    ModuleRow,
    by_module,
    flatten,
    outcomes_by_package,
    target_packages,
    total_seconds,
)
from adt_ai.ut.inventory import coverage_percent

# Through ``render`` rather than straight from ``export_db.runner``, the edge
# ``changes.py`` already borrows for the same reason: this module is imported by
# ``reporter`` and by the CLI, and a second edge into the export_db package from
# here re-enters ``adt_ai.cli`` while it is still initialising. One module owns
# that dependency.
from adt_ai.ut.render import print_adt_header, print_adt_table
from adt_ai.ut.runner import Ut3Result

MODULE_SUMMARY_TITLE = "SUMMARY PER MODULE:"

# **`-compact` replaces the pair of summaries with one heading, not with a third
# one beside them.** `SUMMARY PER SUITE:` and `SUMMARY PER MODULE:` name what
# they group because a reader has both on screen and has to tell them apart;
# this one has nothing to be told apart from, so it names what it carries: the
# run's score. Jan, 2026-08-27, naming it himself.
COMPACT_TITLE = "RESULTS:"

# `SUMMARY PER SUITE:` again, one row per `ut_module` group instead of per suite
# package. `PACKAGES` and `LINES` are the two columns the per-suite table does
# not have, and they are the pair that makes the rest honest: a group of one and
# a group of nine are the same row without the count, and forty lines and four
# thousand are the same row without the size. They sit together, ahead of the
# verdicts, because both describe the group rather than what this run did to it.
_MODULE_COLUMNS = (
    "MODULE_NAME",
    "PACKAGES",
    "LINES",
    "PASS",
    "FAIL",
    "ERROR",
    "TIMER",
    "COVERAGE",
)


# The total row with its verdict counts dropped and one word in their place.
# `PACKAGES` and `LINES` say how big the run was, `TIMER` what it cost,
# `COVERAGE` how much of it was reached, and `STATUS` whether it is green: the
# same questions the wide tables answer, over the run instead of over a group.
_COMPACT_COLUMNS = ("PACKAGES", "LINES", "TIMER", "COVERAGE", "STATUS")


def print_module_summary(result: Ut3Result) -> None:
    """The same run grouped by `ut_module`, the second table of a run.

    **It answers a different question from the table above it.** The per-suite
    table says which suite is red; this says which *area* is. On a schema with
    ninety suites the per-suite table is a list you scroll and this is the one
    you read, which is why it is a table of its own rather than a sort order on
    the first. The two headings are a matched pair for that reason, `SUMMARY PER
    SUITE:` then `SUMMARY PER MODULE:`, so the difference between them is the
    first thing on each screen rather than something to work out from a column.

    `PACKAGES` is the group size and `LINES` is how much code that adds up to,
    and together they are what make the rest honest: four failures spread over
    nine suites and four in one are not the same news, and neither are ninety
    percent of forty lines and ninety percent of four thousand.

    **`LINES` counts the packages the group's suites test, not the suites.** It
    is the same deduplicated set `COVERAGE` beside it is computed over, two
    suites testing one package contribute one body once, so the two columns can
    never describe two different sets. A group whose suites pair to nothing has
    no lines to count and blanks, exactly where its `COVERAGE` blanks too.

    **The last row is the whole run, with a blank module name.** A `TOTAL` label
    would be a value in a column of module names, it would sort among them and
    read as one, so the total is placed rather than labelled. A suite whose name
    `ut_module` cannot parse groups at the top, and reads `?` rather than blank:
    two unnamed rows in one table say nothing about which is which, which is the
    defect card `#248` fixed.

    **`COVERAGE` is the group's own figure, over the packages its suites test.**
    Every column on the row describes one set of suites, `COVERAGE` included, so
    a group is never a mix of this run's verdicts and some wider schema's
    coverage. The one shared `coverage_percent` helper computes the groups and
    the total alike, so the total can never disagree with the rows above it, and
    a target Oracle measured nothing for still counts its body lines, which pulls
    the group down in proportion to how much of it went unreached.

    Prints only when `ut_module` is configured. A project without one sees the
    output it saw before this existed, not a table of empty groups.
    """
    grouped = by_module(outcomes_by_package(result), result)
    print_adt_header(MODULE_SUMMARY_TITLE)
    print_adt_table(
        [_module_row(module_cell(module), rows) for module, rows in grouped]
        + [_module_row("", flatten(grouped))],
        columns = list(_MODULE_COLUMNS),
        numeric = SUMMARY_NUMERIC,
    )


def print_compact_header() -> None:
    """`RESULTS:` alone, so it can lead the coverage read.

    Same split, and the same reason, as `print_summary_header`: the `COVERAGE`
    cell is what the run reads Oracle's profiler for, and `#379` put the heading
    of the table above that wait rather than leaving the cursor on a finished
    progress row. A mode that collapses the report does not get to un-announce
    the one wait the report is still paying for.
    """
    print_adt_header(COMPACT_TITLE)


def print_compact_row(result: Ut3Result, *, passed: bool) -> None:
    """The whole run as one row: `-compact`'s entire report.

    Jan, 2026-08-27: *"instead of printing (any) SUMMARY table, you will print
    just the overall score."* So both summaries go, and what stands in their
    place is the row the module table already ends on, which is the run.

    **It goes through `_module_row`, never a second sum of its own**, the
    argument the module docstring above makes. `LINES` and `COVERAGE` in
    particular are over the deduplicated target packages, so re-deriving them
    here would be the one place a package two suites test could be counted twice.

    **The grouping is not a condition on the row.** `SUMMARY PER MODULE:` prints
    only when `ut_module` is set, because a table of groups needs a convention
    that names them; a total over every suite needs none, so a project that
    blanked the expression still gets its `PACKAGES` and `LINES`.

    ``passed`` is the caller's exit-code expression, see `cells.status_cell`.
    """
    total = _module_row("", flatten(by_module(outcomes_by_package(result), result)))
    print_adt_table(
        [
            {
                "PACKAGES" : total["PACKAGES"],
                "LINES"    : total["LINES"],
                "TIMER"    : total["TIMER"],
                "COVERAGE" : total["COVERAGE"],
                "STATUS"   : status_cell(passed),
            }
        ],
        columns = list(_COMPACT_COLUMNS),
        numeric = SUMMARY_NUMERIC,
    )


def _module_row(name: str, rows: list[ModuleRow]) -> dict[str, object]:
    """One roll-up row: a named group, the unnamed total, or the compact score.

    All three go through one builder so the total can never be a different
    calculation from the rows it sits under, nor the compact row a third one
    again. That was already true of `COVERAGE`, which has always shared
    `coverage_percent`; writing the group and the total out twice is what let
    `LINES` be the next column with two ways to be right.

    Every caller takes the cells its own table declares, so the verdict counts
    are simply not read by the compact row rather than being built conditionally.
    """
    packages = target_packages(rows)
    return {
        "MODULE_NAME" : name,
        "PACKAGES"    : len(rows),
        "LINES"       : count_cell(sum(package.lines for package in packages)),
        "PASS"        : count_cell(sum(row["passed"] for row in rows)),
        "FAIL"        : count_cell(sum(row["failed"] for row in rows)),
        "ERROR"       : count_cell(sum(row["errored"] for row in rows)),
        "TIMER"       : seconds_cell(total_seconds(rows)),
        "COVERAGE"    : percent_cell(coverage_percent(packages)),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
