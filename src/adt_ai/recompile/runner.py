"""Orchestration for the recompile module.

Mirrors old ADT ``recompile.py``: read overview, recompile invalid (or all
with force), retry failures in reverse after reconnecting, then re-check which
objects are still invalid and summarize their errors.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from adt_ai.db import QueryGateway
from adt_ai.recompile.inventory import (
    ObjectError,
    ObjectOverview,
    RecompileDiscovery,
    RecompileObject,
)
from adt_ai.recompile.queries import build_compile_statement

# A no-arg factory that returns a fresh gateway, mirroring old ADT's reconnect
# between the compile loop, the retry pass, and the final re-check.
GatewayFactory = Callable[[], QueryGateway]


@dataclass(frozen=True)
class RecompileRequest:
    object_name: str = "%"
    object_type: str = "%"
    prefix: str = ""
    ignore: str = ""
    force: bool = False
    native: bool = False
    optimize_level: int | None = None
    scope: list[str] | None = None
    warnings: list[str] | None = None
    debug: bool = False


@dataclass(frozen=True)
class RecompileResult:
    compiled: list[RecompileObject] = field(default_factory=list)
    troublemakers: list[RecompileObject] = field(default_factory=list)
    invalid: list[ObjectError] = field(default_factory=list)
    overview: list[ObjectOverview] = field(default_factory=list)
    success: bool = True


class RecompileRunner:
    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self.gateway_factory = gateway_factory

    def run(self, request: RecompileRequest) -> RecompileResult:
        scope = {
            "object_name" : request.object_name,
            "object_type" : request.object_type,
            "prefix"      : request.prefix,
            "ignore"      : request.ignore,
        }

        gateway = self.gateway_factory()
        discovery = RecompileDiscovery(gateway)
        overview = discovery.overview(**scope)
        todo = discovery.objects_to_recompile(**scope, force=request.force)
        if not todo:
            return RecompileResult(
                compiled = [],
                invalid  = [],
                overview = overview,
                success  = True,
            )

        compiled: list[RecompileObject] = []
        troublemakers: list[RecompileObject] = []
        for obj in todo:
            statement = self._statement_for(obj, request)
            try:
                gateway.execute(statement)
                compiled.append(obj)
            except Exception:
                if request.debug:
                    raise
                troublemakers.append(obj)

        # retry the leftovers in reverse on a fresh connection (errors swallowed)
        if troublemakers:
            gateway = self.gateway_factory()
            for obj in reversed(troublemakers):
                try:
                    gateway.execute(self._statement_for(obj, request))
                except Exception:
                    if request.debug:
                        raise
                    pass

        # reconnect for the final re-check, mirroring old ADT
        gateway = self.gateway_factory()
        discovery = RecompileDiscovery(gateway)
        overview = discovery.overview(**scope)
        errors = discovery.errors_summary(**scope)
        remaining = discovery.objects_to_recompile(**scope, force=False)
        invalid = _enrich_invalid(remaining, errors)

        return RecompileResult(
            compiled      = compiled,
            troublemakers = troublemakers,
            invalid       = invalid,
            overview      = overview,
            success       = not invalid,
        )

    @staticmethod
    def _statement_for(obj: RecompileObject, request: RecompileRequest) -> str:
        return build_compile_statement(
            obj.object_type,
            obj.object_name,
            native         = request.native,
            optimize_level = request.optimize_level,
            scope          = request.scope,
            warnings       = request.warnings,
        )


def _enrich_invalid(
    remaining: list[RecompileObject],
    errors: list[ObjectError],
) -> list[ObjectError]:
    index = {(error.object_type, error.object_name): error for error in errors}
    enriched: list[ObjectError] = []
    for obj in remaining:
        match = index.get((obj.object_type, obj.object_name))
        if match is not None:
            enriched.append(match)
        else:
            enriched.append(ObjectError(obj.object_type, obj.object_name, 0, None))
    return enriched
