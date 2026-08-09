"""Console rendering for the ut3 module.

Four sections, in the order a run produces them: the suites that matched, the
per-test verdicts as each suite finishes, the detail for anything that did not
pass, and the per-suite tally.

Two shapes carry that. The suites roll-up and the tally are tables because
every value in them is bounded — a package name and counts. The verdicts are
fixed-width status rows grouped under their package, because a run prints one
block per suite as that suite completes and a table cannot be emitted in
pieces. Everything free-text a run produces — a failure message, an ORA stack —
is a per-record stanza: status-led heading first, wrapped text beneath it, never
a table column that would be sized to its widest sentence.

A test package that cannot run — INVALID, or holding no parsed `%test` — is in
none of the four sections. It is not a suite, and `ut3` reports suites.

`-coverage`'s own sections live in `render_coverage.py`; what both modules share
is how a single cell looks, which is `cells.py`.
"""

from __future__ import annotations

import textwrap

from adt_ai.export_db.runner import print_adt_header, print_adt_table
from adt_ai.shared.progress import FixedWidthProgressPrinter
from adt_ai.ut3.cells import SUMMARY_NUMERIC, count_cell, module_cell, seconds_cell
from adt_ai.ut3.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    SuitePackage,
    TestOutcome,
)
from adt_ai.ut3.render_coverage import print_coverage_header
from adt_ai.ut3.runner import Ut3Reporter, Ut3Result

_SUITE_COLUMNS = ("SUITE_PACKAGE", "TESTS")
_SUMMARY_COLUMNS = ("SUITE_PACKAGE", "PASSED", "FAILED", "ERRORED", "TIMER")

# `SUMMARY:` again, one row per `ut_module` group instead of per suite package.
# `PACKAGES` is the only column the per-suite table does not have, and it is
# there because a group of one and a group of nine are the same row without it.
_MODULE_COLUMNS = ("MODULE_NAME", "PACKAGES", "PASSED", "FAILED", "ERRORED", "TIMER")

# One grid for both content sections. `TEST RESULTS:` and `ERRORS & FAILURES:`
# have the same shape — a heading naming what follows, then its detail — so a
# package heading sits where a stanza heading sits and a test row sits where a
# wrapped message line sits. Four spaces against two: the detail reads as
# nested under its heading without starting a third of a tab-stop in.
_HEADING_INDENT = "  "
_DETAIL_INDENT = "    "

# The message body wraps inside the terminal; only an unbreakable token (an
# ORA stack line, a path) is allowed to overhang.
_MESSAGE_WIDTH = 78


def print_suites(packages: tuple[SuitePackage, ...]) -> None:
    """The matched suites, one row each, printed before anything runs.

    **The header and the column row always print, even with nothing to list.**
    An empty table is a complete answer — it says the command looked and found
    no suite — and it says it in the same shape as a run that found ten. The
    section this replaced printed a four-line troubleshooting lecture instead,
    which buried the one fact the reader wanted behind advice they had not
    asked for. The failure is still carried, by the exit code and by `SUMMARY:`.

    **Only runnable suites are listed.** A package that is INVALID, or that
    utPLSQL parsed no test for, contributes no row and no stanza anywhere else
    either: Jan's instruction is that `ut3` ignores it. It has no test count, so
    a table of suites and their counts has nothing to say about it, and naming it
    below under a heading that reads `ERRORS & FAILURES:` claims a failure the
    command has decided not to have.

    Packages print in the dictionary's A-Z order, which the discovery query
    already imposes.
    """
    print_adt_header("UNIT TESTS SUITES:")
    print_adt_table(
        [
            {
                "SUITE_PACKAGE" : package.name,
                "TESTS"         : len(package.tests),
            }
            for package in packages
            if package.runnable
        ],
        columns=list(_SUITE_COLUMNS),
    )


def print_results_header() -> None:
    # The blank belongs to the header, not to the first package block: a suite
    # block is opened by its own name and closed by a blank line, so the section
    # is spaced like every table without the first block being a special case.
    print_adt_header("TEST RESULTS:")
    print()


def print_package_heading(package: SuitePackage) -> None:
    """The package name alone, flushed, before that suite's work starts.

    Only the heading can be streamed ahead of the work — utPLSQL runs a whole
    suite in one `ut.run` call, so no individual test result exists until the
    call returns. Printing the heading first is what puts the visible pause on
    the suite being run instead of on the section header above it.
    """
    print(f"{_HEADING_INDENT}{package.name}", flush=True)


