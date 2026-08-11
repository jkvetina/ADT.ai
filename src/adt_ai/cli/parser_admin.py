from __future__ import annotations

from adt_ai.cli.parser_common import add_connection_key_argument
from adt_ai.connection.runner import DEFAULT_PORT as CONNECTION_DEFAULT_PORT


def add_admin_parsers(subparsers) -> None:
    doctor = subparsers.add_parser(
        "doctor",
        description="check local ADT.ai environment setup and run explicit updates",
        help="check local setup and run explicit updates",
    )
    doctor.add_argument(
        "-offline",
        action="store_true",
        help="skip online update checks and show local versions only",
    )
    doctor.add_argument(
        "-update",
        action="store_true",
        help="run full ADT.ai, requirements, and SQLcl upgrade",
    )
    doctor.add_argument(
        "-sqlcl",
        action="store_true",
        help="upgrade SQLcl only; runs immediately without -update",
    )
    doctor.add_argument(
        "-init",
        action="store_true",
        help="scaffold project config, ignore rules, patch templates, and safe local folders",
    )
    doctor.add_argument("--root", "-root", default=".", help="project root folder for -init")
    doctor.add_argument(
        "--force",
        "-force",
        action="store_true",
        help="overwrite existing generated template files with -init",
    )
    connection = subparsers.add_parser(
        "connection",
        description="edit the resolved connection file: create a connection, add an "
                    "environment or schema, set a schema password, or set a wallet password",
        help="edit the connection file (add env/schema, set password)",
    )
    connection.add_argument(
        "-create",
        action="store_true",
        help="create or update a connection file entry (requires -env and -schema)",
    )
    connection.add_argument(
        "-add-env",
        action="store_true",
        help="add a new environment (requires -env; -like clones another environment)",
    )
    connection.add_argument(
        "-add-schema",
        action="store_true",
        help="add a new schema to an environment (requires -env and -schema)",
    )
    connection.add_argument(
        "-set-pwd",
        action="store_true",
        help="set a schema password (requires -env and -schema; prompts interactively)",
    )
    connection.add_argument(
        "-set-wallet-pwd",
        action="store_true",
        help="set a wallet password (requires -env; prompts interactively)",
    )
    connection.add_argument("--env", "-env", help="environment name")
    connection.add_argument("--schema", "-schema", help="schema name")
    connection.add_argument(
        "--user",
        "-user",
        help="database user for -add-schema (defaults to the schema name)",
    )
    connection.add_argument(
        "--like",
        "-like",
        metavar = "ENV",
        help    = "with -add-env, clone this environment's db/wallet (secrets stripped)",
    )
    connection.add_argument("--host", "-host", help="with -add-env, set the db hostname")
    connection.add_argument(
        "--port",
        "-port",
        type = int,
        help = f"with -add-env, set the db port (default: {CONNECTION_DEFAULT_PORT})",
    )
    connection.add_argument("--service", "-service", help="with -add-env, set the db service")
    connection.add_argument("--sid", "-sid", help="with -create, set the db SID")
    connection.add_argument("--wallet", "-wallet", help="with -create, set the wallet name/path")
    connection.add_argument("--workspace", "-workspace", help="with -create, set APEX workspace")
    connection.add_argument("--app", "-app", help="with -create, set default APEX app scope")
    connection.add_argument("--prefix", "-prefix", help="with -create, set export prefix filter")
    connection.add_argument("--ignore", "-ignore", help="with -create, set export ignore filter")
    connection.add_argument(
        "--default",
        "-default",
        action = "store_true",
        help   = "with -create, mark the schema as the default db/APEX schema",
    )
    connection.add_argument(
        "--encrypt",
        "-encrypt",
        action = "store_true",
        help   = "encrypt written passwords with -key or ADT_KEY",
    )
    add_connection_key_argument(connection)
    connection.add_argument("--root", "-root", default=".", help="project root folder")
    connection.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    connection.add_argument(
        "--go",
        "-go",
        action = "store_true",
        help   = "apply the change (default previews without writing)",
    )
    connection.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and resolved startup context",
    )
