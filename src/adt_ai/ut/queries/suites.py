"""Discovery and execution SQL for the ut module.

Two independent sources answer two different questions, and the module needs
both:

* the **data dictionary** answers "which ``_UT`` packages exist, and did they
  compile", the only place an INVALID test package is visible at all;
* **utPLSQL's own annotation cache** (``ut_runner.get_suites_info``) answers
  "which of them are suites, and what tests do they hold", the only place the
  ``%suite`` / ``%test`` annotations have been parsed.

Reading only the dictionary cannot tell a suite from a helper package; reading
only utPLSQL cannot see a package that failed to compile, because a suite that
stops compiling simply stops being discovered. That silent disappearance is the
empty-green run this module exists to make loud.

Everything here reads ``ALL_*`` and the ut3 public synonyms, never a dynamic
performance view and never a privileged ``DBA_*`` one: ADT connects as the
application schema and holds no catalog role. ``ALL_*`` rather than ``USER_*``
because ``ut_owner`` may put the test packages in a schema of their own, and
``USER_*`` can only ever see the connected one; scoped by an explicit
``:ut_owner`` bind, it returns the same rows for the default same-schema case.
"""

from __future__ import annotations

# The suite schema's test packages, with their compile state and both names the
# convention derives from them.
#
# **`ut_pattern` is applied here, in SQL, where the `LIKE '%\\_UT'` used to sit.**
# A schema holds thousands of packages and a handful of them are suites, so
# fetching the whole list to discard 99% of it after the round trip spends that
# round trip on rows nobody wants, on a command whose runtime already matters.
# `REGEXP_LIKE` is the direct replacement: same position in the plan, same one
# row per suite coming back, and the convention is now configurable.
#
# **The two capture groups are extracted in the same pass**, by `REGEXP_SUBSTR`
# with an explicit subexpression argument. `TARGET_NAME` is `ut_match` group 1,
# the package this suite tests, which is what puts its verdicts on the right row
# of the coverage report, and `MODULE_NAME` is `ut_module` group 1. Both are
# properties of the row, so deriving them anywhere else would mean a second
# regex engine disagreeing with this one about the same string.
#
# `:ut_module` is NULL when a project turns the roll-ups off, and
# `REGEXP_SUBSTR(x, NULL, ...)` is NULL, so the column comes back empty without
# the query needing a second shape.
#
# `'i'` throughout: Oracle stores identifiers upper case but a config file is
# hand-written, and a lower-case pattern that silently selected nothing would be
# the empty green run this module exists to prevent.
#
# `-name` stays client-side, deliberately: it is Oracle LIKE rather than regex,
# it is a per-run argument rather than a convention, and by the time it applies
# the set is already down to the schema's suites.
SUITE_PACKAGES_QUERY = """
SELECT
    o.object_name,
    o.status,
    REGEXP_SUBSTR(o.object_name, :ut_match,  1, 1, 'i', 1)  AS target_name,
    REGEXP_SUBSTR(o.object_name, :ut_module, 1, 1, 'i', 1)  AS module_name
FROM all_objects o
WHERE 1 = 1
    AND o.owner         = :ut_owner
    AND o.object_type   = 'PACKAGE'
    AND REGEXP_LIKE(o.object_name, :ut_pattern, 'i')
ORDER BY o.object_name
"""

# utPLSQL's parsed annotations: one row per suite, context, and test. ITEM_TYPE
# separates them (UT_SUITE / UT_SUITE_CONTEXT / UT_LOGICAL_SUITE / UT_TEST).
#
# `i.path` was selected here until `#670` on the reasoning that it is the
# runnable identifier `ut.run` accepts. It is not the one this command uses:
# `Ut3Runner._run_suite` composes `<owner>.<package>` itself, so the column was
# fetched into a field nothing ever read. A column nobody reads is a column that
# cannot be wrong, which is what kept it here for six cards.
SUITE_ITEMS_QUERY = """
SELECT
    i.object_owner,
    i.object_name,
    i.item_name,
    i.item_type,
    i.item_description,
    i.item_line_no,
    i.disabled_flag,
    i.disabled_reason
FROM TABLE(ut_runner.get_suites_info(:owner)) i
ORDER BY i.object_name, i.item_line_no
"""

# Declaration order inside the package specification, which is the order a
# reader knows the tests in and therefore the order the results print in.
#
# The annotation cache cannot answer this. `get_suites_info` carries
# `item_line_no` (the line the `%test` comment sits on) and the module used to
# sort by it, which is right only while every annotation sits directly above the
# procedure it describes and the cache is current. `SUBPROGRAM_ID` is the
# position the spec actually declares, so it stays right when the annotation
# does not, and it is the source Jan named.
PACKAGE_PROCEDURES_QUERY = """
SELECT
    p.object_name,
    p.procedure_name,
    p.subprogram_id
FROM all_procedures p
WHERE 1 = 1
    AND p.owner             = :ut_owner
    AND p.object_type       = 'PACKAGE'
    AND p.procedure_name    IS NOT NULL
    AND REGEXP_LIKE(p.object_name, :ut_pattern, 'i')
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
# a summary line, utPLSQL does not raise on a failed test, and a regex over
# prose is what makes that silence dangerous.
RUN_SUITE_QUERY = """
SELECT
    t.column_value AS line
FROM TABLE(ut.run(:path, ut_junit_reporter())) t
"""

__all__ = [
    "PACKAGE_PROCEDURES_QUERY",
    "REBUILD_ANNOTATION_CACHE_STATEMENT",
    "RUN_SUITE_QUERY",
    "SUITE_ITEMS_QUERY",
    "SUITE_PACKAGES_QUERY",
    "annotations",
]
