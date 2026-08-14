"""How a single ut3 table cell renders.

Four rules, each of which the run's two tables have to agree on: `SUMMARY:` and
`MODULES:` carry the same columns (the verdicts, `TIMER`, `COVERAGE`) over
different groupings, and a count that blanks in one and prints `0` in the other
would be a visible inconsistency inside one report.

They stay out of ``render.py`` because that module is close to the repo's 20 KB
per-file context budget, and because a rendering rule is a fact about the value
rather than about the table it sits in.
"""

from __future__ import annotations

from adt_ai.ut3.inventory import RESULT_PASSED, PackageCoverage, TestOutcome


def count_cell(count: int) -> object:
    """A zero renders blank.

    A column of `0`s competes for the eye with the counts that matter, and what
    a reader scans these tables for is the row that is *not* all-passed.
    """
    return count or ""


def seconds_cell(seconds: float | None) -> str:
    """One decimal, always present, and `0.0` is a figure, not a blank.

    The verdict columns blank their zeros; a timing zero is the opposite. A
    suite that finished inside a tenth of a second *was* measured, and an empty
    cell would claim it was not. Only a suite that never ran has nothing to
    print.

    Fixed precision is what makes the column scannable, the same argument
    `COVERAGE` settled: with trailing zeros stripped, `3`, `0.3` and `12.5` end
    at three different offsets and the digits do not stack even flush right.
    """
    return "" if seconds is None else f"{seconds:.1f}"


def percent_cell(percent: float | None) -> str:
    # Same shape as a package row's own figure, so the roll-up stacks under the
    # column it summarises rather than reading as a different kind of number.
    return "" if percent is None else f"{percent:.1f}"


# What a `MODULE NAME` cell reads when `ut_module` parsed no module out of the
# name. A single character, because the column is a column of short identifiers
# and a word like `UNKNOWN` would be the widest cell in it, and a `?` is not a
# module a reader could mistake for one, which `n/a` or `-` are closer to being.
UNNAMED_MODULE = "?"


def module_cell(module: str) -> str:
    """A group name, and a marker where the convention could not read one.

    **Both module tables end on a total row whose name is deliberately blank**,
    Jan's shape: a `TOTAL` label would sort and read as a module. So a group with
    no name cannot also be blank, or the table prints two nameless rows and says
    nothing about which is which. That is exactly what `-coverage -name ICT%`
    printed on 2026-08-08 (card `#248`): an unattributed row of one package at the
    top and the 58-package total at the bottom, both unnamed.

    Only the group rows go through here. The total is blank **by construction**,
    passed as an empty label by the renderer rather than derived from a module,
    so nothing can accidentally mark it.
    """
    return module or UNNAMED_MODULE


def coverage_cell(package: PackageCoverage | None) -> str:
    """One suite row's `COVERAGE` figure, and a blank where there is none.

    **A blank means "no measurement", and there are two honest ways to get one.**
    The suite's own name may pair to nothing (`ut_match` found no capture group,
    or names a package the schema does not hold), or Oracle may have collected no
    block for the package it does pair to, natively compiled code carries no
    instrumentation at all. Neither is a zero: `0.0` is the answer for a package
    Oracle *did* instrument and nothing entered, which `percent` returns as a
    real figure.

    This is the one place the run's per-suite figure differs from a group's:
    `percent_cell` blanks only a `None` from `coverage_percent`, which reports an
    unmeasured group as `0.0` because a group has code in it whether or not any
    of it ran.

    **One decimal place, always present, and no `%`.** The header names the unit
    once, so repeating it on every row bought nothing and cost a character of
    width on each. Variable precision cost more: with trailing zeros stripped,
    `100`, `75` and `53.1` end at three different offsets, so even flush right
    the figures do not stack, and a column read by scanning down it for the low
    numbers is exactly the column that has to stack.
    """
    if package is None or package.percent is None:
        return ""
    return f"{package.percent:.1f}"


def test_status_cell(outcome: TestOutcome) -> str:
    """A `TEST RESULTS:` row's right-hand text: the timer on a pass, the word otherwise.

    Jan, 2026-08-13: a clean test's own elapsed seconds are worth more screen
    space than a status word that only ever says "yes", `PASS` names a fact
    the reader already knows from the row not carrying `FAIL`/`ERROR`/`SKIP`,
    while the timer is new information. A row that did not pass keeps its
    status word instead: `FAIL`/`ERROR`/`SKIP` are the fact the reader is
    scanning for, and a timer would bury it in a column of numbers.
    """
    if outcome.result != RESULT_PASSED:
        return outcome.result
    return seconds_cell(outcome.seconds)


# `TIMER` and `COVERAGE` are numbers and line up on their units digit like ones.
# Detection reads the cells with `isnumeric()`, which rejects the decimal point,
# so `75.0` is no more numeric to the sniffer than `75%` was, both columns stay
# declared. One tuple, because since card `#291` both tables carry both columns.
SUMMARY_NUMERIC = ("TIMER", "COVERAGE")


__all__ = [name for name in globals() if not name.startswith("__")]
