"""The streaming console reporter: what a `ut` run prints while it is running.

Split from ``render.py`` when the phase sections pushed that module past the
repo's 20 KB per-file context budget, along the seam the module had already
grown: ``render`` builds the report shapes out of a finished result and knows
nothing about time, this file is the one thing in ``ut`` that speaks while the
command is still working, and it owns every label that has to reach the terminal
before the call it describes.

**It opens no section of its own.** `#359` gave three waits a heading and a row
each, `REFRESHING THE ANNOTATION CACHE:`, `DISCOVERING SUITES:` and
`MEASURING COVERAGE:`, reasoning that a wait owning no row owns a section. Jan,
2026-08-16, reading two of them: *"WHAT IS THIS SHIT in UT MODULE? ... I DID
NOT FUCKING ASKED FOR EATHER OF THIS SHIT!"* He had asked for the cursor to stop
parking on a finished row, which is not the same as asking for three new
sections.

So every wait here sits behind a header the run was going to print anyway, moved
above its own first read:

============================ ==================================================
``-refresh``                 the run's own heading, opened early
discovery                    the same heading, or `UNIT TESTS SUITES:` verbose
each suite                   the package heading, or one bump of the dotted bar
coverage                     `SUMMARY PER SUITE:`, the table the read fills,
                             or `RESULTS:` under `-compact`
============================ ==================================================

**The last row is what `#379` changed, and it is why the report's front half is
rendered from here.** The bar used to be left open through the coverage read, on
the reasoning that an open line announces. It does, of the work that will close
it, and this row's work was over: Jan measured it sitting at `100%  0:00:00` for
9.9 seconds of a 19.3 second run, then printing header and table together. *"you
are waiting on the progress line and only after you fetch summaries you print
header and data."* So `measuring_coverage` closes the bar and lays down
everything the run can already say, the problem stanzas and the summary heading,
and the three round trips happen under the heading of the table they fill. What
is left for the caller is the rows.
"""

from __future__ import annotations

from adt_ai.ut.changes import print_coverage_changes_header, print_coverage_changes_rows
from adt_ai.ut.inventory import CoverageReport, SuitePackage, TestOutcome
from adt_ai.ut.problems import print_problems
from adt_ai.ut.progress import SuiteProgressBar, print_section_header
from adt_ai.ut.render import (
    print_package_heading,
    print_results,
    print_results_header,
    print_suites_header,
    print_suites_rows,
    print_summary_header,
    print_test_rows,
)
from adt_ai.ut.rollup import print_compact_header
from adt_ai.ut.runner import Ut3Reporter, Ut3Result
from adt_ai.ut.store import (
    RunSnapshot,
    baseline_percents,
    coverage_changes,
    measured_percents,
)


