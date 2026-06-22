from __future__ import annotations

from adt_ai.cli_constants import DEFAULT_ROW_LIMIT


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
    recompile.add_argument("--target", "-target", help="connection environment (alias of -env)")
    recompile.add_argument("--schema", "-schema", help="schema to recompile")
    recompile.add_argument(
        "--type",
        "-type",
        default = "%",
        help    = "object type pattern to recompile, supports %% wildcards",
    )
    recompile.add_argument(
        "--name",
        "-name",
        default = "%",
        help    = "object name pattern to recompile, supports %% wildcards",
    )
    recompile.add_argument(
        "--force",
        "-force",
        action = "store_true",
        help   = "recompile all matching objects, not just invalid ones",
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
        help   = "compile PL/SQL to interpreted code (default)",
    )
    recompile.add_argument(
        "--scope",
        "-scope",
        nargs = "*",
        help  = "PL/Scope settings (IDENTIFIERS, STATEMENTS, ALL)",
    )
    recompile.add_argument(
        "--warnings",
        "-warnings",
        nargs = "*",
        help  = "PL/SQL warnings (SEVERE, PERF, INFO)",
    )
    recompile.add_argument(
        "--mviews",
        "-mviews",
        nargs   = "?",
        const   = "%",
        default = None,
        metavar = "NAME",
        help    = "report materialized views (optionally filtered by NAME pattern, e.g. "
                  "-mviews DEP%%), then COMPILE invalid and REFRESH stale ones; with -force, "
                  "REFRESH every matching view",
    )
    recompile.add_argument(
        "--synonyms",
        "-synonyms",
        nargs   = "?",
        const   = "%",
        default = None,
        metavar = "NAME",
        help    = "report-only: map each synonym (optionally filtered by NAME pattern, e.g. "
                  "-synonyms APP%%) to owner tables with compact PRIV/GRNT/VALID "
                  "columns and one privilege per row; skips the object recompile entirely",
    )

    recompile.add_argument(
        "--disabled",
        "-disabled",
        nargs   = "?",
        const   = "%",
        default = None,
        metavar = "NAME",
        help    = "report-only: show disabled constraints/triggers and invalid or "
                  "function-disabled indexes (optionally filtered by NAME pattern, "
                  "e.g. -disabled APP%%); "
                  "skips the object recompile entirely",
    )
    recompile.add_argument(
        "--jobs",
        "-jobs",
        nargs   = "?",
        const   = "%",
        default = None,
        metavar = "NAME",
        help    = "report-only: show today's scheduler job runs (optionally filtered "
                  "by NAME pattern, e.g. -jobs APP%%); skips the object recompile "
                  "entirely",
    )
    recompile.add_argument(
        "--errors",
        "-errors",
        action = "store_true",
        help   = "print the full compile error messages (line, position, text) of invalid objects",
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
