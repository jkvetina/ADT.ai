from __future__ import annotations

from adt_ai.cli.parser_common import SubParsers, add_connection_key_argument


def add_live_upload_parser(subparsers: SubParsers) -> None:
    live_upload = subparsers.add_parser(
        "live_upload",
        description=(
            "watch a static files folder and upload every save straight into APEX, "
            "or upload the whole folder once with -once"
        ),
        help="upload static files to APEX as you save them",
    )
    # `-app` is a plain single value here, the shape `connection -app` already
    # has: the command watches ONE folder and binds ONE workspace, so the
    # multi-id filter `export_apex` and `flow` declare would take a list this
    # command could not act on.
    live_upload.add_argument("--app", "-app", metavar="ID", help="application to upload into")
    live_upload.add_argument(
        "--workspace",
        "-workspace",
        action = "store_true",
        help   = "upload to the workspace static files instead of the application ones",
    )
    live_upload.add_argument(
        "--folder",
        "-folder",
        metavar = "PATH",
        help    = "folder to watch instead of the exported static files folder",
    )
    # `-interval` paces the watch and `-once` replaces the watch, so the pair is
    # refused by the parser rather than accepted with one of them doing nothing.
    mode = live_upload.add_mutually_exclusive_group()
    mode.add_argument(
        "--interval",
        "-interval",
        type    = int,
        metavar = "SECONDS",
        help    = "seconds to wait between passes over the folder, one by default",
    )
    mode.add_argument(
        "--once",
        "-once",
        action = "store_true",
        help   = "upload every file in the folder once and exit instead of watching",
    )
    live_upload.add_argument(
        "--show",
        "-show",
        action = "store_true",
        help   = "list what the folder already holds before the watch starts",
    )
    live_upload.add_argument("--root", "-root", default=".", help="project root folder")
    live_upload.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    live_upload.add_argument("--env", "-env", help="connection environment")
    live_upload.add_argument("--schema", "-schema", help="APEX owner schema to connect through")
    live_upload.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and resolved startup context",
    )
    add_connection_key_argument(live_upload)
