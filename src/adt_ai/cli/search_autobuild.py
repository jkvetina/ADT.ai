"""`search` builds the store it answers from rather than naming the rebuild (ADT #900).

Jan, 2026-09-19: *"When user ask for search in app and we dont have it or it
is not fresh, we should not give him a quest, we should make it work!"* A page
or `-app` question about an application the local stores do not hold, or hold
from before its last change in the database, first runs the refresh
`rebuild -app APP` runs, on the same screen, then answers. An object question
with no dependency mirror at all builds it the way a bare `rebuild` does.

**Fresh is measured, never assumed from a clock.** The database says how many
seconds ago the application last changed, on its own clock; the store says when
this machine last refreshed it, on this one. The application is stale when it
changed more recently than it was refreshed, so an offset between the two
clocks never enters the comparison.

**Only building needs the database.** A store that already holds the
application answers as it stands when no connection resolves, so a checkout
without one keeps searching what it has. A store that holds nothing has no
answer to give, and the refresh stops on the shared configuration screen every
connecting command prints.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from datetime import datetime
from pathlib import Path

from adt_ai.cli.constants import ConnectionConfigError, DependencyStore, GatewayFactory
from adt_ai.cli.context import _load_startup_context
from adt_ai.cli.context_errors import _is_user_database_error
from adt_ai.cli.export_apex_owners import apex_lookup_schema
from adt_ai.cli.rebuild_refresh import plan_database_refresh, run_database_refresh
from adt_ai.cli.refresh_connect import _connecting_mode_gateways
from adt_ai.flow import queries as flow_queries
from adt_ai.shared.announce import mark_announced
from adt_ai.shared.internal_paths import internal_path

_STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _refresh_namespace(
    root: Path, apps: list[str] | None = None, schemas: list[str] | None = None
) -> argparse.Namespace:
    """The arguments `rebuild` would parse for this refresh, and nothing more."""
    return argparse.Namespace(
        root       = str(root),
        config_dir = None,
        env        = None,
        key        = None,
        schema     = [schemas] if schemas else None,
        app        = [apps] if apps else None,
        force      = False,
        debug      = False,
    )


@contextmanager
def _refresh_stream(machine_format: bool) -> Iterator[None]:
    """The refresh is chrome: beside a `-format yaml`/`md` document it goes to stderr.

    The module banner went there too, so on that screen it is still the newest
    thing and announces what the plan reads before its first header prints.
    """
    if not machine_format:
        yield
        return
    with redirect_stdout(sys.stderr):
        mark_announced()
        yield


def refresh_stores(
    root: Path,
    gateway_factory: GatewayFactory | None,
    *,
    machine_format: bool,
    apps: list[str] | None = None,
    schemas: list[str] | None = None,
) -> int:
    """Run `rebuild`'s database half for these applications or schemas, exit code."""
    started_at = time.monotonic()
    with _refresh_stream(machine_format):
        plan = plan_database_refresh(_refresh_namespace(root, apps, schemas), root, gateway_factory)
        if isinstance(plan, int):
            return plan
        return run_database_refresh(plan, first_started_at=started_at)


def refresh_stamps(root: Path, scope_type: str) -> dict[str, str]:
    """`scope -> last refresh` for one kind of stamp the dependency mirror keeps."""
    db_path = internal_path(root, "dependencies.db")
    if not db_path.exists():
        return {}
    with DependencyStore.open(db_path) as store:
        return {
            row["scope"]: row["last_refresh"]
            for row in store.last_refreshes()
            if row["type"] == scope_type
        }


def _app_stamps(root: Path, scope_type: str = "app") -> dict[int, str]:
    return {
        int(scope): stamp
        for scope, stamp in refresh_stamps(root, scope_type).items()
        if scope.isdigit()
    }


def stamped_apps(root: Path) -> set[int]:
    """The applications `rebuild -app` has scanned into the dependency mirror."""
    return set(_app_stamps(root))


def refreshed_seconds_ago(stamp: str | None) -> float | None:
    if not stamp:
        return None
    try:
        return (datetime.now() - datetime.strptime(stamp, _STAMP_FORMAT)).total_seconds()
    except ValueError:
        return None


def changed_apps(
    root: Path,
    app_ids: list[int],
    gateway_factory: GatewayFactory | None,
    scope_type: str = "app",
) -> list[int]:
    """The applications changed in the database since this store refreshed them.

    An application with no readable refresh stamp counts as changed, since
    nothing says the store is current. No connection, or a database that does
    not answer, means nothing can be measured, and the store answers as it
    stands.
    """
    if not app_ids:
        return []
    stamps = _app_stamps(root, scope_type)
    try:
        startup = _load_startup_context(_refresh_namespace(root))
        connections = startup.connections
        environment = connections.default_environment
        lookup_schema = apex_lookup_schema(
            connections, environment, connections.schema_names(environment)
        )
        _connection_for, factory = _connecting_mode_gateways(
            startup, environment, gateway_factory, debug=False, kind="apex"
        )
        gateway = factory(lookup_schema)
        changed: list[int] = []
        for app_id in app_ids:
            refreshed = refreshed_seconds_ago(stamps.get(app_id))
            rows = gateway.fetch_all(flow_queries.APP_CHANGED_QUERY, {"app_id": app_id})
            seconds = rows[0].get("SECONDS_AGO") if rows else None
            if refreshed is None or (seconds is not None and float(seconds) < refreshed):
                changed.append(app_id)
        return changed
    except ConnectionConfigError:
        return []
    except Exception as error:
        # A database that does not answer (no VPN, no client, a listener down)
        # is the same case as no connection: the store answers as it stands.
        if _is_user_database_error(error):
            return []
        raise


__all__ = [name for name in globals() if not name.startswith("__")]