class ConsoleUt3Reporter(Ut3Reporter):
    """Prints the run as it proceeds, never as one dump at the end.

    Discovery is where the modes part, and each owns everything it prints from
    its own heading to the report:

    * default   , `RUNNING TESTS:`, one dotted bar bumped by finished suites,
      under a heading that names the `-name` patterns when there are any.
    * ``verbose``, `UNIT TESTS SUITES:` the moment discovery returns, then
      `TEST RESULTS:`, a package heading before each suite blocks and a row per
      test once its verdict is known.
    * ``silent`` , neither listing, and no bar.

    **The suites roll-up is verbose output, not chrome** (Jan, card `#348`). It
    answers "what is about to run", and the bar's own label already carries that
    in one line, so on a schema of ninety suites the table was ninety rows
    between the connection block and the report.

    `-silent` outranks `-verbose` for the reason it outranked `-dense`: two flags
    about one region of the screen, and the one that removes it wins, or the
    quiet flag would print a section it exists to suppress.
    """

    def __init__(
        self,
        silent: bool = False,
        verbose: bool = False,
        *,
        compact: bool = False,
        names: tuple[str, ...] = (),
        previous_seconds: float = 0.0,
        started_at: float | None = None,
        error_limit: int | None = None,
        history: tuple[RunSnapshot, ...] = (),
    ) -> None:
        self._silent = silent
        self._verbose = verbose
        # `-compact` owns the REPORT region, where `-silent` and `-verbose` own
        # the run's own. Two different halves of the screen, so the three
        # compose instead of one outranking another: `-silent -compact` is a run
        # that says nothing until it says one row.
        self._compact = compact
        # `#251`: what earlier runs measured, read from `config/internal/ut.db`
        # BEFORE this run records itself. Empty on a first run, on a fresh root,
        # and whenever the store could not be read, and each of those renders the
        # same way: no change table, because there is nothing to have changed from.
        #
        # **Every recorded run, not the newest one** (`#436`). The baseline is the
        # newest run whose figures DIFFER from this one's, and which run that is
        # cannot be known until the profiler has been read, so the choosing
        # happens in `coverage_measured` and this holds the candidates.
        self._history = tuple(history)
        self._packages: tuple[SuitePackage, ...] = ()
        # Set when the change table's header, rather than `SUMMARY PER SUITE:`,
        # is the one standing above the coverage read.
        self._changes_lead = False
        # The `-name` patterns reach the reporter for one reason: the run's
        # section heading names them, see `progress.section_title`.
        self._names = tuple(names)
        self._previous = previous_seconds
        self._started_at = started_at
        # `ut_limit_errors`, carried because the problem stanzas print from here
        # now: they sit between the last suite and the summary heading, and that
        # heading has to be on screen before the coverage read.
        self._error_limit = error_limit
        self._bar: SuiteProgressBar | None = None
        # The mode's own heading, printed once by whichever wait comes first.
        # `-refresh` runs before discovery, so neither hook can own it alone.
        self._section_open = False
        # `-verbose` only: a suite block whose rows have printed and whose
        # closing blank has not. That blank is what ends the package heading's
        # claim on the screen, and it is paid at the next heading or at `close`.
        self._block_open = False
        self.streamed = False

    def refreshing(self, owner: str) -> None:
        """`-refresh` only, and the first thing a run carrying it does."""
        self._open_section()

    def refreshed(self) -> None:
        """Silent, like its partner."""

    def discovering(self, owner: str) -> None:
        """The dictionary and the annotation cache, under the mode's heading."""
        self._open_section()

    def _open_section(self) -> None:
        """The mode's first header, once, ahead of the first thing that blocks.

        Idempotent because either wait can be the first: `-refresh` reparses the
        annotations and only then does discovery read them, and the reader needs
        the heading for whichever of the two they are actually sitting through.
        """
        if self._silent or self._section_open:
            return
        self._section_open = True
        if self._verbose:
            print_suites_header()
        else:
            print_section_header(self._names)

    def measuring_coverage(self, result: Ut3Result) -> None:
        """Close the run's section and open the report's, before the read.

        Everything down to the `COVERAGE` column is knowable the moment the last
        suite returns, so all of it prints here and the three round trips that
        end and read Oracle's profiler happen under `SUMMARY PER SUITE:`.
        """
        self.close()
        if self._verbose and not self._silent and not self.streamed:
            # Nothing ran, so nothing streamed. The section still prints, empty:
            # a run that found no suite reports it in the same shape as one that
            # did, and the exit code carries the failure.
            print_results(result)
        # `-silent` never takes out the problem stanzas: it exists to make a
        # green run quiet, not to make a red one unreadable, and a `FAIL` count
        # whose message is reachable only by re-running is not a report.
        print_problems(result, limit=self._error_limit)
        # **Whichever table comes first is the one that leads the coverage read.**
        # `#379` put a header above that wait rather than leaving the cursor on a
        # finished progress row, and `#251` adds a table above the summaries under
        # `-verbose`; the announcement is a property of the wait, so it moves to
        # whatever now prints first instead of being duplicated.
        self._changes_lead = self._reports_changes()
        if self._changes_lead:
            print_coverage_changes_header()
        elif self._compact:
            print_compact_header()
        else:
            print_summary_header()

    def coverage_measured(
        self,
        coverage: CoverageReport,
        packages: tuple[SuitePackage, ...] = (),
    ) -> None:
        """The change table, when this run leads with one, then the summary header.

        The rows can only be built once the profiler has been read, so they land
        here while their header went up before the wait.

        ``packages`` supersedes what :meth:`discovered` stored: the suite-to-
        package pairing is `ut_match`'s guess until the schema's own package list
        resolves it, and that happens during the coverage build (`#436`). A row
        keyed on the unresolved name would look its figure up under a name the
        report is not keyed by, which is the blank cell this card exists to fix.
        """
        if packages:
            self._packages = packages
        if not self._changes_lead:
            return
        # **The baseline is chosen here, not when the reporter was built.** It is
        # the newest recorded run whose figures differ from the ones just
        # measured, and the ones just measured are what this call is delivering
        # (`#436`).
        baseline = baseline_percents(self._history, measured_percents(coverage.packages))
        # **No baseline is no rows, never an empty baseline.** Handing
        # `coverage_changes` an empty mapping reports every package as appearing
        # for the first time, which is the right answer for a genuinely fresh
        # schema and exactly the wrong one for a schema whose coverage has simply
        # not moved since it was last measured.
        print_coverage_changes_rows(
            ()
            if baseline is None
            else coverage_changes(baseline, self._packages, coverage.packages)
        )
        print_summary_header()

    def _reports_changes(self) -> bool:
        """Does this run print the change table at all?

        Three conditions, and the third is the one that keeps the table honest:
        `-verbose` asked for it, `-silent` did not veto it, and there is an
        earlier run of the same selection to compare against. A first run on a
        schema has no comparison, and a table of nothing above two full summaries
        would be chrome.

        **Whether anything MOVED is not one of the three.** That is only knowable
        once the profiler has been read, and the heading has to be on screen
        before that read to announce it (`#379`), so a run with history prints
        the heading and then prints however many rows the comparison produced.

        `-compact` is a fourth veto, and it is the one place that flag does
        outrank `-verbose`: the change table is per-package detail inside the
        region `-compact` collapses to a single row, so printing it there would
        be the mode reporting more than the report it replaced.
        """
        return bool(
            self._verbose and not self._silent and not self._compact and self._history
        )

    def discovered(self, packages: tuple[SuitePackage, ...]) -> None:
        # Kept for the change table: a row is per suite, and the suite-to-package
        # pairing is `ut_match`'s, which only discovery has.
        self._packages = packages
        runnable = [package for package in packages if package.runnable]
        if self._silent:
            return
        if self._verbose:
            print_suites_rows(packages)
            return
        # **A run that matched nothing gets no bar.** A bar over zero units would
        # have to print some percentage beside no work, and every value it could
        # print is a claim rather than a report. The heading above it stays: it
        # says what was looked for, under the filter that was used, and the empty
        # `SUMMARY PER SUITE:` below says what was found.
        if not runnable:
            return
        # The bar moves in suites and is labelled in tests, and both totals are
        # read off the discovery that just returned, so `-name` narrows the label
        # and the axis together.
        self._bar = SuiteProgressBar(
            len(runnable),
            tests            = sum(len(package.tests) for package in runnable),
            names            = self._names,
            previous_seconds = self._previous,
            started_at       = self._started_at,
        )
        self._bar.begin()
        self.streamed = True

    def suite_begin(self, package: SuitePackage) -> None:
        # Nothing to stream ahead of the work in bar mode: the bar is already on
        # screen at 0%, and it moves on completion, not on start.
        if self._silent or not self._verbose:
            return
        if not self.streamed:
            print_results_header()
            self.streamed = True
        if self._block_open:
            # The blank the previous block owes, paid here instead of when that
            # block's last row printed, see `close`.
            print()
            self._block_open = False
        print_package_heading(package)

    def suite_end(self, package: SuitePackage, outcomes: tuple[TestOutcome, ...]) -> None:
        if self._silent:
            return
        if self._bar is not None:
            self._bar.advance()
            return
        # **Only `-verbose` gets this far**, and the guard that said so was a
        # branch nothing could take (`#670`). `discovered` opens a bar for every
        # non-silent, non-verbose run that has a runnable suite, and a run with
        # no runnable suite never reaches a suite's end, so "no bar, not
        # verbose" is not a state this method can be called in.
        #
        # `package.tests` rides along so a `%disabled` test's reason prints under
        # its own SKIP row, the same block the batch render produces. The two
        # paths stay byte-for-byte identical.
        print_test_rows(outcomes, close_block=False, tests=package.tests)
        self._block_open = True

    def close(self) -> None:
        """End the bar, once, when the suites are done.

        It is the one thing that can still be open: the row is rewritten with a
        carriage return and carries no newline until something ends it. That
        ending is owed the moment the last suite returns rather than after the
        coverage read, because the row measures suites and a row that has
        measured all of them is a result (`#379`). A per-test block needs
        nothing, it closes itself with its own blank line.

        A no-op on a run that drew no bar, which is what lets the error path call
        it too, and idempotent, which is what lets the caller call it after
        `measuring_coverage` already has.
        """
        if self._block_open:
            print()
            self._block_open = False
        if self._bar is None:
            return
        self._bar.close()
        self._bar = None


__all__ = [name for name in globals() if not name.startswith("__")]
