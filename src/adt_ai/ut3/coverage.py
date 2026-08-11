"""Assembling the coverage figures out of what a run measured.

Split from ``runner.py`` when the naming configuration pushed that module past
the repo's 20 KB per-file context budget, along the seam the ``queries/`` folder
already uses: ``suites`` is discovering and executing, ``coverage`` is measuring.
The runner still owns the session — ``coverage_start`` and ``coverage_stop`` are
its ``try``/``finally``, because instrumentation lives on the connection — and
calls in here once the suites have finished.

**Coverage is run-scoped** since card `#291` (Jan's call): the report holds one
record per package a discovered suite tests, and nothing else. Until then it
listed every package in the schema, so that the removed ``NO CODE COVERAGE:``
table could be a work list of untested code. With the figure folded into the
run's own two tables there is no row for an untested package to sit on — every
row of `SUMMARY:` and `MODULES:` is a suite that ran — so listing the rest of
the schema would fetch rows nothing can render.
"""

from __future__ import annotations

from adt_ai.shared.db import QueryGateway
from adt_ai.ut3 import queries
from adt_ai.ut3.inventory import CoverageReport, PackageCoverage, SuitePackage


def build_coverage_report(
    gateway: QueryGateway,
    owner: str,
    coverage_run_id: str,
    packages: tuple[SuitePackage, ...],
    naming_pattern: str,
) -> CoverageReport:
    """What the run measured about the packages its suites test.

    ``owner`` is the schema under test, never ``ut_owner``. Coverage measures the
    code, and which schema holds the tests is a different question — reading the
    test schema here would report the coverage of the test packages.

    The selection is the run's own ``ut_match`` pairings, so ``-name`` needs no
    separate handling: it already chose the suites, and the targets follow.

    The dictionary listing leads and the coverage rows are joined onto it, never
    the other way round. Coverage data only describes packages something
    executed, so a coverage-first build would silently drop a target no test
    reached — and a target that was supposed to be reached and was not is exactly
    what a `0.0` in the column has to be able to say.
    """
    targets = _targets(packages)
    if not targets:
        return CoverageReport()

    measured = {
        str(row.get("PACKAGE_NAME") or "").upper(): row
        for row in gateway.fetch_all(
            queries.PACKAGE_COVERAGE_QUERY,
            {"coverage_run_id": coverage_run_id, "owner": owner},
        )
    }

    listed = []
    # The query excludes the test packages by the same `ut_pattern` that selects
    # them — a suite whose `ut_match` pairs it to another suite would otherwise
    # report the coverage of test code — and it does so in SQL, because a
    # schema's whole package list is exactly the fetch that exclusion avoids.
    # Narrowing to the targets themselves happens here rather than in a generated
    # IN-list: the row count is a schema's packages, not its rows, and a bind
    # list built per run cannot be a stored constant the way every other
    # statement in `queries/` is.
    for row in gateway.fetch_all(
        queries.SCHEMA_PACKAGES_QUERY,
        {"owner": owner, "ut_pattern": naming_pattern},
    ):
        name = str(row.get("OBJECT_NAME") or "")
        if name.upper() not in targets:
            continue
        found = measured.get(name.upper(), {})
        listed.append(
            PackageCoverage(
                name           = name,
                lines          = int(row.get("LINES") or 0),
                blocks_total   = int(found.get("BLOCKS_TOTAL") or 0),
                blocks_covered = int(found.get("BLOCKS_COVERED") or 0),
            )
        )
    return CoverageReport(packages=tuple(listed))


def _targets(packages: tuple[SuitePackage, ...]) -> set[str]:
    """The packages the run's suites test, upper-cased for dictionary matching.

    **Runnable suites only.** A `_UT` package that is INVALID or that utPLSQL
    parsed no `%test` for never executes, so it can have contributed no block to
    anything; `ut3` ignores it everywhere else and a coverage row for its target
    would be the one place it reappeared, carrying a figure some other suite
    earned.

    A suite `ut_match` could not pair has an empty target and contributes
    nothing — the same honest silence its `COVERAGE` cell prints.
    """
    return {
        package.target.upper()
        for package in packages
        if package.runnable and package.target
    }


__all__ = [name for name in globals() if not name.startswith("__")]
