"""Assembling the `-coverage` report out of what a run measured.

Split from ``runner.py`` when the naming configuration pushed that module past
the repo's 20 KB per-file context budget, along the seam the ``queries/`` folder
already uses: ``suites`` is discovering and executing, ``coverage`` is measuring.
The runner still owns the session — ``coverage_start`` and ``coverage_stop`` are
its ``try``/``finally``, because instrumentation lives on the connection — and
calls in here once the suites have finished.
"""

from __future__ import annotations

from adt_ai.shared.db import QueryGateway
from adt_ai.shared.sql_like import matches_sql_like
from adt_ai.ut3 import queries
from adt_ai.ut3.inventory import (
    BLOCKED_NATIVE,
    RESULT_ERRORED,
    RESULT_FAILED,
    RESULT_PASSED,
    CoverageReport,
    PackageCoverage,
    SuitePackage,
    TestOutcome,
)
from adt_ai.ut3.naming import UtNaming


def build_coverage_report(
    gateway: QueryGateway,
    owner: str,
    coverage_run_id: str,
    packages: tuple[SuitePackage, ...],
    outcomes: tuple[TestOutcome, ...],
    names: tuple[str, ...],
    naming: UtNaming,
) -> CoverageReport:
    """Every package in the schema, with what the run measured about it.

    The schema listing leads and the coverage rows are joined onto it, never the
    other way round: coverage data only describes packages that were executed,
    so a coverage-first report silently omits the untested package the reader
    opened the report to find.

    ``owner`` is the schema under test, never ``ut_owner``. Coverage measures
    the code, and which schema holds the tests is a different question — reading
    the test schema here would report the coverage of the test packages.

    ``names`` filters this listing with the same patterns that already chose the
    suites in ``run`` — the flag means one thing in both modes, so the report
    lists the packages the reader named and the run executed the suites they
    named.
    """
    # Spelled per query rather than passed as one superset: Oracle rejects a bind
    # the statement does not declare, and only the listing derives a module.
    listing_binds = {
        "owner"      : owner,
        "ut_pattern" : naming.pattern,
        "ut_module"  : naming.module_bind,
    }
    settings_binds = {"owner": owner, "ut_pattern": naming.pattern}
    measured = {
        str(row.get("PACKAGE_NAME") or "").upper(): row
        for row in gateway.fetch_all(
            queries.PACKAGE_COVERAGE_QUERY,
            {"coverage_run_id": coverage_run_id, "owner": owner},
        )
    }
    blocked = _blocked_reasons(
        gateway.fetch_all(queries.PACKAGE_COMPILE_SETTINGS_QUERY, settings_binds)
    )
    verdicts = _verdicts_by_target(outcomes, packages)
    # The fallback, for a package whose own name yields no module: the module of
    # the suite that tests it, carried across the `ut_match` pairing Oracle
    # resolved at discovery. It is second, not first — see `_module_for`.
    paired = {
        package.target.upper(): package.module for package in packages if package.target
    }

    listed = []
    # The query excludes the test packages by the same `ut_pattern` that selects
    # them — they pair 1:1 with the code under test, so listing them doubles the
    # report — and it does so in SQL, because a schema's whole package list is
    # exactly the fetch that exclusion exists to avoid.
    for row in gateway.fetch_all(queries.SCHEMA_PACKAGES_QUERY, listing_binds):
        name = str(row.get("OBJECT_NAME") or "")
        if names and not any(matches_sql_like(name, pattern) for pattern in names):
            continue
        found = measured.get(name.upper(), {})
        verdict = verdicts.get(name.upper(), {})
        blocks_total = int(found.get("BLOCKS_TOTAL") or 0)
        listed.append(
            PackageCoverage(
                name           = name,
                lines          = int(row.get("LINES") or 0),
                passed         = verdict.get(RESULT_PASSED, 0),
                failed         = verdict.get(RESULT_FAILED, 0),
                errored        = verdict.get(RESULT_ERRORED, 0),
                blocks_total   = blocks_total,
                blocks_covered = int(found.get("BLOCKS_COVERED") or 0),
                lines_covered  = int(found.get("LINES_COVERED") or 0),
                # A reason explains silence. Oracle having collected blocks for
                # this package is the answer to "could it be measured", so a
                # prerequisite that predicts otherwise loses to the data rather
                # than overwriting it.
                blocked_reason = "" if blocks_total else blocked.get(name.upper(), ""),
                module         = _module_for(row, paired),
            )
        )
    return CoverageReport(packages=tuple(listed), modules=naming.modules_enabled)


