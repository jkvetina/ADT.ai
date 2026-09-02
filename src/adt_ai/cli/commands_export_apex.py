"""The two things `export_apex` does, each in its own function.

`_run_export_apex` resolves one set of inputs (which schemas, which apps, which
actions, which connection) and then runs one of two entirely different screens
with them: `-reveal` is a single cross-schema inventory read over one
connection, and an export is a per-schema segment loop that can grow its own
schema list as it goes. Sharing a `return` in the middle of one 265-line
function made the second half read as a continuation of the first, which it
never is, and put both modes' locals in one scope where only the resolution
above them is common (`#670`).

So the resolution stays in `commands_exports.py`, lands in `ApexRun`, and each
mode is a function that takes it. The seam is the `-reveal` early return that
was already there; `commands_export_db_groups.py` is the same split made for
`export_db -groups`, and for the same 24 KB per-file budget.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from adt_ai.cli.constants import (
    ApexApplication,
    ApexDiscovery,
    ApexExportRequest,
    ApexExportRunner,
    QueryGateway,
)
from adt_ai.cli.context import (
    ApexAppSelection,
    _apex_scope,
    _app_in_selection,
    _flatten_arg_groups,
    _print_connection_block,
)
from adt_ai.cli.context_apex import ApexScope
from adt_ai.cli.export_apex_messages import (
    print_apex_app_not_found,
    print_apex_owner_not_configured,
)
from adt_ai.cli.export_apex_owners import _resolve_apex_app_owners
from adt_ai.cli.export_apex_reveal import print_reveal_screen
from adt_ai.cli.export_reporters import ConsoleApexRevealReporter
from adt_ai.cli.schema_sections import run_schema_sections
from adt_ai.export_apex.deep import ApexDeepFilterError
from adt_ai.export_apex.filters import ApexComponentFilter, ApexPageSelection
from adt_ai.export_apex.schema_level import schema_level_only
from adt_ai.shared.connections import Connection, ConnectionResult


@dataclass(frozen=True)
class ApexRun:
    """Everything `_run_export_apex` resolved before it knew which mode it was.

    Frozen because the resolution is finished by the time either mode starts;
    the export loop still appends to `schemas` and writes into
    `schema_connections` / `schema_scope`, which is the owner routing described
    in `_route_missing_apps` and is why those three are containers rather than
    values.
    """

    args              : argparse.Namespace
    root              : Path
    config            : Mapping[str, object]
    connections       : ConnectionResult
    environment       : str
    schemas           : list[str]
    schema_connections: dict[str, Connection]
    schema_scope      : dict[str, ApexScope]
    gateway_factory   : Callable[[str], QueryGateway]
    connection_schema : str
    app_selection     : ApexAppSelection | None
    actions           : dict[str, bool]
    explicit_actions  : frozenset[str]
    recent_days       : int | None
    recent_report_only: bool
    page_selection    : ApexPageSelection | None
    component_filters : tuple[ApexComponentFilter, ...]
    my_name           : str | None
    my_email          : str | None
    started_at        : float
    applications_by_schema: dict[str, list[ApexApplication]] = field(default_factory=dict)

    @property
    def has_app_ranges(self) -> bool:
        return bool(self.app_selection and self.app_selection.has_ranges)

    def in_selection(self, applications: list[ApexApplication]) -> list[ApexApplication]:
        """`applications` narrowed to a `MIN-MAX` / `MIN+` range, if one was given.

        A range is resolved in Python rather than in SQL, so both modes filter
        what came back the same way; two spellings of that filter is how the
        two modes would start disagreeing about what a range means.
        """
        if not self.has_app_ranges or self.app_selection is None:
            return applications
        return [
            application
            for application in applications
            if _app_in_selection(application.app_id, self.app_selection)
        ]


def run_apex_reveal(run: ApexRun) -> int:
    """The `-reveal` inventory screen: one connection, one shared teardown footer.

    Untouched by the per-schema segmenting the export mode does, which is what
    makes it a mode rather than a flag.
    """
    args = run.args
    reporter = ConsoleApexRevealReporter()
    _print_connection_block(
        run.gateway_factory(run.connection_schema),
        run.schema_connections[run.connection_schema],
        debug=args.debug,
    )
    # The first of the three tables opens its section before any of them is
    # read, so the screen names the inventory being gathered instead of
    # parking on the connection block (`#372`).
    reporter.begin_workspaces()
    # -reveal is an inventory screen, so only an explicit -ws narrows it
    # (`#564`, Jan: "-reveal should always reveal all workspaces and apps,
    # UNLESS -ws is passed"). `apex.workspace` still scopes the per-schema
    # EXPORT below; here it only marks the ACTIVE row, because a wrong value
    # in that key used to filter both reads to nothing and leave the screen
    # empty at exit 0. `-group` and `-app` keep reading the file either way.
    reveal_workspace = args.ws or None
    configured_workspace = _apex_scope(
        run.schema_connections[run.connection_schema].apex
    ).workspace
    for schema in run.schemas:
        discovery = ApexDiscovery(run.gateway_factory(schema))
        scope = run.schema_scope[schema]
        applications = discovery.applications(
            owner     = schema,
            workspace = reveal_workspace,
            group     = scope.group,
            app_ids   = scope.app_ids,
            recent_days = run.recent_days,
            max_app_id = args.max_app_id,
        )
        run.applications_by_schema[schema] = run.in_selection(applications)
    print_reveal_screen(
        ApexDiscovery(run.gateway_factory(run.connection_schema)),
        reporter,
        run.schemas,
        run.applications_by_schema,
        workspace            = reveal_workspace,
        configured_workspace = configured_workspace,
        is_filtered          = bool(args.app) or bool(args.schema),
        widen_owner_counts   = bool(args.owners),
        max_app_id           = args.max_app_id,
    )
    return 0


def run_apex_export(run: ApexRun) -> int:
    """The export mode: each schema is its own console segment.

    Connection block -> discovery -> export -> TIMER, driven by the shared
    per-schema-section helper. `run.schemas` is mutated in place by the
    missing-app owner routing below, and the helper's lazy iteration picks up
    whatever gets appended.
    """
    args = run.args
    reporter = ConsoleApexRevealReporter()
    initial_schema_count = len(run.schemas)
    processed = 0

    def run_one(schema: str) -> int:
        nonlocal processed
        processed += 1
        versions = _print_connection_block(
            run.gateway_factory(schema), run.schema_connections[schema], debug=args.debug
        )
        discovery = ApexDiscovery(run.gateway_factory(schema))
        scope = run.schema_scope[schema]
        applications = discovery.applications(
            owner     = schema,
            workspace = scope.workspace,
            group     = scope.group,
            app_ids   = scope.app_ids,
            recent_days = None,
            max_app_id = args.max_app_id,
        )
        run.applications_by_schema[schema] = run.in_selection(applications)
        # Exports no application, so it lists none (`schema_level_only`).
        if not schema_level_only(run.actions):
            reporter.applications(schema, run.applications_by_schema[schema])

        # Missing-app owner routing runs once, inside the last originally
        # requested schema's segment, after its own export/before its timer.
        if processed == initial_schema_count and not run.has_app_ranges:
            _route_missing_apps(run, schema)

        if any(run.actions.values()) or run.recent_report_only:
            try:
                _export_one_schema(run, schema, versions)
            except ApexDeepFilterError as exc:
                print(f"export_apex: {exc}", file=sys.stderr)
                return 2
        return 0

    return run_schema_sections(run.schemas, run_one, first_started_at=run.started_at)


def _route_missing_apps(run: ApexRun, schema: str) -> None:
    """Give each requested app nobody listed a segment on its own owner schema.

    A newly-routed owner is appended to `run.schemas` and gets its own full
    segment (connection block, discovery, export, timer) the next time the
    helper's loop reaches it; it is never spliced into an already-completed
    segment.

    **A schema already in the list is not appended again** (`#670`). The scope
    this rebuilds is composed from the same `-app`/`-ws`/`-group` the first pass
    used, so a re-run of a schema the invocation already named repeats a read
    that has already returned nothing, and pays a second connection block and a
    second `TIMER` to print the same empty answer.
    """
    args = run.args
    requested_app_ids = _flatten_arg_groups(args.app)
    if not requested_app_ids:
        return
    found_ids = {
        str(application.app_id)
        for apps in run.applications_by_schema.values()
        for application in apps
    }
    missing_app_ids = [
        app_id for app_id in requested_app_ids if str(app_id) not in found_ids
    ]
    if not missing_app_ids:
        return
    owner_discovery = ApexDiscovery(run.gateway_factory(schema))
    owner_to_app_ids, not_configured, not_found = _resolve_apex_app_owners(
        owner_discovery,
        missing_app_ids,
        run.connections.schema_names(run.environment),
    )
    for owner_schema, owner_app_ids in owner_to_app_ids.items():
        if owner_schema in run.schemas:
            continue
        connection = run.schema_connections.get(owner_schema)
        if connection is None:
            connection = run.connections.resolve(
                environment=run.environment, schema=owner_schema, kind="apex"
            )
            run.schema_connections[owner_schema] = connection
        run.schema_scope[owner_schema] = _apex_scope(
            connection.apex,
            workspace = args.ws,
            group     = args.group,
            app_ids   = owner_app_ids,
        )
        run.schemas.append(owner_schema)
    for app_id, owner in not_configured:
        print_apex_owner_not_configured(app_id, owner, run.environment)
    for app_id in not_found:
        print_apex_app_not_found(app_id)


def _export_one_schema(run: ApexRun, schema: str, versions: dict[str, str]) -> None:
    args = run.args
    ApexExportRunner(run.gateway_factory).run(
        ApexExportRequest(
            root         = run.root,
            schemas      = [schema],
            applications = {schema: run.applications_by_schema[schema]},
            actions      = run.actions,
            explicit_actions = run.explicit_actions,
            config       = run.config,
            release      = args.release,
            recent       = run.recent_days,
            environment  = run.environment,
            changed_by   = args.by or None,
            my_changes   = args.my,
            my_name      = run.my_name,
            my_email     = run.my_email,
            recent_report_only=run.recent_report_only,
            page_selection=run.page_selection,
            component_filters=run.component_filters,
            deep=args.deep,
            # Already probed by the connection block above, the 26.1 format
            # gates read it rather than asking the DB again.
            apex_version=versions.get("APEX"),
            compact=args.compact,
        )
    )


__all__ = [name for name in globals() if not name.startswith("__")]
