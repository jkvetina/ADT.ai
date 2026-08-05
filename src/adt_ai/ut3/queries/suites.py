"""Discovery and execution SQL for the ut3 module.

Two independent sources answer two different questions, and the module needs
both:

* the **data dictionary** answers "which ``_UT`` packages exist, and did they
  compile" — the only place an INVALID test package is visible at all;
* **utPLSQL's own annotation cache** (``ut_runner.get_suites_info``) answers
  "which of them are suites, and what tests do they hold" — the only place the
  ``%suite`` / ``%test`` annotations have been parsed.

Reading only the dictionary cannot tell a suite from a helper package; reading
only utPLSQL cannot see a package that failed to compile, because a suite that
stops compiling simply stops being discovered. That silent disappearance is the
empty-green run this module exists to make loud.

Everything here reads ``USER_*`` and the ut3 public synonyms — never a dynamic
performance view and never a privileged ``DBA_*`` one: ADT connects as the
application schema and holds no catalog role.
"""

from __future__ import annotations

# The suffix that marks a package as a test package. Jan's naming rule, and the
# whole selection contract: `ut3` never runs a package that does not carry it,
# so production code can never be swept into a test run by a loose pattern.
UT_PACKAGE_SUFFIX = "_UT"

# Every `_UT` package with its compile state. Deliberately unfiltered by name —
# `-name` is applied client-side through shared.sql_like so one LIKE
# implementation covers both halves of the report, and the `_UT` set is small
# enough that the round trip is not worth splitting.
SUITE_PACKAGES_QUERY = """
SELECT
    o.object_name,
    o.status
FROM user_objects o
WHERE 1 = 1
    AND o.object_type   = 'PACKAGE'
    AND o.object_name   LIKE '%\\_UT' ESCAPE '\\'
ORDER BY o.object_name
"""

# utPLSQL's parsed annotations: one row per suite, context, and test. ITEM_TYPE
# separates them (UT_SUITE / UT_SUITE_CONTEXT / UT_LOGICAL_SUITE / UT_TEST), and
# PATH is the runnable identifier `ut.run` accepts.
SUITE_ITEMS_QUERY = """
SELECT
    i.object_owner,
    i.object_name,
    i.item_name,
    i.item_type,
    i.item_description,
    i.item_line_no,
    i.path,
    i.disabled_flag,
    i.disabled_reason
FROM TABLE(ut_runner.get_suites_info(:owner)) i
ORDER BY i.object_name, i.item_line_no
"""

# Declaration order inside the package specification, which is the order a
# reader knows the tests in and therefore the order the results print in.
#
# The annotation cache cannot answer this. `get_suites_info` carries
# `item_line_no` — the line the `%test` comment sits on — and the module used to
# sort by it, which is right only while every annotation sits directly above the
# procedure it describes and the cache is current. `SUBPROGRAM_ID` is the
# position the spec actually declares, so it stays right when the annotation
# does not, and it is the source Jan named.
PACKAGE_PROCEDURES_QUERY = """
SELECT
    p.object_name,
    p.procedure_name,
    p.subprogram_id
FROM user_procedures p
WHERE 1 = 1
    AND p.object_type       = 'PACKAGE'
    AND p.object_name       LIKE '%\\_UT' ESCAPE '\\'
    AND p.procedure_name    IS NOT NULL
ORDER BY p.object_name, p.subprogram_id
"""

# A freshly compiled package is not in the annotation cache yet, so it is not
# discoverable and `ut.run` legitimately reports zero tests. `-refresh` closes
# that window explicitly rather than paying for a rebuild on every run.
REBUILD_ANNOTATION_CACHE_STATEMENT = """
BEGIN
    ut_runner.rebuild_annotation_cache(:owner);
END;
"""

# One suite per call, reported as JUnit XML rather than the documentation
# reporter's prose. The XML carries per-test name, class, duration, and failure
# message as markup, so the result is parsed rather than pattern-matched out of
# a summary line — utPLSQL does not raise on a failed test, and a regex over
# prose is what makes that silence dangerous.
RUN_SUITE_QUERY = """
SELECT
    t.column_value AS line
FROM TABLE(ut.run(:path, ut_junit_reporter())) t
"""

__all__ = [name for name in globals() if not name.startswith("__")]
