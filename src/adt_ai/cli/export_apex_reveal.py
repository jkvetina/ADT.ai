"""The `-reveal` screen: three tables assembled from reads already made.

Split out of `commands_exports.py` at the 20 KB per-file context budget, along
the seam that module's own comment always drew: `-reveal` is a single
cross-schema inventory screen, not a per-schema export, and nothing below it in
`run_export_apex` touches this path. What stayed behind is the reading, the
connection block and the per-schema `applications()` loop, because those need
the run's gateway factory and its filter arguments; what moved is everything
that happens once the applications are known.

Ordering here is load-bearing and was arrived at twice. Every read runs before
any table prints, because reporting between two reads put a row under a header
it had nothing to do with (`#360`, live run); the rows those two reads grew are
gone again (`#372`), and this ordering is what replaced them.
"""

from __future__ import annotations

from collections.abc import Iterable

from adt_ai.cli.export_reporters import ConsoleApexRevealReporter
from adt_ai.export_apex.inventory import (
    ApexApplication,
    ApexDiscovery,
    ApexWorkspace,
    with_derived_workspaces,
)

#: Where an unmatched workspace name came from, in the words the reader can act
#: on: one is an argument they just typed, the other a key they have to go and
#: find.
WS_ARGUMENT = "-ws"
WS_CONNECTION_FILE = "apex.workspace in the connection file"


def print_reveal_screen(
    discovery: ApexDiscovery,
    reporter: ConsoleApexRevealReporter,
    schemas: Iterable[str],
    applications_by_schema: dict[str, list[ApexApplication]],
    workspace: str | None = None,
    configured_workspace: str | None = None,
    is_filtered: bool = False,
    widen_owner_counts: bool = False,
    max_app_id: int | None = None,
) -> None:
    """Print `WORKSPACES:`, `APPLICATIONS PER LISTED OWNERS:`, then each schema.

    ``workspace`` is `-ws` and is the **only** thing that narrows this screen
    (`#564`, Jan: *"-reveal should always reveal all workspaces and apps, UNLESS
    -ws is passed"*). ``configured_workspace`` is the connection file's
    `apex.workspace`: it marks the `ACTIVE` row and is reported when it names
    nothing, but it no longer filters, because a wrong value there used to empty
    the whole screen at exit `0` with nothing said.

    ``is_filtered`` is the run having named a `-app` or a `-schema`: the two
    overview tables then narrow to what the applications actually reached, so a
    filtered screen does not head its own answer with the whole instance.
    ``widen_owner_counts`` is `-owners`, which drops the schema filter from the
    owner counts alone; it has never applied to the application list.
    """
    schemas = list(schemas)
    active_workspaces = {
        application.workspace
        for applications in applications_by_schema.values()
        for application in applications
    }
    schema_filter = None if is_filtered else schemas
    all_workspaces = discovery.workspaces(
        workspace=workspace, schemas=schema_filter, max_app_id=max_app_id
    )
    all_workspaces = with_derived_workspaces(
        discovery,
        all_workspaces,
        configured_workspace=workspace,
        named_by_applications=active_workspaces,
        schemas=schema_filter,
        max_app_id=max_app_id,
    )
    owner_filter = None if widen_owner_counts else schemas
    all_owner_counts = discovery.owner_app_counts(owner_filter, max_app_id=max_app_id)
    listed = (
        [w for w in all_workspaces if w.workspace in active_workspaces]
        if (is_filtered and active_workspaces) else all_workspaces
    )
    reporter.workspaces(listed, configured_workspace=configured_workspace)
    unmatched = _unmatched_workspace(listed, workspace, configured_workspace)
    if unmatched:
        reporter.workspace_not_found(*unmatched)
    active_owners = {s for s, apps in applications_by_schema.items() if apps}
    reporter.owner_counts(
        [oc for oc in all_owner_counts if oc.owner in active_owners]
        if (is_filtered and active_owners) else all_owner_counts
    )
    for schema in schemas:
        reporter.applications(schema, applications_by_schema[schema])


def _unmatched_workspace(
    listed: list[ApexWorkspace],
    workspace: str | None,
    configured_workspace: str | None,
) -> tuple[str, str] | None:
    """The workspace someone named that the screen could not find (`#564`).

    `-ws` outranks the connection file: while it is passed it is the only thing
    narrowing the table, so a configured value that differs from it is not
    missing, it is overridden, and reporting it would be noise on a screen the
    user deliberately scoped. Without it the connection file names the `ACTIVE`
    row and nothing else, and a value no row matches is exactly the silence this
    card was filed to end.
    """
    if workspace:
        return None if listed else (workspace, WS_ARGUMENT)
    if not configured_workspace:
        return None
    wanted = configured_workspace.upper()
    if any(listed_workspace.workspace.upper() == wanted for listed_workspace in listed):
        return None
    return (configured_workspace, WS_CONNECTION_FILE)


__all__ = [name for name in globals() if not name.startswith("__")]
