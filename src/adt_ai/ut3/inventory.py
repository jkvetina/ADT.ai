"""What the ut3 module discovered and what came back from running it.

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
# are both "not a runnable suite", and Jan's standing instruction is that `ut3`
# ignores those completely — no table row, no problem stanza, no effect on the
# verdict. They stay named rather than collapsing to a bare `runnable` flag
# because `-debug` and any future diagnostic still needs to say which it was.
SKIP_INVALID = "INVALID"
SKIP_NOT_A_SUITE = "NOT A SUITE"

RESULT_PASSED = "PASSED"
RESULT_FAILED = "FAILED"
# The status word a test row prints. A failed expectation and a raised exception
# are different things and stay counted apart, but both have to fit the same
# fixed-width status column, and `ERROR` is what the reader is looking for.
RESULT_ERRORED = "ERROR"
RESULT_SKIPPED = "SKIPPED"


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
    name        : str
    status      : str = "VALID"
    description : str = ""
    tests       : tuple[SuiteTest, ...] = field(default_factory=tuple)
    skip_reason : str = ""

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
    """How long one suite took, in seconds — the `SUMMARY:` `TIMER` column.

    **Wall clock around the `ut.run` call, not the sum of the reporter's own
    per-test `time` attributes.** The question the column answers is which suite
    makes a run slow, and what makes a suite slow is as often its `%beforeall`,
    its teardown and the round trip as the assertions themselves. Adding up
    `TestOutcome.seconds` would leave all of that out, so the column would not
    account for the run the reader just sat through — and a timing column that
    does not add up is worse than none.
    """

    package : str
    seconds : float


# Why Oracle collected nothing for a package. Native compilation strips the
# PL/SQL instrumentation block coverage reads, so the unit never reaches
# `dbmspcc_blocks` at all — and an unexplained absence is indistinguishable from
# genuinely untested code, which is why it is named rather than left as a bare
# `-`.
#
# **A reason explains an absent measurement; it never replaces a present one.**
# The runner attaches it only when nothing was collected.
#
# `PLSQL_OPTIMIZE_LEVEL > 1` sat beside it until 2026-08-06 and was removed
# because a live run disproved it: level 2 is Oracle's default, and block
# coverage is collected there regardless. The optimizer reshapes the *line* map a
# profiler reads, not the basic-block map this report is built on. Measured on
# `ICT_OWNER@ORCLPDB1` — all 78 package bodies at level 2, 36 of them recorded in
# `dbmspcc_blocks` — the flag fired on every package of every default database
# and not one percentage ever printed.
BLOCKED_NATIVE = "NATIVE"


@dataclass(frozen=True)
class PackageCoverage:
    """One package's line in the coverage report.

    ``lines`` is the package body's own line count — how much code the
    percentage beside it is a percentage of. It is the only field here that says
    anything about scale, and it is what makes a list of uncovered packages a
    priority order rather than an alphabet.

    ``passed``, ``failed`` and ``errored`` come from the package's ``_UT``
    partner **by name**, not from whatever executed it: attributing execution
    back to a specific test is not something block coverage records, so a package
    covered by somebody else's suite shows real blocks and no verdicts. There is
    deliberately no ``tests`` count — the three verdicts sum to it, and a fourth
    number derived from three already on the row is noise.

    ``blocks_total`` already excludes ``not_feasible`` blocks — the exclusion
    happens in SQL, so every consumer of this record sees the same denominator.

    ``lines_covered`` is the line half of that same block measurement: the
    distinct source lines carrying a covered block. **It is not a fraction of
    ``lines``** — that counts every row of the body, comments, blanks and
    declarations included, none of which Oracle instruments — so even a fully
    covered package reports fewer covered lines than it has lines. Where the
    report prints the two together it says so, rather than leaving a reader to
    divide them and conclude the figure is broken.
    """

    name           : str
    lines          : int = 0
    passed         : int = 0
    failed         : int = 0
    errored        : int = 0
    blocks_total   : int = 0
    blocks_covered : int = 0
    lines_covered  : int = 0
    blocked_reason : str = ""

    @property
    def has_coverage(self) -> bool:
        """Did anything actually execute this package? The report splits on this.

        Deliberately stricter than ``measured``: a package Oracle instrumented
        and nothing ever entered has a real, honest ``0%``, and a reader scanning
        for what still needs tests wants it beside the packages that were never
        measured at all. Both answer "is this covered" with no.
        """
        return self.measured and self.blocks_covered > 0

    @property
    def measured(self) -> bool:
        """Did Oracle actually collect anything for this package?

        A package with no blocks was either never executed or could not be
        instrumented. The two are different answers and the report must not
        print ``0%`` for both — ``blocked_reason`` separates them.

        A measured package never carries a ``blocked_reason``: the runner only
        attaches one where there is silence to explain.
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
    """Every package the report lists, in dictionary order, split two ways.

    The split is the report's shape: ``covered`` answers "how well is what we
    test actually tested", ``uncovered`` is the work list. ``packages`` keeps the
    whole thing in one order so a caller that wants the full picture is not
    reassembling it from halves.
    """

    packages : tuple[PackageCoverage, ...] = field(default_factory=tuple)

    @property
    def covered(self) -> tuple[PackageCoverage, ...]:
        return tuple(package for package in self.packages if package.has_coverage)

    @property
    def uncovered(self) -> tuple[PackageCoverage, ...]:
        return tuple(package for package in self.packages if not package.has_coverage)

    @property
    def blocked(self) -> tuple[PackageCoverage, ...]:
        return tuple(package for package in self.packages if package.blocked_reason)

    @property
    def percent(self) -> float | None:
        """The `covered` half aggregated — the roll-up's one percentage.

        **The denominator is the measured blocks and nothing wider.** A package
        Oracle never instrumented has no block count at all, so stretching the
        figure across the whole schema would mean inventing how much code sits
        in the packages that were never reached. How far the tests reach is a
        different question, and the roll-up answers it with the package and line
        counts beside this figure rather than by bending this one.
        """
        total = sum(package.blocks_total for package in self.covered)
        if not total:
            return None
        return _percent(sum(package.blocks_covered for package in self.covered), total)


def _percent(covered: int, total: int) -> float:
    """Half rounds up, not to even, and the ratio never touches a binary float.

    `round()` would report 17 of 32 blocks as 53.12% where Oracle's own ROUND —
    and the reader checking the division by hand — says 53.13, and an exact half
    is precisely where someone bothers to check.
    """
    ratio = Decimal(covered) * 100 / Decimal(total)
    return float(ratio.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


__all__ = [name for name in globals() if not name.startswith("__")]