def _module_for(row: dict[str, object], paired: dict[str, str]) -> str:
    """The package's own module, falling back to the module of its suite.

    **Its own name comes first, because the module is a property of the package.**
    The report used to read the module only across the `ut_match` pairing, so a
    package with no discovered suite carried no module at all — and `-name`
    filters the listing and the suites independently, so a filtered run put most
    of its packages in the blank group while every one of them spelled its module
    in its own name (card `#247`, from `-coverage -name ICT_INT%`).

    The pairing stays as the fallback rather than being replaced, for the project
    whose `ut_module` is anchored to its test-package suffix: that expression
    matches no package under test, so the pairing is the only module such a
    project has. Preferring the package's own name costs those projects nothing
    — the fallback answers exactly when the primary is blank.
    """
    return str(row.get("MODULE_NAME") or "") or paired.get(
        str(row.get("OBJECT_NAME") or "").upper(), ""
    )


def _verdicts_by_target(
    outcomes: tuple[TestOutcome, ...],
    packages: tuple[SuitePackage, ...],
) -> dict[str, dict[str, int]]:
    """``A_UT``'s verdicts are reported on package ``A``'s row.

    **This is a naming-convention lookup, not an execution fact.** Block coverage
    records which blocks ran, never which test ran them, so no data source can
    say "this test covered that package". A suite that exercises a package other
    than its namesake therefore contributes blocks to that package's coverage and
    no verdicts to its row. That asymmetry is honest — the blocks were measured,
    the attribution was inferred — and collapsing it would mean inventing an
    owner for every test.

    ``SKIPPED`` has no column, so a ``%disabled`` test lands in none of the
    three; its own result row in a plain run still reads ``SKIPPED``.
    """
    # `ut_match`'s capture group is the pairing, resolved by Oracle at discovery
    # and carried on the package, so a project using a prefix convention
    # attributes its verdicts exactly as a suffix one does. A suite the
    # expression could not pair has an empty target and contributes none —
    # the same honest silence a suite testing something other than its namesake
    # already has.
    targets = {package.name.upper(): package.target.upper() for package in packages}

    verdicts: dict[str, dict[str, int]] = {}
    for outcome in outcomes:
        target = targets.get(outcome.package.upper(), "")
        if not target:
            continue
        counts = verdicts.setdefault(target, {})
        counts[outcome.result] = counts.get(outcome.result, 0) + 1
    return verdicts


def _blocked_reasons(rows: list[dict[str, object]]) -> dict[str, str]:
    """Compile settings that explain why Oracle collected nothing for a package.

    Only ``PLSQL_CODE_TYPE = NATIVE`` qualifies: native compilation strips the
    instrumentation, so the unit produces no ``dbmspcc_blocks`` rows and the
    report would otherwise show a bare `-` indistinguishable from untested code.

    ``PLSQL_OPTIMIZE_LEVEL > 1`` was here until a live run disproved it — see
    ``inventory.BLOCKED_NATIVE``. The caller applies whatever comes back only to
    packages with no measurement, so this map can never suppress a real figure.
    """
    reasons: dict[str, str] = {}
    for row in rows:
        name = str(row.get("PACKAGE_NAME") or "").upper()
        if not name:
            continue
        if str(row.get("PLSQL_CODE_TYPE") or "").upper() == "NATIVE":
            reasons[name] = BLOCKED_NATIVE
    return reasons


__all__ = [name for name in globals() if not name.startswith("__")]
