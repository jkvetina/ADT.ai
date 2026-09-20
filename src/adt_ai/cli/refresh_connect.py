"""How the two commands that read the database for a cache connect (`#30`).

`rebuild` refreshes the dependency mirror and the page picture, and `validate
-scan` compiles a live application. Both resolve `-app` ids and ranges, both
pick the one schema an application is read through, and both open one gateway
per schema, so the wiring is written once here. Two copies of it is how one
command comes to cache what the other resolves twice, and how two commands come
to disagree about what `-app 100-200` means.

Split out of `commands_dependencies` by `#751` as `dependencies_modes.py`; the
mode questions it also answered left with the `dependencies` command (`#30`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adt_ai.cli.constants import (
    ConnectionConfigError,
    ConnectionResult,
    GatewayFactory,
    QueryGateway,
)
from adt_ai.cli.context import (
    ApexAppSelection,
    _app_in_selection,
    _flatten_arg_groups,
    _parse_apex_app_selection,
    _parse_apex_page_selection,
)
from adt_ai.cli.gateways import build_gateway, cached_schema_gateway_factory
from adt_ai.shared.connections import Connection


def _app_selection_error(raw: list[list[str]] | None) -> str | None:
    """Why an `-app` value cannot be read, or `None` when it can.

    Returned to the dispatcher before the command runs, so a malformed range or
    an id that is not a number lands on the parser-style error screen rather than
    inside a handler that already connected. Ranges (MIN-MAX / MIN+) are parsed
    by the same reader `export_apex` uses; explicit ids must be numeric.
    """
    try:
        selection = _parse_apex_app_selection(_flatten_arg_groups(raw))
    except ValueError as exc:
        return str(exc)
    if selection is not None:
        for app_id in selection.explicit_ids:
            if not app_id.isdigit():
                return f"invalid APP_ID: {app_id}"
    return None


def _page_selection_error(raw: list[list[str]] | None) -> str | None:
    """Why a `-page` value cannot be read, or `None`: the `-app` refusal's twin.

    Without it a page written `abc` reached the handler, after the banner and
    the connection, and ended as Python's own `invalid literal for int()`.
    A range the shared reader rejects keeps that reader's sentence. `validate
    -scan` and `search -app` both read `-page` through it (`#30`).
    """
    values = _flatten_arg_groups(raw) or []
    for token in (part.strip() for value in values for part in value.split(",")):
        try:
            _parse_apex_page_selection([token])
        except ValueError as error:
            message = str(error)
            return message if message.startswith("invalid -page") else f"invalid PAGE_ID: {token}"
    return None


def _connecting_mode_gateways(
    startup: Any,
    environment: str | None,
    gateway_factory: GatewayFactory | None,
    *,
    debug: bool,
    kind: str = "db",
) -> tuple[Callable[[str], Connection], GatewayFactory]:
    """The per-schema connection cache and gateway factory a connecting run uses.

    A connection resolved at most once per schema, the injected factory when a
    caller supplied one, and `cached_schema_gateway_factory` around whichever it
    is so `-debug` keeps the nesting `build_gateway` documents (`#670`). `kind`
    is the connection flavour: the page picture reads APEX through the
    application's parsing schema, the dependency mirror through the database one.
    """
    connections = startup.connections
    connection_cache: dict[str, Connection] = {}

    def connection_for(schema: str) -> Connection:
        if schema not in connection_cache:
            connection_cache[schema] = connections.resolve(
                environment=environment, schema=schema, kind=kind
            )
        return connection_cache[schema]

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, connection_for(schema), project_root=startup.root)

    return connection_for, cached_schema_gateway_factory(
        gateway_factory or default_gateway_factory, debug=debug
    )


def _refresh_lookup_schema(
    connections: ConnectionResult,
    environment: str | None,
) -> str | None:
    """The schema a connecting run reads the APEX inventory through.

    The configured default wins, else the first schema the environment lists,
    else nothing. ``default_schemas`` RAISES on an unconfigured default rather
    than returning an empty list, so written inline as
    ``defaults[0] if defaults else configured[0]`` the fallback can never run:
    a connection file carrying ``schemas:`` and no ``defaults:`` failed an
    application range on the configuration screen instead of reading its first
    schema (`#670`). Same shape, and the same reason, as
    ``export_apex_owners.apex_lookup_schema``.
    """
    try:
        defaults = connections.default_schemas(environment)
    except ConnectionConfigError:
        defaults = []
    if defaults:
        return defaults[0]
    configured = connections.schema_names(environment)
    return configured[0] if configured else None


def _resolve_refresh_app_ids(
    selection: ApexAppSelection | None,
    connections: ConnectionResult,
    environment: str | None,
    gateway_factory: GatewayFactory,
) -> list[int]:
    """Resolve the ``-app`` selection into a unique, ordered list of app ids.

    No selection → no apps. Explicit ids (no ranges) pass straight through. A
    range (MIN-MAX / MIN+) is resolved against apps discovered across the
    configured schemas and filtered with ``_app_in_selection``, so `rebuild` and
    `validate -scan` agree on range semantics with `export_apex`.

    The APEX inventory is imported here, on the one path that reads it, and not
    at module scope (ADT #895). This module ships in every release and neither
    half of the inventory does, so a module-scope import failed every command of
    a release without `export_apex`, `--help` included.
    """
    if selection is None:
        return []
    if not selection.has_ranges:
        return [int(app_id) for app_id in selection.explicit_ids]

    from adt_ai.cli.export_apex_owners import listed_applications
    from adt_ai.export_apex.inventory import ApexDiscovery

    configured_schemas = connections.schema_names(environment)
    lookup_schema = _refresh_lookup_schema(connections, environment)
    if lookup_schema is None:
        return []
    discovery = ApexDiscovery(gateway_factory(lookup_schema))
    seen: set[int] = set()
    app_ids: list[int] = []
    for app in listed_applications(discovery, configured_schemas):
        if _app_in_selection(app.app_id, selection) and app.app_id not in seen:
            seen.add(app.app_id)
            app_ids.append(app.app_id)
    return app_ids


def _resolve_refresh_names(raw: list[str] | None) -> list[str]:
    """Flatten repeated/comma-joined names into unique uppercase values."""
    names: list[str] = []
    for value in raw or []:
        for part in str(value).split(","):
            part = part.strip().upper()
            if part and part not in names:
                names.append(part)
    return names


__all__ = [name for name in globals() if not name.startswith("__")]
