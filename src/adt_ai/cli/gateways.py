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
one — and ``dependencies`` did (ADT #177): it never passed ``startup_sql``, ran
no session setup, and died on a schema whose DDL trigger requires a client
identifier. Nothing else was affected, which is precisely the problem: the fix
had to be applied per site, so the *next* addition to session setup would have
to be threaded through eleven places again.

Hence this module. ``build_gateway`` reads what a connection needs from the
context itself rather than from its caller, so a caller cannot decline session
setup — the choice does not exist at the call site. Adding to what every ADT.ai
connection does is now an edit to one function, and every command inherits it.

``tests/contracts/test_gateway_startup_wiring.py`` pins that: exactly one
``OracleGateway(...)`` construction in ``src/adt_ai/``, and it is this one.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from adt_ai.cli.context_debug import DebugQueryGateway
from adt_ai.shared.db import OracleGateway, QueryGateway

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from adt_ai.cli.context import StartupContext


def build_gateway(
    startup: StartupContext,
    connection: object,
    *,
    debug: bool = False,
    project_root: Path | None = None,
) -> QueryGateway:
    """Return the gateway for ``connection``, wired from ``startup``.

    ``project_root`` is passed only by the commands that shell out to SQLcl
    (it decides where the generated script runs from); everything else about
    the connection comes from the context and is not a caller's choice.
    """
    gateway = OracleGateway(
        connection,
        project_root = project_root,
        startup_sql  = startup.startup_sql,
        config       = startup.config,
    )
    return DebugQueryGateway(gateway) if debug else gateway
