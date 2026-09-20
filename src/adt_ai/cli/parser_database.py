from __future__ import annotations

from adt_ai.cli.constants import DEFAULT_ROW_LIMIT
from adt_ai.cli.parser_common import SubParsers, add_connection_key_argument
from adt_ai.cli.parser_diff import add_diff_parser
from adt_ai.ut.limits import GATE_FROM_CONFIG


def add_database_parsers(subparsers: SubParsers) -> None:
    add_diff_parser(subparsers)
    recompile = subparsers.add_parser(
        "recompile",
        description="recompile invalid database objects",
        help="recompile invalid database objects",
    )
    recompile.add_argument("--root", "-root", default=".", help="project root folder")
    recompile.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    recompile.add_argument("--env", "-env", help="connection environment")
    # Declaration order is help order within a section: FILTERS reads -type, -name,
    # -schema.
    # Multi-pattern, like export_db/export_data: `-type A B`, `-type A,B`, and a
    # repeated `-type A -type B` all work. An object matching several patterns is
    # still reported once.
    recompile.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern(s) to recompile, repeatable, comma- or "
                 "space-separated, supports %% wildcards; Oracle names, so PACKAGE is "
                 "specs only",
    )
    recompile.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern(s) to recompile, repeatable, comma- or "
                 "space-separated, supports %% wildcards",
    )
    recompile.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "schema(s) to recompile, repeatable, comma- or space-separated, "
                 "supports %% wildcards",
    )
    recompile.add_argument(
        "--force",
        "-force",
        action = "store_true",
        help   = "recompile all matching objects, not just invalid ones; combined with "
                 "a compile modifier, only objects whose settings drift from it",
    )
    recompile.add_argument(
        "--level",
        "-level",
        type = int,
        help = "PL/SQL optimize level (1-3)",
    )
    recompile.add_argument(
        "--native",
        "-native",
        action = "store_true",
        help   = "compile PL/SQL to native code",
    )
    recompile.add_argument(
        "--interpreted",
        "-interpreted",
        action = "store_true",
        help   = "compile PL/SQL to interpreted code (-native wins if both given; "
                 "with neither, the code type is left untouched)",
    )
    recompile.add_argument(
        "--scope",
        "-scope",
        action = "append",
        nargs  = "+",
        help   = "PL/Scope settings (IDENTIFIERS, STATEMENTS, ALL); separate with "
                 "space, comma, +, or a repeated flag",
    )
    recompile.add_argument(
        "--warnings",
        "-warnings",
        action = "append",
        nargs  = "+",
        help   = "PL/SQL warnings (SEVERE, PERF, INFO); separate with space, comma, "
                 "+, or a repeated flag",
    )
    # Every ACTION is a bare flag scoped by the shared -name/-type filters. None of
    # them carries its own name pattern: that was pure duplication of -name, and a
    # command with two competing name filters is harder to hold in your head than it
    # is useful.
    recompile.add_argument(
        "--mviews",
        "-mviews",
        action = "store_true",
        help   = "report materialized views (scoped by -name), then COMPILE invalid and "
                 "REFRESH stale ones; with -force, REFRESH every matching view",
    )
    recompile.add_argument(
        "--synonyms",
        "-synonyms",
        action = "store_true",
        help   = "report-only: map each synonym (scoped by -name) to owner tables with "
                 "compact PRIV/GRNT/VALID columns and one privilege per row; skips the "
                 "object recompile entirely",
    )
    recompile.add_argument(
        "--disabled",
        "-disabled",
        action = "store_true",
        help   = "report-only: show disabled constraints/triggers and invalid or "
                 "function-disabled indexes (scoped by -name, and by -type to one of "
                 "CONSTRAINT/INDEX/TRIGGER); skips the object recompile entirely",
    )
    recompile.add_argument(
        "--jobs",
        "-jobs",
        action = "store_true",
        help   = "report-only: show today's scheduler job runs (scoped by -name); skips "
                 "the object recompile entirely",
    )
    recompile.add_argument(
        "--vpd",
        "-vpd",
        nargs   = "?",
        const   = "",
        default = None,
        metavar = "COLUMN",
        help    = "report-only: VPD policies, their tables, and how many tables have "
                  "none; with COLUMN, also the tables having it but no policy (scoped "
                  "by -name, and by -type to TABLE/POLICY/FUNCTION)",
    )
    recompile.add_argument(
        "--trailing",
        "-trailing",
        action = "store_true",
        help   = "strip trailing whitespace from stored source in the database via "
                 "CREATE OR REPLACE (scoped by -type/-name); skips the object "
                 "recompile entirely",
    )
    recompile.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress object overview details; keep required command chrome",
    )
    recompile.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    add_connection_key_argument(recompile)
    discovery = subparsers.add_parser(
        "discovery",
        description="run read-only SELECT discovery queries against the target database",
        help="run read-only SELECT discovery queries",
    )
    discovery.add_argument("--root", "-root", default=".", help="project root folder")
    discovery.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    discovery.add_argument("--env", "-env", help="connection environment")
    discovery.add_argument("--schema", "-schema", help="schema to query")
    discovery.add_argument(
        "--sql",
        "-sql",
        help="a single SELECT statement to run",
    )
    discovery.add_argument(
        "--file",
        "-file",
        dest="statements_file",
        help="path to a file of ;-separated SELECT statements",
    )
    discovery.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = DEFAULT_ROW_LIMIT,
        help    = f"max rows rendered per query (default: {DEFAULT_ROW_LIMIT})",
    )
    discovery.add_argument(
        "--no-log",
        "-nolog",
        dest   = "no_log",
        action = "store_true",
        help   = "run queries and print results without writing a discovery report",
    )
    discovery.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    add_connection_key_argument(discovery)
    ut = subparsers.add_parser(
        "ut",
        description="run the schema's utPLSQL (UT) test suites",
        help="run utPLSQL test suites",
    )
    ut.add_argument("--root", "-root", default=".", help="project root folder")
    ut.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    ut.add_argument("--env", "-env", help="connection environment")
    ut.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "name pattern(s), repeatable, comma- or space-separated, supports "
                 "%% wildcards; selects the suites to run, and names itself in the "
                 "RUNNING TESTS FOR ...: header; no pattern means everything",
    )
    ut.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "schema(s) to test, repeatable, comma- or space-separated, "
                 "supports %% wildcards",
    )
    ut.add_argument(
        "--refresh",
        "-refresh",
        action = "store_true",
        help   = "rebuild utPLSQL's annotation cache before discovery, so a suite "
                 "compiled since the last run is found",
    )
    ut.add_argument(
        "--gate",
        "-gate",
        nargs  = "?",
        type   = float,
        const  = GATE_FROM_CONFIG,
        help   = "fail the run when a tested package's COVERAGE is below a "
                 "threshold; with a number that number is the threshold, bare it "
                 "comes from config ut_coverage_gate, absent nothing gates",
    )
    ut.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress the progress bar, the phase sections, and the suites "
                 "roll-up and per-test results under -verbose; keep the summary, "
                 "the errors and failures detail, and command chrome",
    )
    # Same argparse shape as `export_db -compact` and `export_apex -compact`, so
    # the shared-argument contract needs no exception for it. The effect differs
    # and may: there the flag collapses the per-object listing, here the report,
    # and both are the same promise, one line where a list of rows would be.
    ut.add_argument(
        "--compact",
        "-compact",
        action = "store_true",
        help   = "replace both summary tables with one RESULTS row: the run's "
                 "PACKAGES, LINES, TIMER, COVERAGE and a PASS or ERROR status; "
                 "the errors and failures detail and the coverage gate stay",
    )
    ut.add_argument(
        "--verbose",
        "-verbose",
        action = "store_true",
        help   = "print UNIT TESTS SUITES: and TEST RESULTS: with a row per test "
                 "instead of the RUNNING TESTS: bar, and list the suites whose "
                 "coverage moved since the last differing run; -silent outranks it",
    )
    ut.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    add_connection_key_argument(ut)
