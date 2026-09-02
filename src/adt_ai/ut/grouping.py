"""How a run's outcomes are grouped before anything is printed.

Split out of ``render.py`` when the `LINES` column and the errors cap pushed it
against the repo's 20 KB per-file context budget. The seam is a real one rather
than a size trick: everything here answers *which records belong together*, and
nothing in it knows what a table looks like.

One rule holds the module together. A group is described by the suites in it and
by the packages those suites test, and the two are different sets, several
suites can test one package, and a suite can test nothing the schema holds. Every
per-group figure therefore goes through the same pair of helpers, so a group's
`PACKAGES`, `LINES` and `COVERAGE` can never turn out to be three counts over
three different sets.
"""

from __future__ import annotations

from typing import TypedDict

from adt_ai.ut.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    PackageCoverage,
    SuitePackage,
    TestOutcome,
)
from adt_ai.ut.session import Ut3Result


# The row shape the module roll-up is built from: one entry per suite, carrying
# its counted verdicts, its seconds, and the package it tests. Built once here so
# a group and the total under it read the same records rather than looking each
# suite up again.
class ModuleRow(TypedDict):
    passed: int
    failed: int
    errored: int
    seconds: float | None
    #: The suite's TARGET package, `None` for a suite `ut_match` paired to
    #: nothing. Named on the row rather than looked up again in the renderer.
    coverage: PackageCoverage | None


def outcomes_by_package(
    result: Ut3Result,
) -> list[tuple[SuitePackage, tuple[TestOutcome, ...]]]:
    """Every runnable suite, with the outcomes that belong to it.

    A package that could not run (INVALID, or holding no parsed ``%test``) is
    left out here and is therefore absent from every table below: `ut` reports
    suites, and that is not one.
    """
    grouped: list[tuple[SuitePackage, tuple[TestOutcome, ...]]] = []
    for package in result.packages:
        if not package.runnable:
            continue
        outcomes = tuple(
            outcome for outcome in result.outcomes if outcome.package == package.name
        )
        grouped.append((package, outcomes))
    return grouped


def by_module(
    grouped: list[tuple[SuitePackage, tuple[TestOutcome, ...]]],
    result: Ut3Result,
) -> list[tuple[str, list[ModuleRow]]]:
    """One entry per module, A-Z, each holding its suites' counted verdicts.

    ``coverage`` on a row is the suite's **target** package, not the suite: it is
    the code the group's figures are about, and it is None for a suite
    ``ut_match`` could not pair. Carried here rather than looked up again in the
    renderer so the group and the total read the same records.
    """
    modules: dict[str, list[ModuleRow]] = {}
    for package, outcomes in grouped:
        modules.setdefault(package.module, []).append(
            {
                "passed"   : count(outcomes, RESULT_PASSED),
                "failed"   : count(outcomes, RESULT_FAILED),
                "errored"  : count(outcomes, RESULT_ERRORED),
                "seconds"  : result.seconds_for(package.name),
                "coverage" : result.coverage.for_package(package.target),
            }
        )
    return sorted(modules.items())


def target_packages(rows: list[ModuleRow]) -> list[PackageCoverage]:
    """The packages a set of suites tests, each counted once.

    **Deduplicated by name**, because two suites testing one package are two rows
    in `SUMMARY PER SUITE:` and one body of code here. Left as-is, that package's lines and
    blocks would be counted twice, `LINES` would over-report the group's size and
    `COVERAGE` would skew its reach scaling toward whichever package happens to
    carry the most suites.

    The name lies about nothing it used to: this returns every *paired* target,
    measured or not, which is exactly the set both `LINES` and `coverage_percent`
    want. Filter on ``.measured`` where only the measured half is meant.
    """
    unique: dict[str, PackageCoverage] = {}
    for row in rows:
        package = row["coverage"]
        if package is not None:
            unique.setdefault(package.name.upper(), package)
    return list(unique.values())


def flatten(grouped: list[tuple[str, list[ModuleRow]]]) -> list[ModuleRow]:
    """Every suite row in the run, module boundaries dropped.

    What the unnamed total row is computed over. It goes through the same
    `target_packages` and `total_seconds` as a group does, which is what keeps the
    total from ever disagreeing with the rows above it.
    """
    return [row for _, rows in grouped for row in rows]


def gated_packages(result: Ut3Result) -> tuple[PackageCoverage, ...]:
    """Every package this run's suites test, deduplicated, what `-gate` judges.

    The same set the `SUMMARY PER MODULE:` total is computed over, so a figure that gates is
    a figure the reader can find in the report above it. A suite that could not
    run contributes nothing, and a suite ``ut_match`` paired to nothing
    contributes nothing either: both are absent from `SUMMARY PER SUITE:` for the same
    reason, and a gate that judged what the report does not show would be
    unarguable.
    """
    return tuple(target_packages(flatten(by_module(outcomes_by_package(result), result))))


def total_seconds(rows: list[ModuleRow]) -> float | None:
    """The group's own seconds, or None when not one suite in it ever ran.

    A group of skipped suites has nothing to report, which ``seconds_cell`` then
    blanks, the same rule a single suite's cell follows.
    """
    measured = [seconds for row in rows if (seconds := row["seconds"]) is not None]
    return sum(measured) if measured else None


def count(outcomes: tuple[TestOutcome, ...], result: str) -> int:
    return sum(1 for outcome in outcomes if outcome.result == result)


__all__ = [name for name in globals() if not name.startswith("__")]
