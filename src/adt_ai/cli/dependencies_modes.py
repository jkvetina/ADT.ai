"""What mode a ``dependencies`` invocation names, and how the connecting ones connect.

Split out of `commands_dependencies` by `#751`, which is the card that took that
file past the 24 000 byte context guard: it arrived carrying a third mode, and
the seam was already there to be cut. Everything here answers one of two
questions every mode has to ask before it does anything -- *which mode is this?*
and *what am I connecting through?* -- and both answers are shared by `-refresh`
and `-scan`, which is exactly why a second copy of either is a defect waiting to
happen rather than a convenience.

The query modes read the mirror offline and reach only the first question.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, redirect_stdout
from typing import Any

from adt_ai.cli.constants import (
    ConnectionConfigError,
    ConnectionResult,
    GatewayFactory,
    QueryGateway,
)
from adt_ai.cli.context import ApexAppSelection, _app_in_selection
from adt_ai.cli.export_apex_owners import listed_applications
from adt_ai.cli.gateways import build_gateway, cached_schema_gateway_factory
from adt_ai.export_apex.inventory import ApexDiscovery
from adt_ai.shared.connections import Connection


@contextmanager
def _refresh_chrome_stream(machine_format: bool) -> Iterator[None]:
    """Where one connecting segment's console output goes.

    A refresh prints no document at all: the connection block, both headers, the
    APEX applications table and the runner's progress rows are chrome to the last
    byte. Under `-format yaml`/`md` that whole screen belongs on stderr beside the
    timer, so `dependencies -refresh -format yaml` leaves stdout empty and stays
    pipeable, which is what the routing beside `timer_stdout` already claimed and
    only the footer honoured (`#656`).

    Redirecting the segment keeps that one decision in one place instead of a
    `file=` threaded through five printers, and the caller wraps the segment body
    rather than the `run_schema_sections` call: that one marks its final-timer
    latch on the real `sys.stdout` after the loop, and a redirect spanning it
    would set the latch on stderr and earn the run a second `TIMER` footer.

    `-scan` wraps the same way and for the same reason, with one difference it
    states at its own call site: its document is not chrome, so it prints after
    the block rather than inside it.
    """
    if not machine_format:
        yield
        return
    with redirect_stdout(sys.stderr):
        yield


def _query_requested(args: argparse.Namespace) -> bool:
    """True when the invocation names a query mode, so it reads the mirror offline.

    The one question the command asks of its own arguments, and the reason it is
    a named helper rather than an inline chain: the dispatcher's argument check
    and the command body both decide refresh against query, and a second spelling
    of the same list is how two such decisions drift apart.
    """
    return bool(
        args.uses
        or args.used_by
        or args.impact
        or args.tree
        or getattr(args, "age", False)
    )


def _mode_requested(args: argparse.Namespace) -> bool:
    """True when the invocation names one of the three modes (ADT #751).

    The refusal and the dispatcher both have to agree on what "named a mode"
    means, or the command refuses invocations it would have run, or runs ones it
    said it refused. So it is asked once, here, beside `_query_requested` for
    the same reason that one is.

    `-refresh` is in the list rather than being what is left over when nothing
    else matched. It was the default until this card, which made it the one mode
    reachable by accident: a bare `dependencies`, a mistyped query, or `-app 100`
    on its own all connected and rebuilt the mirror. Jan: *"With -scan added, the
    -refresh should be mandatory (not implied)"*.
    """
    return bool(
        getattr(args, "scan", False)
        or getattr(args, "refresh", None) is not None
        or _query_requested(args)
    )


def _connecting_mode_gateways(
    startup: Any,
    environment: str | None,
    gateway_factory: GatewayFactory | None,
    *,
    debug: bool,
) -> tuple[Callable[[str], Connection], GatewayFactory]:
    """The per-schema connection cache and gateway factory both live modes use.

    `-refresh` and `-scan` connect the same way and for the same reason, so the
    wiring is written once: a connection resolved at most once per schema, the
    injected factory when a caller supplied one, and `cached_schema_gateway_
    factory` around whichever it is so `-debug` keeps the nesting `build_gateway`
    documents (`#670`). Two copies of this is how one mode comes to cache what
    the other resolves twice.
    """
    connections = startup.connections
    connection_cache: dict[str, Connection] = {}

    def connection_for(schema: str) -> Connection:
        if schema not in connection_cache:
            connection_cache[schema] = connections.resolve(
                environment=environment, schema=schema
            )
        return connection_cache[schema]

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, connection_for(schema))

    return connection_for, cached_schema_gateway_factory(
        gateway_factory or default_gateway_factory, debug=debug
    )


def _refresh_lookup_schema(
    connections: ConnectionResult,
    environment: str | None,
) -> str | None:
    """The schema a connecting mode reads the APEX inventory through.

    The configured default wins, else the first schema the environment lists,
    else nothing. ``default_schemas`` RAISES on an unconfigured default rather
    than returning an empty list, so written inline as
    ``defaults[0] if defaults else configured[0]`` the fallback can never run:
    a connection file carrying ``schemas:`` and no ``defaults:`` failed
    ``dependencies -refresh -app 100-200`` on the configuration screen instead
    of reading its first schema (`#670`). Same shape, and the same reason, as
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
    configured schemas and filtered with ``_app_in_selection``, the same shape
    ``_refresh_flow`` uses (cli_commands_flow.py) so the two commands agree on
    range semantics.
    """
    if selection is None:
        return []
    if not selection.has_ranges:
        return [int(app_id) for app_id in selection.explicit_ids]

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
    """Flatten repeated/comma-joined refresh names into unique uppercase values."""
    names: list[str] = []
    for value in raw or []:
        for part in str(value).split(","):
            part = part.strip().upper()
            if part and part not in names:
                names.append(part)
    return names


__all__ = [name for name in globals() if not name.startswith("__")]
