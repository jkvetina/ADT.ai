"""`rebuild`'s database caches: the dependency mirror and the page picture (`#30`).

`rebuild` builds every cache ADT.ai answers from. The commit store is its own
half; this module is the other one. Jan, 2026-09-19: *"always refresh (missing)
commits + object deps for selected/active schema and rebuild/scan apps only on
demand"*. So every run refreshes the object dependencies of the schemas `-schema`
names, or of the connection's default schema when it names none, and only an
`-app` run reaches the application caches: the APEX dependency scan into the
mirror, and the page links behind `search -to APP.PAGE`.

Both refreshes are the ones the retired `dependencies` and `flow` commands
ran. A refresh only reloads what changed; `-force` wipes the scope first and
reloads all of it. An application's page links are read right after its
dependency scan and print under the scan's header, one header per application.

The run is planned before the commit walk and carried out after it. Every read
that decides WHAT to refresh (an `-app` range, the schema an application routes
through, the labels its header prints) happens in the plan, under the module
banner, which is still the newest thing on screen and so announces them. After
the walk the finished progress bar is, and a closed row announces nothing.

**A connection that cannot be resolved stops the run on the shared
configuration screen**, the one every connecting command prints, and exits 1.
Jan, 2026-09-19: no warning that skips the dependency half. A bare run stops
there after the commit walk, which needs no database, so a checkout without a
connection still gets its history; a run that named `-schema` or `-app` asked
for the database by name and is refused before the walk.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    ConnectionConfigError,
    DependencyIndexRequest,
    DependencyIndexRunner,
    GatewayFactory,
    QueryGateway,
    print_adt_header,
)
from adt_ai.cli.context import (
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
    _print_connection_block,
)
from adt_ai.cli.export_apex_owners import listed_applications, resolve_apex_owner_routes
from adt_ai.cli.export_reporters import ConsoleApexRevealReporter
from adt_ai.cli.rebuild_flow import FlowPlan, FlowRefresh, plan_flow_refresh
from adt_ai.cli.refresh_connect import (
    _app_selection_error,
    _connecting_mode_gateways,
    _refresh_lookup_schema,
    _resolve_refresh_app_ids,
)
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.export_apex.inventory import ApexApplication, ApexDiscovery
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.connection_errors import ConnectionNotFoundError
from adt_ai.shared.connections import Connection
from adt_ai.shared.dates import resolve_since
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.progress import FixedWidthProgressPrinter, schema_label

#: The flags that steer the database half, by dest, in the order a refusal names
#: them. `-config-dir` is not one: the commit half reads config too.
REFRESH_FLAGS = (
    ("schema", "-schema"),
    ("app", "-app"),
    ("force", "-force"),
    ("env", "-env"),
    ("key", "-key"),
)


def _rebuild_argument_error(args: argparse.Namespace) -> str | None:
    """Refuse a database flag beside a mode that never connects, before the banner.

    `-reveal` lists branches and `-verify` reads the commit stores, and neither
    refreshes a database cache, so a `-schema` beside them would be a flag the
    run ignores. `console.md` puts the rule as a flag a command does not take
    being a parser error rather than a flag it ignores, and a mode is where one
    command stops taking a flag.

    The branch flags are refused here too, for the same reason and on the same
    screen: `-since` that is not a date, `-switch` with no `-reveal` list to pick
    from, and `-since` beside `-limit` outside `-reveal`, where both bound the
    same walk. Refused from inside the handler, they printed under a stdout
    banner and without the `-h` hint every other refusal carries.
    """
    since = getattr(args, "since", None)
    if since is not None:
        try:
            resolve_since(since)
        except ValueError as exc:
            return str(exc)
    mode = "-reveal" if args.reveal is not None else "-verify" if args.verify else None
    steering = [
        flag
        for dest, flag in REFRESH_FLAGS
        if getattr(args, dest, None) not in (None, False)
    ]
    if mode and steering:
        # Every flag named, as a sentence: `-schema / -app refreshes` read as
        # one flag with a slash in it and a verb that agreed with neither.
        *rest, last = steering
        named = f"{', '.join(rest)} and {last}" if rest else last
        verb = "refresh" if rest else "refreshes"
        # The headline's joiner is uppercase with the rest of it (ADT #934).
        headline = f"{', '.join(rest)} AND {last}" if rest else last
        return (
            f"{headline} CANNOT BE COMBINED WITH {mode}\n\n"
            f"{named} {verb} the database caches."
        )
    if args.reveal is None and getattr(args, "switch", None) is not None:
        return "-switch NEEDS -reveal"
    if mode is None and since is not None and args.limit is not None:
        return "-since AND -limit CANNOT BE COMBINED"
    return _app_selection_error(args.app) if args.app else None


@dataclass(frozen=True)
class UnresolvedRefresh:
    """A bare run whose connection did not resolve, raised after the commit walk."""

    error: ConnectionNotFoundError


@dataclass
class RefreshPlan:
    """Everything the database half needs, resolved before the commit walk."""

    root: Path
    connection_for: Callable[[str], Connection]
    gateway_factory: GatewayFactory
    config: dict[str, Any] = field(default_factory=dict)
    #: The schemas whose object dependencies this run refreshes.
    schemas: list[str] = field(default_factory=list)
    apps: list[int] = field(default_factory=list)
    #: The schema the application half connects through.
    app_schema: str | None = None
    discovered_apps: list[ApexApplication] = field(default_factory=list)
    flow: FlowPlan | None = None
    force: bool = False

    @property
    def segments(self) -> list[str]:
        """One console segment per schema, the application schema folded in."""
        segments = list(self.schemas)
        if self.app_schema and self.apps and self.app_schema not in segments:
            segments.append(self.app_schema)
        return segments


def plan_database_refresh(
    args: argparse.Namespace,
    root: Path,
    gateway_factory: GatewayFactory | None,
) -> RefreshPlan | UnresolvedRefresh | int:
    """What this run will refresh, or the exit code of a run that cannot start.

    A bare run whose connection does not resolve still rebuilds the commit
    store, which needs no database: the plan carries the error and the run
    raises it after the walk, onto the shared configuration screen. A run that
    named `-schema` or `-app` asked for the database by name, so the same
    failure refuses it here. An `-app` run on a file with no default schema has
    no schema half to run, and reads the application through the schema it
    routes through (`#638`).
    """
    explicit = bool(args.schema or args.app)
    try:
        startup = _load_startup_context(args)
        connections = startup.connections
        environment = args.env or connections.default_environment
    except ConnectionNotFoundError as error:
        if explicit:
            raise
        return UnresolvedRefresh(error)

    schemas: list[str] = []
    if args.schema:
        schemas = connections.expand_schemas(
            _flatten_arg_groups(args.schema), environment=environment
        )
    else:
        try:
            schemas = connections.default_schemas(environment)
        except ConnectionNotFoundError as error:
            if not args.app:
                return UnresolvedRefresh(error)

    connection_for, plan_gateway_factory = _connecting_mode_gateways(
        startup, environment, gateway_factory, debug=False
    )
    plan = RefreshPlan(
        root            = root,
        connection_for  = connection_for,
        gateway_factory = plan_gateway_factory,
        config          = startup.config,
        schemas         = schemas,
        force           = bool(args.force),
    )
    if not args.app:
        return plan

    # -app reuses the shared APEX selection parser: explicit ids flow through
    # unchanged; ranges (MIN-MAX / MIN+) are resolved against discovered apps.
    selection = _parse_apex_app_selection(_flatten_arg_groups(args.app))
    plan.apps = _resolve_refresh_app_ids(
        selection, connections, environment, plan.gateway_factory
    )
    if selection is not None and selection.has_ranges and not plan.apps:
        print_adt_error("INPUT NOT FOUND", "-app RANGE MATCHED NO APPLICATIONS")
        return exit_code_for("INPUT NOT FOUND")

    plan.app_schema = _app_schema(args, plan, root, connections, environment)
    # **The application rows are read before the first connection block, not
    # after it** (`#372`). Both headers they feed name them, `APP <label>,
    # REFRESHING:` and the workspace in `APEX APPLICATIONS: <workspace> |
    # <SCHEMA>`. Jan, 2026-08-16: *"You can resolve
    # the schema/workspace from apex.db, if it is missing, you can do extra
    # query."* The store answers first and costs no round trip at all.
    if plan.app_schema is not None:
        plan.discovered_apps = _stored_apex_applications(root, plan.apps) or (
            _discover_apex_applications(
                plan.gateway_factory(plan.app_schema), plan.app_schema, plan.apps
            )
        )
    plan.flow = plan_flow_refresh(startup, environment, plan.apps, gateway_factory)
    # An application no APEX schema owns was named under `APP NOT FOUND` just
    # now; scanning it anyway died on `ORA-20001` and took the named ones with
    # it (ADT #906), so only the applications that exist are scanned.
    plan.apps = [app for app in plan.apps if app not in plan.flow.not_found]
    if not plan.apps:
        return exit_code_for("INPUT NOT FOUND")
    # An application whose owner schema the connection file does not name is
    # read through the configured schema that reaches it (ADT #907), and the
    # dependency scan connects through that one as well: the scan asks the
    # workspace-filtered APEX views, which answer only where the application
    # is visible. A `-schema` the run named still wins.
    routed = {plan.flow.reached[app] for app in plan.apps if app in plan.flow.reached}
    if not args.schema and len(routed) == 1:
        plan.app_schema = routed.pop()
    return plan


def _app_schema(
    args: argparse.Namespace,
    plan: RefreshPlan,
    root: Path,
    connections: Any,
    environment: str,
) -> str | None:
    """The one schema the APEX dependency scan connects through.

    A `-schema` the run named carries it. Without one, the application's
    recorded owner in the cached `config/internal/apex.db` is preferred, so the
    run connects straight to it; the routing itself is export_apex's own
    resolver, one derivation, two callers. `sole_owner` is where this command's
    single-connection constraint is applied, and mixed owners or unknown apps
    fall back to the default schema.
    """
    if args.schema:
        return plan.schemas[0]
    try:
        owner_routes = resolve_apex_owner_routes(root, connections, environment, plan.apps)
    except ConnectionConfigError:
        # Routing exists to skip a wasted default-schema connection, so it has a
        # question to answer only where a default is configured; the resolver
        # raises rather than saying so. Without one the fallback the range was
        # discovered through is the only connection the file describes (`#670`).
        return _refresh_lookup_schema(connections, environment)
    return owner_routes.sole_owner or owner_routes.default_schema


def run_database_refresh(
    plan: RefreshPlan | UnresolvedRefresh, *, first_started_at: float
) -> int:
    """Refresh every planned cache, one console segment per schema."""
    if isinstance(plan, UnresolvedRefresh):
        # The dispatcher prints it on the configuration screen, exit 1.
        raise plan.error
    gateway_factory = plan.gateway_factory
    connection_for = plan.connection_for
    runner = DependencyIndexRunner(gateway_factory)

    def refresh_segment(schema: str) -> int:
        is_schema_segment = schema in plan.schemas
        is_app_segment = schema == plan.app_schema and bool(plan.apps)
        versions = _print_connection_block(
            gateway_factory(schema), connection_for(schema), debug=False
        )
        segment_apex_versions = {schema: versions["APEX"]} if versions.get("APEX") else {}

        segment_app_labels: dict[int, str] = {}
        if is_app_segment:
            labels = _apex_app_labels(plan.apps, plan.discovered_apps)
            segment_app_labels = dict(zip(plan.apps, labels, strict=True))
        if is_schema_segment:
            # The schema is uppercased into the sentence rather than trailing a
            # colon: `REFRESHING: app_owner` left the dashed rule stopping at the
            # colon, one word short of the line it was underlining (ADT #237).
            # It names the schema alone even when the applications ride in the
            # same segment: every row under it reads the schema, and each
            # application opens its own `APP <label>, REFRESHING:` below. It
            # read `REFRESHING <SCHEMA> SCHEMA AND APEX APP <label>:` until ADT
            # #904, Jan: *"RENAME, because you are fetching just the schema,
            # nothing related to the app"*.
            print_adt_header(f"REFRESHING {schema_label(schema)} SCHEMA:")
        else:
            # Same shape export_apex prints before its own per-app export
            # loop: one APEX APPLICATIONS: table instead of a flat banner.
            ConsoleApexRevealReporter().applications(schema, plan.discovered_apps)

        request = DependencyIndexRequest(
            root          = plan.root,
            schemas       = [schema] if is_schema_segment else [],
            config        = plan.config,
            apps          = plan.apps if is_app_segment else [],
            app_schema    = schema if is_app_segment else None,
            force         = plan.force,
            progress      = FixedWidthProgressPrinter(),
            apex_versions = segment_apex_versions,
            app_labels    = segment_app_labels if is_app_segment else None,
        )
        # A component scan the database refused is reported on its own
        # application and stepped over (ADT #908), so the run reaches its end
        # and says so in the exit code, the way a page-link failure already does.
        if not is_app_segment or plan.flow is None:
            return 1 if runner.refresh(request) else 0
        with FlowRefresh(plan.flow, schema) as flow:
            scan_failures = runner.refresh(replace(request, on_app_refreshed=flow.app))
            # An application the scan skipped (APEX before 24.2) opened no
            # header, so its page links open the one it would have printed.
            for app in plan.apps:
                if app not in flow.seen:
                    flow.app(app, header=f"APP {segment_app_labels[app]}, REFRESHING:")
        return flow.exit_code or (1 if scan_failures else 0)

    return run_schema_sections(plan.segments, refresh_segment, first_started_at=first_started_at)


def _stored_apex_applications(root: Path, apps: list[int]) -> list[ApexApplication]:
    """``apps`` as ``apex.db`` already knows them, or ``[]`` if it knows none.

    Every application ADT.ai has exported is in that store, workspace and alias
    included, which is the whole of what the two headers need. All or nothing
    on purpose: a partial answer would label some applications and leave the
    rest as bare ids, which reads as a discovery failure rather than a cache
    miss, and the query it saves is one round trip.
    """
    try:
        with ApexStore.load(root) as store:
            rows = [store.application(app) for app in apps]
    except Exception:
        return []
    # Every app has to have answered: a partial hit means the store is behind the
    # instance, and the caller's fallback is a live discovery query, not half a
    # list. Filtered rather than `all(rows)` so the rows below are known non-None.
    found = [row for row in rows if row]
    if len(found) != len(rows):
        return []
    return [
        ApexApplication(
            owner        = str(row.get("owner") or ""),
            workspace    = str(row.get("workspace") or ""),
            app_group    = str(row.get("app_group") or ""),
            app_id       = app,
            app_alias    = str(row.get("app_alias") or ""),
            app_name     = str(row.get("app_name") or ""),
            pages        = None,
            updated_at   = str(row.get("updated_at") or ""),
        )
        for app, row in zip(apps, found, strict=True)
    ]


def _discover_apex_applications(
    gateway: QueryGateway,
    owner: str,
    apps: list[int],
) -> list[ApexApplication]:
    """Full discovered rows for ``apps``, in ``apps`` order."""
    discovered = listed_applications(ApexDiscovery(gateway), [owner], app_ids=apps)
    by_id = {application.app_id: application for application in discovered}
    return [by_id[app] for app in apps if app in by_id]


def _apex_app_labels(apps: list[int], applications: list[ApexApplication]) -> list[str]:
    """``id/ALIAS`` display labels, falling back to a bare id when undiscovered."""
    by_id = {application.app_id: application for application in applications}
    labels: dict[int, str] = {}
    for app in apps:
        application = by_id.get(app)
        if application is None:
            labels[app] = str(app)
        else:
            alias = application.app_alias or application.app_name or str(app)
            labels[app] = f"{app}/{alias.upper()}"
    return [labels[app] for app in apps]


__all__ = [name for name in globals() if not name.startswith("__")]
