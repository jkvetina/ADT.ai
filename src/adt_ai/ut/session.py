"""What a `ut` run is ASKED for, what it ANSWERS with, and what it announces.

Split out of ``runner.py`` when `#436` pushed that module past the repo's 20 KB
per-file context budget, along the seam the module already had: three records
that describe a run, against the machinery that performs one. Nothing in here
reaches the database or knows how a suite is executed, which is what makes the
split hold rather than move the problem.

``Ut3Reporter`` sits with them rather than with the runner because it is the same
kind of thing, the shape of a run rather than the doing of one, and because the
console reporter in ``ut/reporter.py`` subclasses it: leaving it in ``runner``
made every reporter import the runner to get at a base class of no-ops.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adt_ai.ut.inventory import (
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    RESULT_SKIPPED,
    CoverageReport,
    SuitePackage,
    SuiteTiming,
    TestOutcome,
)
from adt_ai.ut.naming import UtNaming


@dataclass(frozen=True)
class Ut3Request:
    """One ut run.

    ``owner`` is the schema under test. Which schema holds its test packages is
    ``naming.owner_for(owner)``, the same value by default, and a different one
    when ``ut_owner`` is configured. The two are kept apart everywhere below:
    suites are discovered and executed in the test schema, coverage is measured
    in the schema under test.
    """

    owner    : str
    names    : tuple[str, ...] = ()
    refresh  : bool = False
    naming   : UtNaming = field(default_factory=UtNaming)


@dataclass(frozen=True)
class Ut3Result:
    packages : tuple[SuitePackage, ...] = field(default_factory=tuple)
    outcomes : tuple[TestOutcome, ...] = field(default_factory=tuple)
    # Always a report, never None: every run measures coverage since card `#291`,
    # so "was it requested" is no longer a question a caller can ask. An empty
    # report means the run measured nothing, and renders as blank cells.
    coverage : CoverageReport = field(default_factory=CoverageReport)
    timings  : tuple[SuiteTiming, ...] = field(default_factory=tuple)
    # `ut_module` is configured, so the renderer should print the module
    # roll-up. A switch rather than a derived "does anything carry a module",
    # for the reason `CoverageReport.modules` gives.
    modules  : bool = False

    def seconds_for(self, package: str) -> float | None:
        """How long that suite took, or None if it never ran.

        None rather than 0.0 for the absent case: a suite that was skipped and a
        suite that finished instantly are different facts, and the renderer is
        the one that decides how each looks.
        """
        for timing in self.timings:
            if timing.package == package:
                return timing.seconds
        return None

    @property
    def passed(self) -> int:
        return self._count(RESULT_PASSED)

    @property
    def failed(self) -> int:
        return self._count(RESULT_FAILED)

    @property
    def errored(self) -> int:
        return self._count(RESULT_ERRORED)

    @property
    def skipped_tests(self) -> int:
        return self._count(RESULT_SKIPPED)

    @property
    def tests_run(self) -> int:
        return len(self.outcomes)

    @property
    def success(self) -> bool:
        # A zero-test run is a failure, never an empty pass, see the module
        # docstring. A package that could not run is **not** counted here: Jan's
        # instruction is that `ut` ignores a `_UT` package that is INVALID or
        # holds no parsed test, and a command that ignores something cannot also
        # fail the run over it. A schema whose every `_UT` package is unrunnable
        # still fails, but through the empty-outcomes clause above.
        if not self.outcomes:
            return False
        return not (self.failed or self.errored)

    def _count(self, result: str) -> int:
        return sum(1 for outcome in self.outcomes if outcome.result == result)


class Ut3Reporter:
    """Streaming hooks so the console can print a phase's label before it blocks.

    The no-op base keeps non-console callers (and every test that does not care)
    unchanged; the CLI swaps in a console reporter.

    **Every hook that opens something has a partner that closes it**, and the
    pairs exist because what sits between them blocks. A run reaches the
    database in four waits, the annotation cache rebuild, discovery, each suite,
    and the coverage measurement, and the reader is entitled to know which one
    they are in. `#359` was the coverage one: it spent 7.4 seconds of a 14
    second run behind the last test row with nothing on screen at all.
    """

    def refreshing(self, owner: str) -> None:
        """`-refresh` only: utPLSQL is about to reparse the schema's annotations.

        The slowest thing this command can be asked to do, and opt-in, so it
        owns a section a normal run never prints.
        """
        return None

    def refreshed(self) -> None:
        return None

    def discovering(self, owner: str) -> None:
        """The dictionary and the annotation cache are about to be read.

        The one wait that is the same in every mode, and each mode announces it
        with the header it was going to print anyway: `UNIT TESTS SUITES:` under
        `-verbose`, `RUNNING TESTS FOR <PATTERNS>:` by default. Neither needs
        anything discovery returns (only the rows under them do), so the header
        leads and the table or the bar fills in behind it (`#379`).
        """
        return None

    def measuring_coverage(self, result: Ut3Result) -> None:
        """The suites are done and the run is about to read what they reached.

        It carries the finished run because the console's answer is to lay down
        everything it can already render, the problem stanzas and the
        `SUMMARY PER SUITE:` header, so the read happens under the header of the
        table it fills. Until `#379` the bar was simply left open through it,
        which is a row reading `100%  0:00:00` standing in for a wait that had
        not started: 9.9 seconds of a 19.3 second run.
        """
        return None

    def coverage_measured(
        self,
        coverage: CoverageReport,
        packages: tuple[SuitePackage, ...] = (),
    ) -> None:
        """The profiler has been read, and the pairings are final.

        ``packages`` carries the suites with their targets as
        :func:`~adt_ai.ut.coverage.resolve_targets` settled them, which is later
        than :meth:`discovered` could know: `ut_match` derives a name and only
        the schema's package list can say whether that name exists (`#436`).
        """
        return None

    def discovered(self, packages: tuple[SuitePackage, ...]) -> None:
        """Every matched suite, before the first one runs.

        The listing has to reach the terminal *before* execution starts, not
        after: a suite run is the slow part, and a reader who is shown what will
        run only once it has finished was never shown it at all.
        """
        return None

    def suite_begin(self, package: SuitePackage) -> None:
        return None

    def suite_end(self, package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
        return None

__all__ = [name for name in globals() if not name.startswith("_")]
