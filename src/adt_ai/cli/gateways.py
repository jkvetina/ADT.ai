"""The one place a database gateway is constructed.

Every command that touches Oracle needs the same connection: the shared session
defaults, the ``DBMS_SESSION.SET_IDENTIFIER`` block composed from
``config/IDENTITY.yaml``, ``config/STARTUP.sql``, the configured timeouts, and
the ``-debug`` wrapper. Those all arrive through :class:`StartupContext`, which
is already the single place that *resolves* them.

Before ADT #179 there was no single place that *applied* them: eleven call sites
across seven command modules each wrote their own ``OracleGateway(...)``,
restating ``startup_sql`` / ``config`` / ``project_root`` and their own debug
wrap. Every one of those keywords was optional, so each site could quietly skip
one, and ``dependencies`` did (ADT #177): it never passed ``startup_sql``, ran
no session setup, and died on a schema whose DDL trigger requires a client
identifier. Nothing else was affected, which is precisely the problem: the fix
had to be applied per site, so the *next* addition to session setup would have
to be threaded through eleven places again.

Hence this module. ``build_gateway`` reads what a connection needs from the
context itself rather than from its caller, so a caller cannot decline session
setup, the choice does not exist at the call site. Adding to what every ADT.ai
connection does is now an edit to one function, and every command inherits it.

``tests/contracts/test_gateway_startup_wiring.py`` pins that: exactly one
``OracleGateway(...)`` construction in ``src/adt_ai/``, and it is this one.
"""

from __future__ import annotations

import contextlib
import contextvars
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from adt_ai.cli.context_debug import DebugQueryGateway
from adt_ai.shared.announce import AnnouncedGateway, strict_mode
from adt_ai.shared.config import is_enabled
from adt_ai.shared.connections import Connection
from adt_ai.shared.db import OracleGateway, QueryGateway
from adt_ai.shared.sqlcl_gateway import SqlclGateway

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from adt_ai.cli.context import StartupContext


class GatewayScope:
    """Own every gateway created during one top-level command."""

    def __init__(self) -> None:
        self._gateways: dict[int, QueryGateway] = {}
        self._lock = threading.Lock()

    def track(self, gateway: QueryGateway) -> QueryGateway:
        # A schema factory may cache and return the same gateway many times.
        # Identity deduplication closes that shared session exactly once.
        with self._lock:
            self._gateways.setdefault(id(gateway), gateway)
        return gateway

    def track_factory(
        self, factory: Callable[..., QueryGateway]
    ) -> Callable[..., QueryGateway]:
        def build(*args: object, **kwargs: object) -> QueryGateway:
            return self.track(factory(*args, **kwargs))

        return build

    def close(self) -> None:
        with self._lock:
            gateways = list(self._gateways.values())
            self._gateways.clear()
        # Reverse construction order, matching ExitStack and nested resource
        # ownership. Cleanup is best-effort: a close error after a command has
        # already failed must not replace the actionable failure screen.
        for gateway in reversed(gateways):
            with contextlib.suppress(Exception):
                gateway.close()


_ACTIVE_SCOPE: contextvars.ContextVar[GatewayScope | None] = contextvars.ContextVar(
    "adt_gateway_scope", default=None
)


@contextlib.contextmanager
def gateway_scope() -> Iterator[GatewayScope]:
    """Install the lifecycle owner used by :func:`build_gateway`."""
    scope = GatewayScope()
    token = _ACTIVE_SCOPE.set(scope)
    try:
        yield scope
    finally:
        _ACTIVE_SCOPE.reset(token)
        scope.close()


def _track(gateway: QueryGateway) -> QueryGateway:
    scope = _ACTIVE_SCOPE.get()
    return scope.track(gateway) if scope is not None else gateway


def build_gateway(
    startup: StartupContext,
    connection: Connection,
    *,
    debug: bool = False,
    project_root: Path | None = None,
) -> QueryGateway:
    """Return the gateway for ``connection``, wired from ``startup``.

    ``project_root`` is passed only by the commands that shell out to SQLcl
    (it decides where the generated script runs from); everything else about
    the connection comes from the context and is not a caller's choice.
    """
    # `sqlcl_only` (ADT #396) swaps the transport and nothing else: the same four
    # methods, the same session setup, the credential left in SQLcl's own store so
    # no database password is ever handled inside this process. It is read here
    # rather than at a call site for the reason the whole module exists.
    if is_enabled(startup.config.get("sqlcl_only")):
        gateway: QueryGateway = SqlclGateway(
            connection,
            project_root = project_root,
            startup_sql  = startup.startup_sql,
            config       = startup.config,
        )
    else:
        gateway = OracleGateway(
            connection,
            project_root = project_root,
            startup_sql  = startup.startup_sql,
            config       = startup.config,
        )
    wired = DebugQueryGateway(gateway) if debug else gateway
    # Outermost, so the console guard sees the call before -debug renders it.
    # This is the real path; the injected-factory path is wrapped in
    # cli.runtime.main, and between the two every command is covered.
    final = AnnouncedGateway(wired) if strict_mode() else wired
    return _track(final)


def debug_wrapped(gateway: QueryGateway, *, debug: bool) -> QueryGateway:
    """``gateway`` with the ``-debug`` logger UNDER the console guard (`#670`).

    The nesting is not a call site's choice, for the reason ``build_gateway``
    above states: ``DebugQueryGateway`` prints the statement and then calls
    ``mark_announced()``, so with the logger on the outside every statement
    announces itself before ``AnnouncedGateway.guard`` can judge the screen, and
    a ``-debug`` run reports no console violation whatever it is showing. Seven
    command modules wrote ``DebugQueryGateway(gateway) if args.debug else
    gateway`` by hand over a gateway ``cli.runtime.main`` had already guarded,
    which is exactly that reversed chain.

    An injected factory arrives already guarded, so the guard is unwrapped, the
    logger goes inside, and the guard is put back. A gateway that carries none
    (a run outside ``strict_mode``) gets the logger and nothing else.
    """
    if not debug:
        return gateway
    if isinstance(gateway, AnnouncedGateway):
        return AnnouncedGateway(DebugQueryGateway(gateway.wrapped))
    return DebugQueryGateway(gateway)


def cached_schema_gateway_factory(
    base_factory: Callable[[str], QueryGateway],
    *,
    debug: bool = False,
) -> Callable[[str], QueryGateway]:
    """One gateway per schema, built once and wrapped for ``-debug`` once.

    Every multi-schema command memoised its own ``dict[str, QueryGateway]`` and
    applied its own debug wrap beside it, which is where the reversed chain
    above kept being written (`#670`). The cache and the wrap travel together
    because they are one decision: a schema's gateway is built, wrapped and then
    reused, so a second caller cannot get a differently-wrapped session.
    """
    cache: dict[str, QueryGateway] = {}

    def factory(schema: str) -> QueryGateway:
        if schema not in cache:
            cache[schema] = debug_wrapped(base_factory(schema), debug=debug)
        return cache[schema]

    return factory
