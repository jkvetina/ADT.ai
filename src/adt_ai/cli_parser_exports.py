from __future__ import annotations


def add_export_parsers(subparsers) -> None:
    export_db = subparsers.add_parser(
        "export_db",
        description="export database objects",
        help="export database objects",
    )
    export_db.add_argument("--root", "-root", default=".", help="output root folder")
    export_db.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    export_db.add_argument("--env", "-env", help="connection environment")
    export_db.add_argument("--schema", "-schema", action="append", help="schema to export")
    export_db.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern(s) to export, supports %% wildcards",
    )
    export_db.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern(s) to export, supports %% wildcards",
    )
    export_db.add_argument(
        "--recent",
        "-recent",
        type = int,
        help = "export objects changed in the last DAYS days",
    )
    export_db.add_argument(
        "--dry-run",
        "-dry-run",
        action="store_true",
        help="plan writes without changing files",
    )
    export_db.add_argument(
        "--delete",
        "-delete",
        action = "store_true",
        help   = "delete existing object files before export, excluding DATA",
    )
    export_db.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress per-object progress; keep overview, chrome, and timer",
    )
    export_db.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )

    export_data = subparsers.add_parser(
        "export_data",
        description="export table data",
        help="export table data",
    )
    export_data.add_argument("--root", "-root", default=".", help="output root folder")
    export_data.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    export_data.add_argument("--env", "-env", help="connection environment")
    export_data.add_argument("--schema", "-schema", action="append", help="schema to export")
    export_data.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "table name pattern(s) to export, supports %% wildcards",
    )
    export_data.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress per-table progress; keep chrome, summary, and timer",
    )
    export_data.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )

    export_apex = subparsers.add_parser(
        "export_apex",
        description="export APEX applications",
        help="export APEX applications",
    )
    export_apex.add_argument("--root", "-root", default=".", help="output root folder")
    export_apex.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    export_apex.add_argument("--env", "-env", help="connection environment")
    export_apex.add_argument("--schema", "-schema", action="append", help="APEX owner schema")
    export_apex.add_argument("--ws", "-ws", help="APEX workspace")
    export_apex.add_argument("--group", "-group", help="APEX application group")
    export_apex.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "application id(s), or ranges MIN-MAX / MIN+, to export or reveal",
    )
    export_apex.add_argument(
        "--max-app-id",
        "--max_app_id",
        "-max_app_id",
        dest = "max_app_id",
        type = int,
        help = "only list apps with application_id below ID (hides temp/backup apps)",
    )
    export_apex.add_argument(
        "--recent",
        "-recent",
        nargs = "?",
        const = 1,
        type  = int,
        help  = "show components changed in the last DAYS days",
    )
    export_apex.add_argument(
        "--by",
        "-by",
        nargs = "?",
        const = "",
        help  = "show components changed by developer",
    )
    export_apex.add_argument("--release", "-release", help="override APEX release in SQL exports")
    export_apex.add_argument(
        "--reveal",
        "-reveal",
        action = "store_true",
        help   = "show matching APEX workspaces and applications",
    )
    export_apex.add_argument(
        "--owners",
        "-owners",
        action = "store_true",
        help   = "in reveal mode, list app counts for all owners, not just configured schemas",
    )
    export_apex.add_argument(
        "--all",
        "-all",
        action = "store_true",
        dest   = "all_formats",
        help   = "export all APEX formats",
    )
    export_apex.add_argument(
        "--full", "-full", action="store_true", help="export full application SQL"
    )
    export_apex.add_argument(
        "--split", "-split", action="store_true", help="export split application source"
    )
    export_apex.add_argument(
        "--readable", "-readable", action="store_true", help="export readable YAML source"
    )
    export_apex.add_argument(
        "--embedded", "-embedded", action="store_true", help="export embedded code report"
    )
    export_apex.add_argument("--rest", "-rest", action="store_true", help="export REST services")
    export_apex.add_argument(
        "--files", "-files", action="store_true", help="export application files"
    )
    export_apex.add_argument(
        "--files-ws",
        "--files_ws",
        "-files_ws",
        action="store_true",
        dest="files_ws",
        help="export workspace files",
    )
    export_apex.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
