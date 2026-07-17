"""Same-connection PL/Scope prerequisite for the dependencies refresh.

Before a schema's ``USER_IDENTIFIERS`` / ``USER_STATEMENTS`` mirrors can be
pulled, every PL/SQL object must have been compiled with full PL/Scope
(``IDENTIFIERS:ALL`` + ``STATEMENTS:ALL``). This runs that prerequisite *on the
already-open gateway* — it sets the session setting, asks the recompile module
which VALID objects are still missing scope, optionally narrows that list to
objects whose dictionary rows changed, and recompiles each with ``REUSE
SETTINGS`` so only PL/Scope changes. No new connection is opened and
``RecompileRunner`` (which reconnects through a no-arg factory) is never used,
honouring the "do all of this on the same connection" constraint.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from adt_ai.dependencies.queries import PLSCOPE_SESSION_STATEMENT
from adt_ai.recompile.inventory import RecompileDiscovery, RecompileObject
from adt_ai.recompile.queries import build_compile_statement
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.oracle_session import is_ddl_lock_timeout


def ensure_plscope(
    gateway: QueryGateway,
    *,
    candidates: Iterable[tuple[str, str]] | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[RecompileObject]:
    """Enable full PL/Scope and recompile selected VALID objects that still lack it.

    Returns the objects recompiled (empty when the schema already has full
    PL/Scope everywhere or none of the candidates lack it). ``progress``
    receives exceptional skip lines only, not one line per successful recompile.
    """
    _progress = progress or (lambda _: None)

    # 1. Turn full PL/Scope on for this session so the recompiles below populate
    #    the identifier / statement dictionaries.
    gateway.execute(PLSCOPE_SESSION_STATEMENT)

    # 2. Discover VALID PL/SQL objects whose stored PL/Scope settings are not
    #    already IDENTIFIERS:ALL + STATEMENTS:ALL (reuses the recompile catalog
    #    read — no RecompileRunner, no second connection).
    pending = RecompileDiscovery(gateway).objects_missing_plscope()
    if candidates is not None:
        candidate_keys = set(candidates)
        pending = [
            database_object
            for database_object in pending
            if (database_object.object_type, database_object.object_name) in candidate_keys
        ]

    # 3. Recompile each with scope=["ALL"] + REUSE SETTINGS on the same gateway.
    recompiled: list[RecompileObject] = []
    for database_object in pending:
        statement = build_compile_statement(
            database_object.object_type,
            database_object.object_name,
            scope=["ALL"],
        )
        try:
            gateway.execute(statement)
        except Exception as exc:
            if not is_ddl_lock_timeout(exc):
                raise
            _progress(f"SKIPPED LOCKED {database_object.object_type}.{database_object.object_name}")
            continue
        recompiled.append(database_object)

    return recompiled
