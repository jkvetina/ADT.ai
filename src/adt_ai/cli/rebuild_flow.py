"""`rebuild -app`'s page picture: the links between an application's pages (`#30`).

Moved here from the retired `flow` command's refresh. The refresh reads the
application, its pages and its navigation edges into `flow.db`, then writes the
Mermaid, DOT and JSON diagrams under `config/flow/` that `search -to APP.PAGE`
and `-from APP.PAGE` answer from.

It runs in two halves because the screen needs it to. **Every owner lookup
happens in the plan, under the module banner** (`#372`): one dictionary read per
application, and interleaved with the refresh loop the second one onwards landed
under the previous application's finished table with the screen saying nothing.
The refresh half then runs inside `rebuild`'s application segment, one
application at a time, right after that application's dependency scan and under
the header the scan opened. Jan, 2026-09-19: one `APP <id>/<name>, REFRESHING:`
per application, closed by `APP <id>/<name>, REFRESHED:`; the empty second
header the page links used to open is gone.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

from adt_ai.cli.constants import (
    ApexDiscovery,
    ApexOwnerResolutionError,
    GatewayFactory,
    print_adt_header,
    print_adt_table,
    resolve_configured_apex_owner_schema,
)
from adt_ai.cli.context import _print_connection_block
from adt_ai.cli.export_apex_messages import (
    APP_NOT_FOUND_HEADER,
    SCHEMA_NOT_CONFIGURED_HEADER,
    print_apex_app_not_found,
)
from adt_ai.cli.export_apex_owners import apex_lookup_schema
from adt_ai.cli.flow_reporters import _print_warning
from adt_ai.cli.refresh_connect import _connecting_mode_gateways

# The page store straight from its package, not through `cli/constants.py`
# (ADT #895): the hub ships in every release and the store only with `rebuild`
# and `search`, so the hub is to stop re-exporting it.
from adt_ai.flow.files import write_all_dumps
from adt_ai.flow.runner import ApexFlowError, ApexFlowRefreshRequest, ApexFlowRefreshRunner
from adt_ai.flow.store import ApexFlowStore
from adt_ai.shared.connections import Connection
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.progress import schema_label


@dataclass
class FlowPlan:
    """Which schema owns each application, resolved before anything else prints."""

    root: Path
    owner_schemas: dict[int, str]
    connection_for: Callable[[str], Connection]
    gateway_factory: GatewayFactory
    #: An application the lookup could not place, so the run already failed it.
    failed: bool = False
    #: Applications no APEX schema owns: nothing to scan, so none is scanned.
    not_found: tuple[int, ...] = ()
    #: Applications routed to a schema that is not their owner, and to which
    #: (`#907`): the dependency scan reads them through that connection too.
    reached: dict[int, str] = field(default_factory=dict)
    #: Owner schemas whose connection block this run has printed.
    blocks: set[str] = field(default_factory=set)


def plan_flow_refresh(
    startup: Any,
    environment: str,
    app_ids: list[int],
    gateway_factory: GatewayFactory | None,
) -> FlowPlan:
    """Resolve every application's owner schema, and warn about the ones that fail.

    The warnings print here, under the banner, under the two headers
    `export_apex` names these same cases with (`#858`), rather than the
    resolver's bare sentences on stderr (`#861`).
    """
    connections = startup.connections
    configured_schemas = connections.schema_names(environment)
    lookup_schema = apex_lookup_schema(connections, environment, configured_schemas)
    connection_for, flow_gateway_factory = _connecting_mode_gateways(
        startup, environment, gateway_factory, debug=False, kind="apex"
    )
    # The lookup schema first: it answered the owner question, so it is the
    # one connection already known to reach the workspaces this run can see.
    search_order = [
        lookup_schema,
        *(schema for schema in configured_schemas if schema != lookup_schema),
    ]
    owner_schemas: dict[int, str] = {}
    reached_by: dict[int, str] = {}
    not_found: list[str] = []
    not_configured: list[str] = []
    for app_id in app_ids:
        try:
            owner_schemas[app_id] = resolve_configured_apex_owner_schema(
                ApexDiscovery(flow_gateway_factory(lookup_schema)),
                app_id=app_id,
                configured_schemas=configured_schemas,
            ).schema
        except ApexOwnerResolutionError as error:
            # The owner schema is not in the connection file, or the lookup
            # schema does not see the application at all. Neither means it
            # cannot be read: a session sees every application of every
            # workspace its schema is mapped to, so a configured schema that
            # reaches it is read through instead of refusing (ADT #907).
            reached = _schema_that_reaches(flow_gateway_factory, app_id, search_order)
            if reached is not None:
                owner_schemas[app_id] = reached
                reached_by[app_id] = reached
            elif error.owner is None:
                not_found.append(str(app_id))
            else:
                not_configured.append(
                    f"APP {app_id} is owned by {error.owner}, "
                    "and no configured schema reaches it"
                )
    # An app a configured schema reached is read and refreshed, and nothing is
    # printed about it: `export_apex` stays silent on the same case, and for the
    # same reason. Jan, 2026-09-20: "You can access the app from other schemas,
    # so dont bother user with this" (`#909`). Only the app nothing reaches is
    # warned about, because that one did not run.
    _print_warning(SCHEMA_NOT_CONFIGURED_HEADER, not_configured)
    print_apex_app_not_found(not_found)
    return FlowPlan(
        root            = startup.root,
        owner_schemas   = owner_schemas,
        connection_for  = connection_for,
        gateway_factory = flow_gateway_factory,
        failed          = bool(not_found or not_configured),
        not_found       = tuple(int(app_id) for app_id in not_found),
        reached         = reached_by,
    )


def _schema_that_reaches(
    gateway_factory: GatewayFactory, app_id: int, search_order: list[str]
) -> str | None:
    """The first configured schema whose connection lists this application.

    `export_apex -reveal` answers the same question the same way (`#858`):
    the application is asked for by id, with no owner filter, through one
    configured connection after another, and the first that returns it can
    read it.
    """
    for schema in search_order:
        if ApexDiscovery(gateway_factory(schema)).applications_by_id([app_id]):
            return schema
    return None


class FlowRefresh:
    """One segment's page-picture refresh, one application per call.

    Opened around the dependency scan, so each application's page links are read
    the moment its scan is stored and print under the scan's header. `segment_schema`
    is the schema whose connection block heads the segment, so an application
    owned by it opens under that block rather than a second copy of it.
    """

    def __init__(self, plan: FlowPlan, segment_schema: str) -> None:
        self.plan = plan
        self.failed = plan.failed
        #: Applications already handed to `app`, whether or not they refreshed.
        self.seen: set[int] = set()
        plan.blocks.add(segment_schema)
        self._store = ApexFlowStore.open(internal_path(plan.root, "flow.db"))

    def __enter__(self) -> FlowRefresh:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._store.close()

    @property
    def exit_code(self) -> int:
        """1 when any application failed, the plan's own warnings included."""
        return 1 if self.failed else 0

    def app(self, app_id: int, *, header: str | None = None) -> None:
        """Read one application's page links and write its diagrams.

        `header` opens the application's section when nothing above did, which
        is an application the dependency scan skipped (APEX before 24.2); every
        other one continues under the header the scan printed.
        """
        self.seen.add(app_id)
        plan = self.plan
        schema = plan.owner_schemas.get(app_id)
        if schema is None:
            return
        if schema not in plan.blocks:
            _print_connection_block(
                plan.gateway_factory(schema), plan.connection_for(schema), debug=False
            )
            plan.blocks.add(schema)
        if header is not None:
            print_adt_header(header)

        try:
            result = ApexFlowRefreshRunner(plan.gateway_factory).refresh(
                ApexFlowRefreshRequest(app_id=app_id, schema=schema, store=self._store)
            )
        except ApexFlowError:
            # The owner answered and the application read came back empty.
            _print_warning(
                APP_NOT_FOUND_HEADER,
                [f"APP {app_id} is not in its owner schema {schema_label(schema)}"],
            )
            self.failed = True
            return

        dump_paths = write_all_dumps(result.app, result.pages, result.edges, root=plan.root)
        alias = (result.app.app_alias or result.app.app_name or str(result.app.app_id)).upper()
        print_adt_header(f"APP {result.app.app_id}/{alias}, REFRESHED:")
        print_adt_table(
            [
                {
                    "pages": result.page_count,
                    "edges": result.edge_count,
                    "diagrams": len(dump_paths),
                }
            ]
        )


__all__ = [name for name in globals() if not name.startswith("__")]
