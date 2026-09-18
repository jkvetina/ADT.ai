from __future__ import annotations

from adt_ai.cli.constants import DEFAULT_ROW_LIMIT
from adt_ai.cli.parser_common import SubParsers, add_connection_key_argument
from adt_ai.shared.dates import recent_window
from adt_ai.shared.recent_state import BARE_RECENT
from adt_ai.ut.limits import GATE_FROM_CONFIG


def add_database_parsers(subparsers: SubParsers) -> None:
    diff = subparsers.add_parser(
        "diff",
        description="compare two schemas and generate a SQLcl DIFF deployment artifact",
        help="compare schemas using SQLcl DIFF",
    )
    diff.add_argument("--root", "-root", default=".", help="project root folder")
    diff.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    # Defaulted rather than required (Jan, ADT #773: *"-source ENV should default
    # to current env"*). It falls back to the connection default environment, the
    # same fallback `-env` documents on every other command, so the side you are
    # standing in is the side you compare FROM without saying so.
    diff.add_argument(
        "--source",
        "-source",
        metavar  = "ENV",
        help     = "source environment name (defaults to the connection default)",
    )
    # `-schema`, not `-source-schema`: the usual comparison is one schema across
    # two environments, so the name that reads as "the schema" is the one a user
    # types (Jan, ADT #763). `-target-schema` is the exception it was always
    # meant to be, and defaults to this one rather than to the environment.
    diff.add_argument(
        "--schema",
        "-schema",
        metavar = "SCHEMA",
        help    = "source schema (defaults to environment default)",
    )
    diff.add_argument(
        "--target",
        "-target",
        metavar  = "ENV",
        help     = "target environment name",
    )
    diff.add_argument(
        "--target-schema",
        "-target-schema",
        metavar = "SCHEMA",
        help    = "target schema (defaults to -schema)",
    )
    # A `.zip` path names the artifact, anything else is the folder it lands in
    # (`#773`). One suffix rather than "does it look like a file", so a folder
    # called `releases/v1.2` cannot be mistaken for one. Under `-data` it names
    # the file the untrimmed rows go to, `.log`/`.txt` or a folder (`#886`).
    diff.add_argument(
        "--out",
        "-out",
        metavar = "DIR|FILE",
        help    = "output folder, or a .zip path naming the artifact "
                  "(default: <root>/config/diff); with -data, write every "
                  "differing row untrimmed to a .log/.txt file or into DIR",
    )
    # Multi-pattern, the shape `recompile` and `export_db` take: `-type A B`,
    # `-type A,B` and a repeated `-type A -type B` all work. Both filters narrow
    # the EXPORT as well as the screen, so a filtered run compares less and its
    # artifact holds only what was asked for (`#780`, overruling `#773`).
    diff.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern(s) to compare, repeatable, comma- or "
                 "space-separated, supports %% wildcards; narrows the export and "
                 "the screen",
    )
    diff.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern(s) to compare, repeatable, comma- or "
                 "space-separated, supports %% wildcards; a grant matches on the "
                 "object it grants",
    )
    diff.add_argument(
        "--verbose",
        "-verbose",
        action = "store_true",
        help   = "list every changed object, not just the count per object type; "
                 "with -data, each table's differing rows and values",
    )
    # Jan's spelling and scope, asked with chips (ADT #878): `-rest` compares the
    # REST modules, privileges and roles both schemas publish INSTEAD of the
    # schema objects. Same `store_true` shape as `export_apex -rest`, the flag it
    # reuses the export of.
    diff.add_argument(
        "--rest",
        "-rest",
        action = "store_true",
        help   = "compare the REST modules, privileges and roles both schemas "
                 "publish, instead of the schema objects",
    )
    # Jan's spelling and scope, asked with chips (ADT #877): `-data` compares the
    # rows of the tables `export_data` exports, database against database,
    # INSTEAD of the schema objects. `store_true`, the `-rest` shape beside it.
    diff.add_argument(
        "--data",
        "-data",
        action = "store_true",
        help   = "compare the rows of the tables export_data exports, instead of "
                 "the schema objects",
    )
    # Jan, ADT #883, with chips: audit columns left out on demand, beside the
    # `ignored_columns` config and the identity columns `-data` always skips.
    # The `patch -ignore` shape, so a pattern list reads the same everywhere.
    diff.add_argument(
        "--ignore",
        "-ignore",
        action = "append",
        nargs  = "+",
        help   = "with -data, column pattern(s) to leave out on both sides, "
                 "repeatable, comma- or space-separated, supports %% wildcards",
    )
    # Jan, ADT #883: *"-limit # to limit number of rows as a shortloop"*. A
    # table stops after N differing rows.
    diff.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = None,
        metavar = "N",
        help    = "with -data, stop each table after N differing rows (0 = all)",
    )
    # NOT "show debug info" (ADT #326), which was the one -debug row of eleven
    # that named neither what is shown nor where it comes from. `-debug` appends
    # itself to the SQLcl DIFF command (`diff/runner.py`), so the extra output is
    # SQLcl's, not ADT.ai's; `-verbose` above is ADT.ai's own screen.
    diff.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters, and pass -debug to the SQLcl DIFF command",
    )
    add_connection_key_argument(diff)
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
    dependencies = subparsers.add_parser(
        "dependencies",
        description=(
            "query the committed dependency index, refresh it from the "
            "database with -refresh, or scan an APEX application for "
            "components that no longer compile with -scan; name one of them"
        ),
        help="query or refresh the index, or scan an APEX app",
    )
    dependencies.add_argument("--root", "-root", default=".", help="project root folder")
    # -from/-to keep dest=uses/used_by so the command body and store calls are
    # unchanged: -from OBJ = objects OBJ depends on; -to OBJ = objects that
    # depend on OBJ. dest avoids the Python `from` keyword.
    dependencies.add_argument(
        "--from",
        "-from",
        dest="uses",
        metavar="OBJ",
        help="objects OBJ depends on",
    )
    dependencies.add_argument(
        "--to",
        "-to",
        dest="used_by",
        metavar="OBJ",
        help="objects that depend on OBJ",
    )
    dependencies.add_argument(
        "--impact",
        "-impact",
        metavar="OBJ",
        help="transitive reverse impact of OBJ",
    )
    dependencies.add_argument(
        "--tree",
        "-tree",
        metavar="CONSTRAINT",
        help="foreign-key reference and dependency cascade for CONSTRAINT",
    )
    dependencies.add_argument(
        "--age",
        "-age",
        action="store_true",
        help="list when each schema/app scope was last refreshed (offline)",
    )
    dependencies.add_argument(
        "--refresh",
        "-refresh",
        nargs="*",
        metavar="NAME",
        help=(
            "rebuild the index from the database, optionally scoped to object "
            "names; required to refresh, never implied"
        ),
    )
    dependencies.add_argument(
        "--recent",
        "-recent",
        nargs="?",
        const=BARE_RECENT,
        type=recent_window,
        help=(
            "refresh only: reload just the objects changed in the last DAYS days "
            "or a fraction of a day, 1/24 = past hour "
            "(bare -recent = since that scope's last refresh)"
        ),
    )
    dependencies.add_argument(
        "--force",
        "-force",
        action="store_true",
        help="wipe the requested refresh scope before reloading it",
    )
    dependencies.add_argument(
        "--scan",
        "-scan",
        action="store_true",
        help=(
            "compile every component of the -app application(s) and report what "
            "no longer compiles; writes nothing"
        ),
    )
    dependencies.add_argument(
        "--page",
        "-page",
        action="append",
        nargs="+",
        help=(
            "scan only: page id(s), or ranges MIN-MAX / MIN+, to scan instead "
            "of the whole application"
        ),
    )
    dependencies.add_argument(
        "--format",
        "-format",
        choices=["table", "yaml", "md"],
        default="table",
        help="output format (default: table)",
    )
    dependencies.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML (refresh)",
    )
    dependencies.add_argument("--env", "-env", help="connection environment (refresh)")
    dependencies.add_argument(
        "--schema",
        "-schema",
        action="append",
        nargs="+",
        help=(
            "owner schema(s), repeatable, comma- or space-separated: an offline "
            "owner filter beside a query, the refresh scope without one"
        ),
    )
    dependencies.add_argument(
        "--app",
        "-app",
        action="append",
        nargs="+",
        help=(
            "APEX application id(s), or ranges MIN-MAX / MIN+ resolved against "
            "discovered apps, to refresh (refresh only), repeatable, comma- "
            "or space-separated"
        ),
    )
    add_connection_key_argument(dependencies)
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
    flow = subparsers.add_parser(
        "flow",
        description="map APEX page navigation: query incoming/outgoing links or refresh diagrams",
        help="map APEX page navigation links (to/from, refresh)",
    )
    flow.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "application id(s), repeatable, comma- or space-separated",
    )
    flow.add_argument(
        "--to",
        "-to",
        dest    = "to_page",
        type    = int,
        metavar = "PAGE",
        help    = "show pages that link INTO this page",
    )
    flow.add_argument(
        "--from",
        "-from",
        dest    = "from_page",
        type    = int,
        metavar = "PAGE",
        help    = "show pages reachable FROM this page",
    )
    flow.add_argument(
        "--refresh",
        "-refresh",
        action = "store_true",
        help   = "rescrape the application from the database and rewrite its edges",
    )
    flow.add_argument(
        "--delete",
        "-delete",
        dest   = "delete",
        action = "store_true",
        help   = "delete the application and its edges from the store",
    )
    flow.add_argument("--root", "-root", default=".", help="project root folder")
    flow.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML (refresh)",
    )
    flow.add_argument("--env", "-env", help="connection environment (refresh)")
    flow.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    add_connection_key_argument(flow)
