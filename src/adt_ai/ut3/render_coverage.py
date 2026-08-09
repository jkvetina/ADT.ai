"""Console rendering for `-coverage`: the two report tables and the roll-up.

Split from ``render.py`` when the `ut_module` roll-up pushed that module past
the repo's 20 KB per-file context budget, along the seam the rest of the module
already uses — ``queries/`` and the runner both separate running the suites from
measuring the code. What the two rendering modules share is how a single cell
looks, and that lives in ``cells.py`` so neither owns it.

Section order is fixed by the reporter, not by this file: ``CODE COVERAGE:`` is
printed at discovery, before the silent run, so the terminal names the work it
is about to spend tens of seconds on.
"""

from __future__ import annotations

from adt_ai.export_db.runner import print_adt_header, print_adt_table
from adt_ai.ut3.cells import (
    COVERAGE_NUMERIC,
    count_cell,
    coverage_cell,
    module_cell,
    percent_cell,
)
from adt_ai.ut3.inventory import CoverageReport, PackageCoverage, coverage_percent

_COVERAGE_COLUMNS = ("PACKAGE", "LINES", "PASSED", "FAILED", "ERRORED", "COVERAGE")

# **The two tables do not carry the same columns, and that is the point.** The
# split is on "did anything execute this", so in the second half every column the
# split determines is constant: `COVERAGE` could only ever read `-`, and the
# verdicts could only ever describe tests that covered nothing. A constant column
# is not a column — it is a wide empty stripe between the two facts that do vary,
# which are which package and how much code is in it. Making the halves
# symmetrical was symmetry for its own sake.
_NO_COVERAGE_COLUMNS = ("PACKAGE", "LINES")

# The roll-up `-coverage` closes with: **one row, for the whole schema**, because
# it answers one question. It shipped as three — one per table above it plus a
# total — which made the reader re-read a breakdown they had just scrolled past
# to get at the single line they came for.
_COVERAGE_SUMMARY_COLUMNS = ("PACKAGES", "LINES", "COVERED", "COVERAGE")

# The same roll-up with its `ut_module` groups shown. Same four measurements —
# nothing about what they mean changes, they are computed per group — with the
# group's name in front and the whole schema as the last row.
_COVERAGE_MODULE_COLUMNS = ("MODULE_NAME", "PACKAGES", "LINES", "COVERED", "COVERAGE")


def print_coverage(report: CoverageReport) -> None:
    """Two tables: what the run covered, then what it did not.

    **The second table is the whole reason the section exists.** A report that
    only listed measured packages would omit exactly the package the reader
    opened it to find, and mixing the two into one list buries the gap among the
    rows that are fine. Splitting them makes `NO CODE COVERAGE:` a work list.

    A package lands in the second table when nothing executed it — whether
    Oracle measured it at `0%`, measured nothing at all, or could not instrument
    it at all.

    **The two tables do not carry the same columns.** Everything the split
    determines is constant in the second half, so what survives there is
    `PACKAGE` and `LINES`: which package, and how much code is sitting untested
    in it. The first half adds the verdicts and the figure, both of which need a
    run behind them to mean anything.

    `PASSED`, `FAILED` and `ERRORED` come from the package's test partner, paired
    by `ut_match`, so a package covered by another suite shows a real percentage
    and no verdicts — see `ut3.coverage._verdicts_by_target`.

    Packages print in the dictionary's A-Z order, which the query already
    imposes.

    **The `CODE COVERAGE:` header is not printed here.** It goes out the moment
    discovery returns — see `print_coverage_header` — because everything between
    that point and this call is a silent suite run.
    """
    print_adt_table(
        [
            {
                "PACKAGE"  : package.name,
                "LINES"    : package.lines or "",
                "PASSED"   : package.passed or "",
                "FAILED"   : package.failed or "",
                "ERRORED"  : package.errored or "",
                "COVERAGE" : coverage_cell(package),
            }
            for package in report.covered
        ],
        columns = list(_COVERAGE_COLUMNS),
        numeric = COVERAGE_NUMERIC,
    )
    print_adt_header("NO CODE COVERAGE:")
    print_adt_table(
        [
            {
                "PACKAGE" : package.name,
                "LINES"   : package.lines or "",
            }
            for package in report.uncovered
        ],
        columns = list(_NO_COVERAGE_COLUMNS),
    )


