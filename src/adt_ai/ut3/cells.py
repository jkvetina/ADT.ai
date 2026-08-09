"""How a single ut3 table cell renders.

Four rules, each of which two sections now have to agree on: the plain run's
tables and `-coverage`'s live in separate modules since the naming configuration
pushed ``render.py`` past the repo's 20 KB context budget, and a count that
blanks in one and prints `0` in the other would be a visible inconsistency in
the same report.
"""

from __future__ import annotations


def count_cell(count: int) -> object:
    """A zero renders blank.

    A column of `0`s competes for the eye with the counts that matter, and what
    a reader scans these tables for is the row that is *not* all-passed.
    """
    return count or ""


def seconds_cell(seconds: float | None) -> str:
    """One decimal, always present — and `0.0` is a figure, not a blank.

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
# and a word like `UNKNOWN` would be the widest cell in it — and a `?` is not a
# module a reader could mistake for one, which `n/a` or `-` are closer to being.
UNNAMED_MODULE = "?"


def module_cell(module: str) -> str:
    """A group name, and a marker where the convention could not read one.

    **Both module tables end on a total row whose name is deliberately blank** —
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


def coverage_cell(package) -> str:
    """Only ever called for a package with a real figure.

    The `-` and `NATIVE` cases this used to carry are gone with the column they
    lived in: a package with no measurement is in the other table now, where the
    absence *is* the row rather than something a cell has to spell.

    **One decimal place, always present, and no `%`.** The header names the unit
    once, so repeating it on every row bought nothing and cost a character of
    width on each. Variable precision cost more than that: stripping the
    trailing zeros ended `100`, `75` and `53.1` at three different offsets, so
    even flush right the figures did not stack under each other — and a column
    read by scanning down it for the low numbers is exactly the column that has
    to stack.
    """
    return f"{package.percent:.1f}"


# COVERAGE is a number and lines up on its units digit like one. Detection reads
# the cells with `isnumeric()`, which rejects the decimal point, so `75.0` is no
# more numeric to the sniffer than `75%` was — the column stays declared. `TIMER`
# is declared for exactly the same reason: `3.0` sniffs as text.
COVERAGE_NUMERIC = ("COVERAGE",)
SUMMARY_NUMERIC = ("TIMER",)


__all__ = [name for name in globals() if not name.startswith("__")]
