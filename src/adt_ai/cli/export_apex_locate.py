"""Where the applications named on `-reveal` actually live (`#858`).

Jan, 2026-09-15: *"ADT export_apex -reveal 430 431 should show where are these
applications present - they might be in a schema which we dont have on the
connection list, so this should be also clear from the outcome"*, and on how to
look: *"All owners one by one, stop if we find it. There should be a way how to
list apex apps without setting security context, so at least you should be able
to reach the same workspace apps."*

The configured owners are read first, exactly as a bare `-reveal` reads them.
Only the ids none of them listed are looked up, by id and with no owner filter,
through the reveal's own connection first and then through each other configured
schema's connection. A session sees the applications of every workspace its
schema is mapped to, so each connection can reach owners the connection file
never names.

The search stops once every id has a configured owner. An id owned by a schema
the file does not name keeps it going through every connection, because the
warning names each schema that reaches it, sorted. Jan, 2026-09-15: *"It is
incomplete list, I would like to see other schemas listed (and sorted)"*
(`#863`).

Nothing is printed here. `print_reveal_screen` prints every table after every
read, which is the ordering `#360` and `#372` settled, so this only reads and
reports what it found.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from adt_ai.cli.context import _flatten_arg_groups
from adt_ai.export_apex.inventory import ApexApplication, ApexDiscovery

if TYPE_CHECKING:
    from adt_ai.cli.commands_export_apex import ApexRun


@dataclass
class LocatedApps:
    """What the id search added to the reveal.

    `owners` are the schemas the screen lists beyond the ones it scanned, in the
    order they were found. `not_configured` holds an app id, an owner the
    connection file does not name and every configured schema whose connection
    found it, sorted; `not_found` is what no connection could see.
    """

    owners        : list[str] = field(default_factory=list)
    not_configured: list[tuple[str, str, list[str]]] = field(default_factory=list)
    not_found     : list[str] = field(default_factory=list)


def locate_revealed_apps(run: ApexRun) -> LocatedApps:
    """Search for the `-app`/`-reveal` ids the configured owners did not list.

    A range (`MIN-MAX`, `MIN+`) names no finite set to look for, so a run with
    one searches nothing, the same limit the export's owner routing keeps.
    """
    located = LocatedApps()
    if run.has_app_ranges or run.schema_gateway_factory is None:
        return located
    listed = {
        str(application.app_id)
        for applications in run.applications_by_schema.values()
        for application in applications
    }
    requested = dict.fromkeys(
        str(app_id) for app_id in _flatten_arg_groups(run.args.app) or []
    )
    missing = [app_id for app_id in requested if app_id not in listed]
    if not missing:
        return located
    configured = run.connections.schema_names(run.environment)
    configured_by_name = {name.upper(): name for name in configured}
    search_order = [
        run.connection_schema,
        *(schema for schema in configured if schema != run.connection_schema),
    ]
    # Unconfigured owner -> (owner, schemas that reached it), in found order.
    reached: dict[str, tuple[str, list[str]]] = {}
    for schema in search_order:
        if schema not in run.schema_connections:
            run.schema_connections[schema] = run.connections.resolve(
                environment=run.environment, schema=schema, kind="apex"
            )
        discovery = ApexDiscovery(run.schema_gateway_factory(schema))
        for application in discovery.applications_by_id(missing):
            app_id = str(application.app_id)
            if app_id not in missing:
                continue
            if app_id not in reached:
                if _place(run, located, application, configured_by_name):
                    missing.remove(app_id)
                    continue
                reached[app_id] = (application.owner, [])
            schemas = reached[app_id][1]
            if schema not in schemas:
                schemas.append(schema)
        if not missing:
            break
    located.not_configured = [
        (app_id, owner, sorted(schemas)) for app_id, (owner, schemas) in reached.items()
    ]
    located.not_found = [app_id for app_id in missing if app_id not in reached]
    return located


def _place(
    run: ApexRun,
    located: LocatedApps,
    application: ApexApplication,
    configured_by_name: dict[str, str],
) -> bool:
    """File a found application under its owner; True when the owner is configured."""
    configured_owner = configured_by_name.get(application.owner.upper())
    owner = configured_owner or application.owner
    if owner not in run.applications_by_schema:
        run.applications_by_schema[owner] = []
        located.owners.append(owner)
    run.applications_by_schema[owner].append(application)
    return configured_owner is not None


__all__ = [name for name in globals() if not name.startswith("__")]