def print_coverage_header() -> None:
    """The section header, printed before the suites run rather than after.

    Under `-coverage` the run is silent by design — the report is the answer, and
    the per-suite chatter is what the flag was passed to remove. That left the
    connection block as the last thing on screen for the tens of seconds a real
    schema takes, which reads as a hang on connecting rather than as work in
    progress. Naming the section first puts the pause under the work it is being
    spent on.

    `print_adt_header` writes through the shared stdout tracker, which flushes
    the visible body of every write immediately, so nothing extra is needed to
    get this onto the terminal ahead of the blocking call.
    """
    print_adt_header("CODE COVERAGE:")


def print_coverage_summary(report: CoverageReport) -> None:
    """The roll-up that closes `-coverage`: the whole schema on one line.

    `PACKAGES` and `LINES` are the entire listing — both tables — so the row is
    the size of the codebase, not of its tested part; a reader who wants the
    split has the two tables immediately above. `COVERED` is the source lines
    that actually executed, and `COVERAGE` the same block figure the per-package
    column carries.

    **`COVERED` over `LINES` is deliberately not `COVERAGE`, and the three are
    labelled so nobody has to work that out by dividing.** They measure
    different things: `LINES` counts every row of every package body — comments,
    blanks and declarations, none of which Oracle instruments — `COVERED` counts
    instrumented lines that ran, and `COVERAGE` is covered blocks over measured
    blocks. Forcing them to agree would mean either dropping the line counts or
    computing a percentage against a denominator that includes code no coverage
    tool ever looks at.

    **`COVERAGE` blanks when nothing was measured**, exactly as a package row's
    does: the package and line counts are true whether or not a test ever ran,
    but a percentage is a claim about collected data. `COVERED` still reads `0`
    there — nothing executed is a measured zero, not an absent measurement.

    **With `ut_module` configured the one row becomes one row per module**, the
    group's name in front and the whole schema as the last row. It is the same
    table — Jan's instruction was to add the column and the total, not to add a
    second table, because this one already had the shape the module roll-up
    needs.
    """
    packages = report.packages
    print_adt_header("SUMMARY:")
    if report.modules:
        print_adt_table(
            [
                *(
                    _coverage_module_row(module_cell(module), rows)
                    for module, rows in _coverage_by_module(report)
                ),
                # The whole schema, in the row the table used to consist of. Its
                # blank name is passed here rather than derived, so the marker a
                # nameless *group* carries can never reach it.
                _coverage_module_row("", list(packages)),
            ],
            columns = list(_COVERAGE_MODULE_COLUMNS),
            numeric = COVERAGE_NUMERIC,
        )
        return
    print_adt_table(
        [
            {
                "PACKAGES" : count_cell(len(packages)),
                "LINES"    : count_cell(sum(package.lines for package in packages)),
                "COVERED"  : sum(package.lines_covered for package in packages),
                "COVERAGE" : percent_cell(report.percent),
            }
        ],
        columns = list(_COVERAGE_SUMMARY_COLUMNS),
        numeric = COVERAGE_NUMERIC,
    )


def _coverage_by_module(report: CoverageReport) -> list[tuple[str, list[PackageCoverage]]]:
    """The listed packages grouped by the module each one belongs to.

    A package whose own name carries no module token, and which no suite lends
    one to either, groups under an empty key rather than dropping out: the module
    rows have to add up to the total beneath them, and unattributed code is
    exactly what the reader of a coverage report is looking for. That group sorts
    first and the unnamed total is last; the renderer marks the group so the two
    are told apart by what they say, not by where they sit.

    That group used to hold every package with no *discovered suite*, which under
    `-name` was most of the listing — see `ut3.coverage._module_for`.
    """
    modules: dict[str, list[PackageCoverage]] = {}
    for package in report.packages:
        modules.setdefault(package.module, []).append(package)
    return sorted(modules.items())


def _coverage_module_row(label: str, packages: list[PackageCoverage]) -> dict[str, object]:
    """One group's four measurements, each meaning what it means schema-wide.

    ``label`` is the rendered cell, not the module: the caller has already put a
    group with no name through `module_cell` and left the total's own name blank,
    which is what keeps the two apart in a table that has one of each.

    `COVERAGE` stays covered blocks over **measured** blocks, so a package Oracle
    never instrumented adds to `PACKAGES` and `LINES` and to neither side of the
    percentage — the same rule that keeps the total row from inventing a
    denominator for code nothing reached. A group with no measurement at all
    blanks its figure and still prints its counts.
    """
    return {
        "MODULE_NAME" : label,
        "PACKAGES"    : count_cell(len(packages)),
        "LINES"       : count_cell(sum(package.lines for package in packages)),
        "COVERED"     : sum(package.lines_covered for package in packages),
        "COVERAGE"    : percent_cell(coverage_percent(packages)),
    }


__all__ = [name for name in globals() if not name.startswith("__")]