def print_package_results(package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
    """One block per suite: the package heading, then a row per test."""
    print_package_heading(package)
    print_test_rows(outcomes)


def print_test_rows(outcomes: tuple[TestOutcome, ...]) -> None:
    """The one row builder both the streamed and the batch render go through."""
    printer = FixedWidthProgressPrinter(indent=_DETAIL_INDENT)
    for outcome in outcomes:
        # begin() lays the label down and status() completes that same row; the
        # pair is the only way to get a labelled fixed-width line.
        printer.begin(outcome.test)
        printer.status(outcome.test, outcome.result)
    print()


def print_results(result: Ut3Result) -> None:
    """Batch fallback for a run that never drove the streaming reporter.

    It renders through the same block and row builders, so the two paths are
    byte-for-byte identical; the console always streams, so this is what prints
    the section when nothing ran at all.
    """
    print_results_header()
    for package in result.packages:
        outcomes = tuple(
            outcome for outcome in result.outcomes if outcome.package == package.name
        )
        if outcomes:
            print_package_results(package, outcomes)


def print_problems(result: Ut3Result) -> None:
    """One stanza per non-passing test: what happened, then why.

    **The status leads the heading** — `ERROR > A_UT.TEST_LABELS#LOOKUP_RAISES`,
    not the name with a trailing verdict. The reader is scanning for the ones
    that errored, and a status word parked at the end of a package-qualified
    identifier is behind the longest, most variable part of the line.

    **Every stanza gets a blank line above it**, the first one included, so the
    section reads as a list of records rather than one wall of text under a
    header.

    A package that could not run is not here and is not anywhere: an INVALID or
    unparsed `_UT` package is ignored outright.
    """
    problems = [
        outcome
        for outcome in result.outcomes
        if outcome.result in {RESULT_FAILED, RESULT_ERRORED}
    ]
    if not problems:
        return
    print_adt_header("ERRORS & FAILURES:")
    for outcome in problems:
        print()
        print(f"{_HEADING_INDENT}{outcome.result} > {outcome.package}.{outcome.test}")
        for line in _wrapped_lines(outcome.message):
            print(line)
    print()


def print_summary(result: Ut3Result) -> None:
    """The suites table again, with what each suite's tests actually did.

    Same first column as `UNIT TESTS SUITES:` so the two read as before and
    after of one list, then a column per verdict. **A zero renders blank**: a
    column of `0`s competes for the eye with the counts that matter, and the
    thing a reader scans this table for is the row that is not all-passed.

    **There is no `TESTS` column.** Every test lands in exactly one verdict, so
    the total was a fourth number derivable from the other three — and the count
    the reader wanted was already on the `UNIT TESTS SUITES:` row above. The one
    thing it carried alone was the `%disabled` test, which appears in no verdict
    column; its own result row still reads `SKIPPED`.

    **`TIMER` closes the row** with that suite's own seconds, which is what turns
    the table from a tally into something that explains a slow run. It is the one
    column a zero does not blank out of — see `seconds_cell`.
    """
    print_adt_header("SUMMARY:")
    print_adt_table(
        [
            {
                "SUITE_PACKAGE" : package.name,
                "PASSED"        : count_cell(_count(outcomes, RESULT_PASSED)),
                "FAILED"        : count_cell(_count(outcomes, RESULT_FAILED)),
                "ERRORED"       : count_cell(_count(outcomes, RESULT_ERRORED)),
                "TIMER"         : seconds_cell(result.seconds_for(package.name)),
            }
            for package, outcomes in _outcomes_by_package(result)
        ],
        columns = list(_SUMMARY_COLUMNS),
        numeric = SUMMARY_NUMERIC,
    )


def print_module_summary(result: Ut3Result) -> None:
    """`SUMMARY:` again, grouped by `ut_module` — the second table of a run.

    **It answers a different question from the table above it.** `SUMMARY:` says
    which suite is red; this says which *area* is. On a schema with ninety suites
    the per-suite table is a list you scroll and this is the one you read, which
    is why it is a table of its own rather than a sort order on the first.

    `PACKAGES` is the group size, and it is the column that makes the rest
    honest: four failures spread over nine suites and four in one are not the
    same news.

    **The last row is the whole run, with a blank module name.** A `TOTAL` label
    would be a value in a column of module names — it would sort among them and
    read as one — so the total is placed rather than labelled. A suite whose name
    `ut_module` cannot parse groups at the top, and reads `?` rather than blank:
    two unnamed rows in one table say nothing about which is which, which is the
    defect card `#248` fixed in `-coverage`'s table and this one alike.

    Prints only when `ut_module` is configured. A project without one sees the
    output it saw before this existed, not a table of empty groups.
    """
    grouped = _by_module(_outcomes_by_package(result), result)
    print_adt_header("MODULES:")
    print_adt_table(
        [
            {
                "MODULE_NAME" : module_cell(module),
                "PACKAGES"    : len(packages),
                "PASSED"      : count_cell(sum(row["passed"] for row in packages)),
                "FAILED"      : count_cell(sum(row["failed"] for row in packages)),
                "ERRORED"     : count_cell(sum(row["errored"] for row in packages)),
                "TIMER"       : seconds_cell(_total_seconds(packages)),
            }
            for module, packages in grouped
        ]
        + [
            {
                "MODULE_NAME" : "",
                "PACKAGES"    : sum(len(packages) for _, packages in grouped),
                "PASSED"      : count_cell(_across(grouped, "passed")),
                "FAILED"      : count_cell(_across(grouped, "failed")),
                "ERRORED"     : count_cell(_across(grouped, "errored")),
                "TIMER"       : seconds_cell(
                    _total_seconds([row for _, rows in grouped for row in rows])
                ),
            }
        ],
        columns = list(_MODULE_COLUMNS),
        numeric = SUMMARY_NUMERIC,
    )


def _by_module(
    grouped: list[tuple[SuitePackage, tuple[TestOutcome, ...]]],
    result: Ut3Result,
) -> list[tuple[str, list[dict[str, object]]]]:
    """One entry per module, A-Z, each holding its suites' counted verdicts."""
    modules: dict[str, list[dict[str, object]]] = {}
    for package, outcomes in grouped:
        modules.setdefault(package.module, []).append(
            {
                "passed"  : _count(outcomes, RESULT_PASSED),
                "failed"  : _count(outcomes, RESULT_FAILED),
                "errored" : _count(outcomes, RESULT_ERRORED),
                "seconds" : result.seconds_for(package.name),
            }
        )
    return sorted(modules.items())


def _across(grouped: list[tuple[str, list[dict[str, object]]]], key: str) -> int:
    return sum(row[key] for _, rows in grouped for row in rows)


def _total_seconds(packages: list[dict[str, object]]) -> float | None:
    """The group's own seconds, or None when not one suite in it ever ran.

    A group of skipped suites has nothing to report, which `seconds_cell` then
    blanks — the same rule a single suite's cell follows.
    """
    measured = [row["seconds"] for row in packages if row["seconds"] is not None]
    return sum(measured) if measured else None


def _outcomes_by_package(
    result: Ut3Result,
) -> list[tuple[SuitePackage, tuple[TestOutcome, ...]]]:
    grouped = []
    for package in result.packages:
        if not package.runnable:
            continue
        outcomes = tuple(
            outcome for outcome in result.outcomes if outcome.package == package.name
        )
        grouped.append((package, outcomes))
    return grouped


def _count(outcomes: tuple[TestOutcome, ...], result: str) -> int:
    return sum(1 for outcome in outcomes if outcome.result == result)



class ConsoleUt3Reporter(Ut3Reporter):
    """Prints the results as the run proceeds, never as one dump at the end.

    The suites roll-up lands the moment discovery returns; then each package's
    heading lands before that suite blocks and its test rows once the verdict is
    known. A reader watching a slow run sees the suite currently being executed,
    not a silent terminal followed by everything at once.

    `-coverage` keeps the same principle with nothing left to stream: the run is
    deliberately quiet there, so what lands at discovery is the section header
    for the report that run is producing.
    """

    def __init__(self, silent: bool = False, coverage: bool = False) -> None:
        self._silent = silent
        self._coverage = coverage
        self.streamed = False

    def discovered(self, packages: tuple[SuitePackage, ...]) -> None:
        if self._coverage:
            print_coverage_header()
            return
        if self._silent:
            return
        print_suites(packages)

    def suite_begin(self, package: SuitePackage) -> None:
        if self._silent:
            return
        if not self.streamed:
            print_results_header()
            self.streamed = True
        print_package_heading(package)

    def suite_end(self, package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
        if self._silent:
            return
        print_test_rows(outcomes)


def _wrapped_lines(message: str) -> list[str]:
    if not message:
        return []
    lines: list[str] = []
    for paragraph in message.splitlines():
        if not paragraph.strip():
            continue
        lines.extend(
            textwrap.wrap(
                paragraph.strip(),
                width              = _MESSAGE_WIDTH,
                initial_indent     = _DETAIL_INDENT,
                subsequent_indent  = _DETAIL_INDENT,
                break_long_words   = False,
                break_on_hyphens   = False,
            )
            or [f"{_DETAIL_INDENT}{paragraph.strip()}"]
        )
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
