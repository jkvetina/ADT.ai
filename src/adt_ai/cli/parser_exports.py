from __future__ import annotations

from adt_ai.cli.parser_common import add_connection_key_argument
from adt_ai.shared.recent_state import BARE_RECENT


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
    export_db.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "schema(s) to export, repeatable, comma- or space-separated, "
                 "supports %% wildcards",
    )
    export_db.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern(s) to export, supports %% wildcards; Oracle type "
                 "names, so PACKAGE means specs only (PACKAGE%% for both) and "
                 "MVIEW/MATERIALIZED mean MATERIALIZED VIEW",
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
        nargs = "?",
        const = BARE_RECENT,
        type  = int,
        help  = "export objects changed in the last DAYS days "
                "(bare -recent = since the last export of that schema)",
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
    export_db.add_argument(
        "--groups",
        "-groups",
        nargs  = "*",
        default= None,
        metavar= "PREFIX",
        help   = "move action: reorganize exported files into <type>/<group>/ "
                 "subfolders (previews then confirms, never connects). Pass PREFIX "
                 "names to group only those; bare -groups auto-detects by prefix",
    )
    export_db.add_argument(
        "--by",
        "-by",
        nargs = "?",
        const = "",
        help  = "export only objects changed by AUTHOR (db user/schema), "
                "resolved via the project's configured audit source",
    )
    export_db.add_argument(
        "--my",
        "-my",
        action = "store_true",
        help   = "export only objects changed by the current user "
                 "(db schema from config/IDENTITY.yaml)",
    )
    add_connection_key_argument(export_db)

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
    export_data.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "schema(s) to export data from, repeatable, comma- or "
                 "space-separated, supports %% wildcards",
    )
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
    add_connection_key_argument(export_data)

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
    export_apex.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "APEX owner schema(s), repeatable, comma- or space-separated, "
                 "supports %% wildcards",
    )
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
        "--page",
        "-page",
        action = "append",
        nargs  = "+",
        help   = "page id(s), or ranges MIN-MAX / MIN+, to export from split/readable output",
    )
    export_apex.add_argument(
        "--deep",
        "-deep",
        action = "store_true",
        help   = "with -page, also export page components from config/dependencies.db",
    )
    export_apex.add_argument(
        "--component",
        "-component",
        action = "append",
        nargs  = "+",
        help   = "component filters as TYPE:NAME_PATTERN, for example LOV:STATUS%%",
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
        const = BARE_RECENT,
        type  = int,
        help  = "show components changed in the last DAYS days "
                "(bare -recent = since the last export of that app/format)",
    )
    export_apex.add_argument(
        "--by",
        "-by",
        nargs = "?",
        const = "",
        help  = "show components changed by developer",
    )
    export_apex.add_argument(
        "--my",
        "-my",
        action = "store_true",
        help   = "show components changed by the current git user",
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
    # `allow_abbrev` is off, so the `-apx` short form has to be declared here;
    # both spellings share the one `apexlang` dest — one action, never two.
    export_apex.add_argument(
        "--apexlang",
        "-apexlang",
        "--apx",
        "-apx",
        action = "store_true",
        dest   = "apexlang",
        help   = "export APEXlang (.apx) source, whole app (APEX 26.1+)",
    )
    export_apex.add_argument(
        "--checksum",
        "-checksum",
        action = "store_true",
        help   = "export the ID-independent SHA-256 application checksum",
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
    add_connection_key_argument(export_apex)

    validate = subparsers.add_parser(
        "validate",
        description="validate APEXlang application source",
        help="validate APEXlang application source",
    )
    validate.add_argument("--root", "-root", default=".", help="output root folder")
    validate.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    validate.add_argument(
        "--input",
        "-input",
        action = "append",
        nargs  = "+",
        help   = "APEXlang folder(s) or zip(s) to validate, repeatable, comma- or "
                 "space-separated; default is every exported apexlang/ folder",
    )
    validate.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "application id(s) whose exported apexlang/ folder to validate",
    )
    validate.add_argument(
        "--silent",
        "-silent",
        action = "store_true",
        help   = "suppress per-folder progress; keep chrome, error tables, and timer",
    )
    validate.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and the generated SQLcl script",
    )
    # No -env/-schema/-key: `apex validate` compiles inside SQLcl and answers on a
    # bare `sql -S /nolog` session, so the command never connects (card #163).
