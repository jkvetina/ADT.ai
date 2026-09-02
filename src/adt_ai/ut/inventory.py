"""What the ut module discovered and what came back from running it.

Three record types, in the order the command prints them: a ``SuiteTest`` is one
``%test`` annotation, a ``SuitePackage`` is one ``_UT`` package with the tests it
holds (and, when it holds none that can run, the reason it was skipped), and a
``TestOutcome`` is one test's verdict after the suite ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

# Skip reasons, in the order they are checked. **Neither reaches the console.**
# A `_UT` package that did not compile and a `_UT` package utPLSQL never parsed
# are both "not a runnable suite", and Jan's standing instruction is that `ut`
# ignores those completely, no table row, no problem stanza, no effect on the
# verdict. They stay named rather than collapsing to a bare `runnable` flag
# because `-debug` and any future diagnostic still needs to say which it was.
SKIP_INVALID = "INVALID"
SKIP_NOT_A_SUITE = "NOT A SUITE"

# The four status words, and they are the *printed* words: one constant is both
# the header of a `SUMMARY PER SUITE:` verdict column and the value in a `TEST RESULTS:`
# row, so a row can never read one spelling under a header that reads another.
# Jan's 2026-08-11 call is the short form everywhere. A failed expectation and a
# raised exception stay counted apart, but all four fit the same fixed-width
# status column, and the short word is what the reader is scanning for.
RESULT_PASSED = "PASS"
RESULT_FAILED = "FAIL"
RESULT_ERRORED = "ERROR"
RESULT_SKIPPED = "SKIP"


@dataclass(frozen=True)
class SuiteTest:
    package         : str
    name            : str
    description     : str = ""
    line            : int | None = None
    path            : str = ""
    disabled        : bool = False
    disabled_reason : str = ""


@dataclass(frozen=True)
class SuitePackage:
    """One test package, with the tests it holds and the module it belongs to.

    ``target`` and ``module`` are ``ut_match``'s and ``ut_module``'s first
    capture groups, both extracted by Oracle in the discovery query rather than
    re-derived here, one regex engine, evaluating each expression once, against
    the row it also selected.

    Either can be empty: an expression that does not parse this name yields
    nothing, and ``module`` is empty for every package when a project has turned
    the roll-ups off. The roll-up tells those apart by its own switch, never by
    looking for a blank here.
    """

    name        : str
    status      : str = "VALID"
    description : str = ""
    tests       : tuple[SuiteTest, ...] = field(default_factory=tuple)
    skip_reason : str = ""
    target      : str = ""
    module      : str = ""

    @property
    def runnable(self) -> bool:
        return not self.skip_reason


@dataclass(frozen=True)
class TestOutcome:
    package : str
    test    : str
    result  : str
    seconds : float | None = None
    message : str = ""

    @property
    def ok(self) -> bool:
        return self.result in {RESULT_PASSED, RESULT_SKIPPED}


@dataclass(frozen=True)
class SuiteTiming:
    """How long one suite took, in seconds, the `SUMMARY PER SUITE:` `TIMER` column.

    **Wall clock around the `ut.run` call, not the sum of the reporter's own
    per-test `time` attributes.** The question the column answers is which suite
    makes a run slow, and what makes a suite slow is as often its `%beforeall`,
    its teardown and the round trip as the assertions themselves. Adding up
    `TestOutcome.seconds` would leave all of that out, so the column would not
    account for the run the reader just sat through, and a timing column that
    does not add up is worse than none.
    """

    package : str
    seconds : float


@dataclass(frozen=True)
class PackageCoverage:
    """One package under test, with what the run measured about it.

    There is one of these per package a discovered suite **tests**, resolved
    through ``ut_match``, and none for anything else in the schema. Coverage is
    run-scoped since card `#291`: the figure describes the code this run
    exercised, not the schema it ran in.

    ``lines`` is the package body's own line count. It is the only field that
    says anything about scale, and it is what ``coverage_percent`` weighs a group
    by: a measured 40-line package and an unmeasured 4000-line one do not
    contribute equally to the module row above them.

    ``blocks_total`` already excludes ``not_feasible`` blocks, the exclusion
    happens in SQL, so every consumer of this record sees the same denominator.

    ``type`` is the unit's coverage spelling (one of utPLSQL's five source
    types), and it carries the class beyond the package bodies its name is
    written for (card `#648`). It defaults to ``PACKAGE BODY`` because every
    record on the rendered path is one, and it is part of the key rather than a
    label: triggers occupy their own Oracle namespace, so ``AUDIT_ROW`` can be a
    trigger and a procedure in one schema, and a lookup by name alone would
    collapse the two into whichever row was read last.
    """

    name           : str
    lines          : int = 0
    blocks_total   : int = 0
    blocks_covered : int = 0
    type           : str = "PACKAGE BODY"

    @property
    def measured(self) -> bool:
        """Did Oracle actually collect anything for this package?

        A package with no blocks was either never entered or could not be
        instrumented, natively compiled code carries no instrumentation at all.
        Both are an absent measurement, and an absent measurement renders blank.
        """
        return self.blocks_total > 0

    @property
    def percent(self) -> float | None:
        # None, never 0.0, when nothing was measured: a percentage is a claim
        # about collected data, and there is none. The renderer decides what an
        # absent figure looks like.
        if not self.measured:
            return None
        return _percent(self.blocks_covered, self.blocks_total)


@dataclass(frozen=True)
class CoverageReport:
    """What the run measured, keyed so a suite's row can find its own figure.

    There is no ``covered``/``uncovered`` split any more: the two tables that
    split on it went with `-coverage` (card `#291`), and a run-scoped report has
    nothing to split, every package in it is one a suite tests.

    An empty report is the honest state for a run that measured nothing, and it
    renders as blank `COVERAGE` cells rather than as a missing column.

    **``objects`` is the other four source types, and nothing renders it**
    (card `#648`). utPLSQL instruments type bodies, procedures, functions and
    triggers alongside package bodies, so the run measures them whether or not
    anything asks; keeping them here rather than in ``packages`` is what makes
    that free. Every consumer (each summary row, the module roll-up, the run
    history) reads ``packages`` and treats its members as the packages the
    run's suites test, so one trigger folded in there would move a printed
    percentage with no test failing.
    """

    packages : tuple[PackageCoverage, ...] = field(default_factory=tuple)
    objects  : tuple[PackageCoverage, ...] = field(default_factory=tuple)

    def for_package(self, name: str) -> PackageCoverage | None:
        """The figure for one package under test, or None when there is none.

        Looked up case-insensitively: the name arrives from ``ut_match``'s
        capture group against a dictionary row, and the report is keyed on the
        dictionary's own spelling.
        """
        if not name:
            return None
        wanted = name.upper()
        for package in self.packages:
            if package.name.upper() == wanted:
                return package
        return None


def coverage_percent(packages: tuple[PackageCoverage, ...] | list[PackageCoverage]) -> float | None:
    """How much of a set of packages is covered, the whole set, not its measured part.

    Shared by every `SUMMARY PER MODULE:` group row and by the total under them, so a group
    and the total can never be two calculations that drift apart.

    **Two halves, because code no test enters is invisible to Oracle.** Block
    coverage only records units something ran, so a package a suite is supposed
    to test but never reaches produces no `dbmspcc` rows at all and cannot
    contribute a denominator. Pooling the measured blocks alone therefore
    answered "how well is the reached code reached", which is not the question a
    module row is read for: Jan's 2026-08-09 run printed `COM 3 1639 21 88.0`
    where `ICT_COM_INVOICE` is 224 well-tested lines and the group's other 1415
    lines had never executed (card `#250`).

    So the pooled block figure is scaled by **reach**, the share of the set's
    body lines Oracle measured at all. `LINES` is the only size every package
    has. A set every package of which was measured has reach 1 and is unchanged,
    so this can only ever move a figure that was over-claiming.

    Two answers that are not percentages of measured blocks:

    * **``0.0`` when nothing in the set was measured but there is code in it.**
      Unreached code is 0% covered, and the blank a `None` renders as reads
      "not measured" in the one column a reader scans for what needs attention.
    * **``None`` only when the set holds no code at all**, which is the one case
      with genuinely nothing to be a percentage of.

    The measured set is everything Oracle collected blocks for, including a
    package it measured at a real zero, a package instrumented and never
    entered belongs on both sides of the fraction, not dropped from it.
    """
    measured = [package for package in packages if package.measured]
    blocks_total = sum(package.blocks_total for package in measured)
    lines_total = sum(package.lines for package in packages)
    if not blocks_total:
        return 0.0 if lines_total else None
    blocks_covered = sum(package.blocks_covered for package in measured)
    if not lines_total:
        return _percent(blocks_covered, blocks_total)
    return _reach_percent(
        blocks_covered,
        blocks_total,
        sum(package.lines for package in measured),
        lines_total,
    )


def _reach_percent(covered: int, total: int, lines_measured: int, lines_total: int) -> float:
    """The pooled block figure, scaled by the share of lines it was measured over.

    One expression rather than two roundings: quantizing the block ratio first
    and then scaling it would round twice and disagree with the same figure
    computed by hand. With ``lines_measured == lines_total`` this is exactly
    `_percent`, which is what keeps a fully measured set unchanged.
    """
    ratio = (
        Decimal(covered) * 100 * Decimal(lines_measured)
        / (Decimal(total) * Decimal(lines_total))
    )
    return float(ratio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _percent(covered: int, total: int) -> float:
    """Half rounds up, not to even, and the ratio never touches a binary float.

    `round()` would report 17 of 32 blocks as 53.12% where Oracle's own ROUND,
    and the reader checking the division by hand, says 53.13, and an exact half
    is precisely where someone bothers to check.
    """
    ratio = Decimal(covered) * 100 / Decimal(total)
    return float(ratio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


__all__ = [name for name in globals() if not name.startswith("__")]
