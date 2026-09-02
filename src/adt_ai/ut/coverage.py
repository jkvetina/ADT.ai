"""Assembling the coverage figures out of what a run measured.

Split from ``runner.py`` when the naming configuration pushed that module past
the repo's 20 KB per-file context budget, along the seam the ``queries/`` folder
already uses: ``suites`` is discovering and executing, ``coverage`` is measuring.
The runner still owns the session, ``coverage_start`` and ``coverage_stop`` are
its ``try``/``finally``, because instrumentation lives on the connection, and
calls in here once the suites have finished.

**Coverage is run-scoped** since card `#291` (Jan's call): the report holds one
record per package a discovered suite tests, and nothing else. Until then it
listed every package in the schema, so that the removed ``NO CODE COVERAGE:``
table could be a work list of untested code. With the figure folded into the
run's own two tables there is no row for an untested package to sit on, every
row of `SUMMARY PER SUITE:` and `SUMMARY PER MODULE:` is a suite that ran, so listing the rest of
the schema would fetch rows nothing can render.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from adt_ai.shared.db import QueryGateway
from adt_ai.ut import queries
from adt_ai.ut.inventory import CoverageReport, PackageCoverage, SuitePackage

#: The one source type the console renders.
#:
#: utPLSQL instruments five and card `#648` collects all five, but every consumer
#: of ``CoverageReport.packages`` (each summary row, the module roll-up, the run
#: history) reads its members as the packages the run's suites test. So this
#: constant is the seam: package bodies on the rendered side, the other four in
#: ``CoverageReport.objects``, which nothing renders yet.
PACKAGE_BODY = "PACKAGE BODY"


@dataclass(frozen=True)
class CoverageOutcome:
    """The report, and the suites with their targets as finally resolved.

    Two values because ``ut_match``'s answer is only a guess about a name until
    the schema's own package list is in hand, and that list arrives here
    (`#436`). The suites travel back so the row that renders a suite's
    `COVERAGE` cell looks the figure up under the same name the report is keyed
    by.
    """

    report   : CoverageReport
    packages : tuple[SuitePackage, ...]


def build_coverage_report(
    gateway: QueryGateway,
    owner: str,
    coverage_run_id: str,
    packages: tuple[SuitePackage, ...],
    naming_pattern: str,
) -> CoverageOutcome:
    """What the run measured about the packages its suites test.

    ``owner`` is the schema under test, never ``ut_owner``. Coverage measures the
    code, and which schema holds the tests is a different question, reading the
    test schema here would report the coverage of the test packages.

    The selection is the run's own ``ut_match`` pairings, so ``-name`` needs no
    separate handling: it already chose the suites, and the targets follow.

    The dictionary listing leads and the coverage rows are joined onto it, never
    the other way round. Coverage data only describes packages something
    executed, so a coverage-first build would silently drop a target no test
    reached, and a target that was supposed to be reached and was not is exactly
    what a `0.0` in the column has to be able to say.

    **The listing is also what settles whether a pairing is real** (`#436`). One
    regular expression cannot know which of a schema's names exist, so
    ``ut_match`` yields a name that may be no package at all, and a suite in that
    state used to contribute no row and print a cell no reader could tell from an
    unmeasured one. The derived name is resolved against the schema's own
    packages before anything is listed; see :func:`resolve_targets`.
    """
    # **The guard is "did this run measure anything", not "did a suite pair".**
    # It tested the target set until card `#648`, which was the same question
    # while package bodies were the only thing collected: no target, no row, so
    # two round trips bought nothing. The other four source types belong to no
    # suite's target, so that test now suppresses exactly the data the widened
    # read exists to gather. A schema whose suites all pair to nothing (the
    # `ut` fixture is one) still runs code, still instruments its triggers, and
    # would report none of it. This is the runner's own `measures` condition, so
    # a run that started no coverage session still reads nothing back.
    if not any(package.runnable for package in packages):
        return CoverageOutcome(CoverageReport(), packages)

    # Keyed by type AND name since card `#648`, because the name alone stopped
    # being unique the moment the read widened past package bodies: triggers have
    # their own Oracle namespace, so `AUDIT_ROW` can be a trigger and a procedure
    # in one schema and a name-keyed dictionary would keep whichever came last.
    measured = {
        _key(row): row
        for row in gateway.fetch_all(
            queries.PACKAGE_COVERAGE_QUERY,
            {"coverage_run_id": coverage_run_id, "owner": owner},
        )
    }
    # The query excludes the test packages by the same `ut_pattern` that selects
    # them, a suite whose `ut_match` pairs it to another suite would otherwise
    # report the coverage of test code, and it does so in SQL, because a
    # schema's whole package list is exactly the fetch that exclusion avoids.
    # Narrowing to the targets themselves happens here rather than in a generated
    # IN-list: the row count is a schema's packages, not its rows, and a bind
    # list built per run cannot be a stored constant the way every other
    # statement in `queries/` is.
    schema_rows = list(
        gateway.fetch_all(
            queries.SCHEMA_PACKAGES_QUERY,
            {"owner": owner, "ut_pattern": naming_pattern},
        )
    )
    # **Package bodies only settle a suite's target.** The listing now carries the
    # other four source types too, and a suite's `ut_match` name resolves against
    # packages; letting a trigger into this set would let a suite pair to one.
    packages = resolve_targets(
        packages,
        {
            str(row.get("OBJECT_NAME") or "").upper()
            for row in schema_rows
            if _type(row) == PACKAGE_BODY
        },
    )
    targets = _targets(packages)

    listed  : list[PackageCoverage] = []
    objects : list[PackageCoverage] = []
    for row in schema_rows:
        name = str(row.get("OBJECT_NAME") or "")
        object_type = _type(row)
        # **The two tuples part here, and only here.** A package body the run's
        # suites test is rendered; everything else is collected and rendered by
        # nothing, so an untested package still drops out exactly as it did
        # before card `#648` while a trigger never has to earn a target at all.
        if object_type == PACKAGE_BODY:
            if name.upper() not in targets:
                continue
            destination = listed
        else:
            destination = objects
        found = measured.get((object_type, name.upper()), {})
        destination.append(
            PackageCoverage(
                name           = name,
                lines          = int(row.get("LINES") or 0),
                blocks_total   = int(found.get("BLOCKS_TOTAL") or 0),
                blocks_covered = int(found.get("BLOCKS_COVERED") or 0),
                type           = object_type,
            )
        )
    report = CoverageReport(packages=tuple(listed), objects=tuple(objects))
    return CoverageOutcome(report, packages)


def resolve_targets(
    packages: tuple[SuitePackage, ...],
    known: set[str],
) -> tuple[SuitePackage, ...]:
    """Each suite's `ut_match` name, resolved against the packages that exist.

    **A suite is often named for what it tests ABOUT a package, not for the
    package.** `ict_int_ariba_pushback_ut` derives `ICT_INT_ARIBA_PUSHBACK`,
    which is no package in `ICT_OWNER`; the code it exercises is
    `ict_int_ariba`. Four more suites in that one schema are the same shape, and
    every one of them ran green while its blocks were collected by the profiler
    and dropped by the report (`#436`). So a name that resolves to nothing falls
    back to the longest package it is a prefix of, which is a name the schema
    itself supplied rather than a guess.

    **Longest first, and it stops on the first hit.** A suite whose derived name
    IS a package keeps it, so nothing that already paired can be re-pointed, and
    a schema holding both `ICT_INT_ARIBA` and `ICT_INT` credits an ARIBA suite to
    ARIBA rather than to whichever name happens to be shorter.

    **Nothing resolves, nothing is invented.** `ict_int_fusion_ariba_ut` walks
    `ICT_INT_FUSION_ARIBA`, `ICT_INT_FUSION`, `ICT_INT` and finds none of them;
    its target stays empty and `cells.coverage_cell` marks the row unpaired.
    Attaching it to some near name would put a figure another suite earned beside
    a suite that did not earn it, which is worse than saying nothing.
    """
    return tuple(
        replace(package, target=_resolve(package.target, known))
        if package.target
        else package
        for package in packages
    )


def _resolve(target: str, known: set[str]) -> str:
    """The derived name, or the longest existing package it prefixes, or blank."""
    candidate = target.upper()
    while candidate:
        if candidate in known:
            return candidate
        head, separator, _ = candidate.rpartition("_")
        if not separator:
            return ""
        candidate = head
    return ""


def _type(row: dict) -> str:
    """A row's source type, in the one spelling everything downstream uses.

    Both queries map ``ALL_OBJECTS``' ``PACKAGE``/``TYPE`` onto ``PACKAGE BODY``
    and ``TYPE BODY`` in SQL, so the dictionary listing and the coverage read
    agree by construction and nothing here translates between two vocabularies.

    The fallback is for a row that predates the column. Every fake-gateway
    fixture written before card `#648` spells a package as ``OBJECT_NAME`` alone,
    and a package body is what each of them means. A live row always carries it.
    """
    return str(row.get("OBJECT_TYPE") or PACKAGE_BODY)


def _key(row: dict) -> tuple[str, str]:
    """The ``(type, name)`` a measured row is filed under, name upper-cased."""
    return _type(row), str(row.get("OBJECT_NAME") or "").upper()


def _targets(packages: tuple[SuitePackage, ...]) -> set[str]:
    """The packages the run's suites test, upper-cased for dictionary matching.

    **Runnable suites only.** A `_UT` package that is INVALID or that utPLSQL
    parsed no `%test` for never executes, so it can have contributed no block to
    anything; `ut` ignores it everywhere else and a coverage row for its target
    would be the one place it reappeared, carrying a figure some other suite
    earned.

    A suite `ut_match` could not pair has an empty target and contributes
    nothing, the same honest silence its `COVERAGE` cell prints.
    """
    return {
        package.target.upper()
        for package in packages
        if package.runnable and package.target
    }


__all__ = [name for name in globals() if not name.startswith("__")]
