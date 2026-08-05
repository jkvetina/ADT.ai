from __future__ import annotations

from adt_ai.cli.constants import DEFAULT_ROW_LIMIT
from adt_ai.cli.parser_common import add_connection_key_argument
from adt_ai.shared.recent_state import BARE_RECENT


def add_database_parsers(subparsers) -> None:
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
        help   = "object type pattern(s) to recompile, supports multiple arguments and "
                 "%% wildcards; Oracle type names, so PACKAGE means specs only "
                 "(PACKAGE%% for both) and MVIEW/MATERIALIZED mean MATERIALIZED VIEW",
    )
    recompile.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern(s) to recompile, supports multiple arguments and "
                 "%% wildcards",
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
        description="query the committed dependency index or refresh it from the database",
        help="query or refresh the dependency index",
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
        help="rebuild the index from the database, optionally scoped to object names",
    )
    dependencies.add_argument(
        "--recent",
        "-recent",
        nargs="?",
        const=BARE_RECENT,
        type=int,
        help=(
            "with -refresh, only reload objects changed in the last DAYS days "
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
            "owner schema(s) — repeatable, comma- or space-separated: refresh "
            "scope with -refresh, else an offline owner filter for the query"
        ),
    )
    dependencies.add_argument(
        "--app",
        "-app",
        action="append",
        nargs="+",
        help=(
            "APEX application id(s) to refresh (only with -refresh) — repeat or "
            "space-separate for multiple, or pass a range (MIN-MAX / MIN+) "
            "resolved against discovered apps"
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
    ut3 = subparsers.add_parser(
        "ut3",
        description="run the schema's utPLSQL (UT3) test suites",
        help="run utPLSQL test suites",
    )
    ut3.add_argument("--root", "-root", default=".", help="project root folder")
    ut3.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    ut3.add_argument("--env", "-env", help="connection environment")
    ut3.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "suite package name pattern(s) to run, supports multiple arguments "
                 "and %% wildcards; no pattern runs every _UT suite in the schema",
    )
    ut3.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "schema(s) to test, repeatable, comma- or space-separated, "
                 "supports %% wildcards",
    )
    ut3.add_argument(
        "--refresh",
        "-refresh",
        action = "store_true",
        help   = "rebuild utPLSQL's annotation cache before discovery, so a suite "
                 "compiled since the last run is found",
    )
    ut3.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress the suites roll-up and the per-test results; keep the "
                 "summary, the errors and failures detail, and command chrome",
    )
    ut3.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    add_connection_key_argument(ut3)
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
        help   = "application id(s) — repeat or space-separate for multiple",
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
