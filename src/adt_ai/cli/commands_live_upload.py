from __future__ import annotations

import argparse
from pathlib import Path

from adt_ai.cli.constants import (
    ApexApplication,
    ApexDiscovery,
    GatewayFactory,
    QueryGateway,
    print_module_banner,
)
from adt_ai.cli.context import (
    StartupContext,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
    _project_relative,
)
from adt_ai.cli.export_apex_owners import apex_lookup_schema
from adt_ai.cli.gateways import build_gateway, debug_wrapped
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.live_upload.minify import INSTALL_COMMAND, load_minifiers
from adt_ai.live_upload.runner import (
    ConsoleLiveUploadReporter,
    LiveUploadRequest,
    LiveUploadRunner,
)
from adt_ai.shared.connections import Connection
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.progress import open_section, print_adt_header

# The header the watch runs under. The folder itself is a row rather than part
# of the header, because it is only known once the application has been read.
MONITORING_HEADER = "MONITORING FOLDER:"

# Minification is optional, so a run without it says so once instead of quietly
# uploading unminified files (`live_upload/minify.py`).
MINIFIERS_HEADER = "WARNING - MINIFIERS NOT INSTALLED:"

QUIT_HINT = "press Control+C to quit"
UPLOADED_LABEL = "UPLOADED"

_APP_REQUIRED_MESSAGE = "An application id is required: pass -app N."
# The default nap between passes, old ADT's own. A folder a person is editing
# changes a few times a minute, so anything shorter buys nothing.
DEFAULT_INTERVAL = 1


def _run_live_upload(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_module_banner("LIVE_UPLOAD")

    app_id = _app_id(args)
    if app_id is None:
        print_adt_error("ARGUMENT INVALID", _APP_REQUIRED_MESSAGE)
        return exit_code_for("ARGUMENT INVALID")

    startup = _load_startup_context(args)
    root = startup.root
    connections = startup.connections
    environment = args.env or connections.default_environment
    schema = args.schema or apex_lookup_schema(
        connections, environment, connections.schema_names(environment)
    )
    connection = connections.resolve(environment=environment, schema=schema, kind="apex")
    gateway = _gateway(args, startup, connection, schema, gateway_factory)

    _print_connection_block(gateway, connection, debug=args.debug)
    if args.debug:
        _print_startup_debug(startup)

    # `-once` never minifies (`LiveUploadRunner.upload_all`), so it neither
    # needs the packages nor warns that they are missing.
    minifiers = None if args.once else load_minifiers()
    if minifiers is None and not args.once:
        print_adt_header(MINIFIERS_HEADER)
        print(f"  {INSTALL_COMMAND}")

    progress = open_section(MONITORING_HEADER)
    folder = _folder(args, startup, gateway, schema, app_id)
    if folder is None:
        return 1
    print(f"  {_project_relative(folder, root)}")
    if not folder.is_dir():
        print_adt_error(
            "INPUT NOT FOUND", f"Folder not found: {_project_relative(folder, root)}"
        )
        return exit_code_for("INPUT NOT FOUND")
    if not args.once:
        print(f"  {QUIT_HINT}")

    runner = LiveUploadRunner(
        gateway,
        reporter  = ConsoleLiveUploadReporter(progress),
        minifiers = minifiers,
    )
    request = LiveUploadRequest(
        folder    = folder,
        app_id    = app_id,
        workspace = args.workspace,
        interval  = args.interval or DEFAULT_INTERVAL,
        show      = args.show,
    )
    result = runner.upload_all(request) if args.once else runner.run(request)
    # `finish` closes a row `begin` opened, so the label is streamed first the
    # same way an upload row is (`shared/fixed_width.py`).
    progress.begin(UPLOADED_LABEL)
    progress.finish(UPLOADED_LABEL, result.uploaded)
    # A one-shot push that left a file behind is a failed run, the way any
    # other command with a failed row exits 1. The watch keeps its 0: it reports
    # each failure as it happens and goes on watching.
    return 1 if result.failed else 0


def _app_id(args: argparse.Namespace) -> int | None:
    """The one application this run binds to, or None when it cannot be read.

    Required even in `-workspace` mode: the workspace itself is never named on
    the command line, it is the one owning this application, and the session has
    to be bound to it before `WWV_FLOW_API` will write anything.
    """
    try:
        return int(str(args.app))
    except (TypeError, ValueError):
        return None


def _gateway(
    args: argparse.Namespace,
    startup: StartupContext,
    connection: Connection,
    schema: str,
    gateway_factory: GatewayFactory | None,
) -> QueryGateway:
    gateway = (
        gateway_factory(schema)
        if gateway_factory
        else build_gateway(startup, connection, project_root=startup.root)
    )
    # Shared wrap, so the console guard keeps the nesting `build_gateway`
    # documents (`#670`).
    return debug_wrapped(gateway, debug=args.debug)


def _folder(
    args: argparse.Namespace,
    startup: StartupContext,
    gateway: QueryGateway,
    schema: str,
    app_id: int,
) -> Path | None:
    """Which folder the watch reads, or None once the reason it cannot is printed.

    `-folder` wins outright, which is what makes the command usable on a tree
    ADT.ai did not export. Otherwise it is the same folder `export_apex -files`
    and `-files_ws` write into, resolved from the project's own configuration so
    the two cannot disagree about where static files live.
    """
    if args.folder:
        return Path(args.folder).expanduser().resolve()
    resolver = ApexFileResolver.from_config(startup.root, startup.config).for_schema(schema)
    if args.workspace:
        return resolver.workspace_file("")
    application = _application(gateway, schema, app_id)
    if application is None:
        print_adt_error("INPUT NOT FOUND", f"Application {app_id} not found in {schema}.")
        return None
    return resolver.application_file(application, "")


def _application(gateway: QueryGateway, schema: str, app_id: int) -> ApexApplication | None:
    """The application row, which is what names its folder.

    `apex_path_app` may be spelled with the alias, the name or the group as well
    as the id, so the folder cannot be composed from the command line alone.
    """
    found = ApexDiscovery(gateway).applications(owner=schema, app_ids=[app_id])
    return found[0] if found else None


__all__ = [name for name in globals() if not name.startswith("__")]
