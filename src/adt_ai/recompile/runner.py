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
    CompileError,
    LockedObject,
    MaterializedView,
    ObjectError,
    ObjectOverview,
    RecompileDiscovery,
    RecompileObject,
    SynonymInfo,
)
from adt_ai.recompile.queries import build_compile_statement, build_refresh_statement

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
    mview: bool = False
    mview_name: str = "%"
    synonyms: bool = False
    synonym_name: str = "%"
    errors: bool = False
    debug: bool = False


@dataclass(frozen=True)
class MViewAction:
    object_name: str
    action: str          # "COMPILE" or "REFRESH"
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class RecompileResult:
    compiled: list[RecompileObject] = field(default_factory=list)
    troublemakers: list[RecompileObject] = field(default_factory=list)
    invalid: list[ObjectError] = field(default_factory=list)
    overview: list[ObjectOverview] = field(default_factory=list)
    locked: list[LockedObject] = field(default_factory=list)
    mviews: list[MaterializedView] = field(default_factory=list)
    mview_actions: list[MViewAction] = field(default_factory=list)
    synonyms: list[SynonymInfo] = field(default_factory=list)
    error_details: list[CompileError] = field(default_factory=list)
    success: bool = True


class RecompileReporter:
    """No-op streaming hooks for the materialized-view pass.

    The runner calls these around each materialized view so a console reporter
    can print the object name, let the refresh hang *attached to that view*,
    then finish the row. The base does nothing, so a plain ``run()`` (the unit
    tests, any non-console caller) behaves exactly as before.
    """

    def locked(self, locked: list[LockedObject]) -> None: ...

    def begin_mviews(self, mviews: list[MaterializedView]) -> None: ...

    def begin_mview(self, mview: MaterializedView) -> None: ...

    def end_mview(self, mview: MaterializedView) -> None: ...

    def end_mviews(self, mview_actions: list[MViewAction]) -> None: ...


class RecompileRunner:
    def __init__(
        self,
        gateway_factory: GatewayFactory,
        reporter: RecompileReporter | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        # streaming hooks; the no-op base keeps non-console callers unchanged. The
        # CLI swaps in a console reporter post-construction, so the fake runners in
        # the CLI tests (single-arg __init__) stay untouched.
        self.reporter = reporter or RecompileReporter()

    def run(self, request: RecompileRequest) -> RecompileResult:
        scope = {
            "object_name" : request.object_name,
            "object_type" : request.object_type,
            "prefix"      : request.prefix,
            "ignore"      : request.ignore,
        }

        gateway = self.gateway_factory()
        discovery = RecompileDiscovery(gateway)

        # -synonyms is a report-only run: no compile/refresh action, no lock pass,
        # no post-action re-read. Read the synonym health table and return it; the
        # OBJECTS OVERVIEW / invalid recompile / compile errors are all skipped.
        if request.synonyms:
            synonyms = discovery.synonyms(
                object_name = request.synonym_name,
                prefix      = request.prefix,
                ignore      = request.ignore,
            )
            return RecompileResult(synonyms=synonyms, success=True)

        # -mviews is a materialized-view-focused run: skip the invalid-object
        # recompile and the OBJECTS OVERVIEW entirely, only collect locks (which
        # can block an MV refresh) and act on the materialized views themselves.
        if request.mview:
            locked = _collect_locked(discovery, scope)
            self.reporter.locked(locked)
            gateway = self.gateway_factory()  # fresh connection for the MV action pass
            discovery = RecompileDiscovery(gateway)
            mview_scope = {
                "object_name" : request.mview_name,
                "prefix"      : request.prefix,
                "ignore"      : request.ignore,
            }
            mviews = discovery.materialized_views(**mview_scope)
            self.reporter.begin_mviews(mviews)
            # Stream one materialized view at a time: announce the view, act on it,
            # then re-read *just that view* so its row shows the post-action
            # STALENESS / LAST REFRESHED AT / dictionary TIMER. Acting per view (not
            # in one batch) puts the visible hang on the MV being refreshed instead
            # of the connection block above the table.
            final_mviews: list[MaterializedView] = []
            mview_actions: list[MViewAction] = []
            for mview in mviews:
                self.reporter.begin_mview(mview)
                actions = _apply_mview_actions(gateway, [mview], request)
                mview_actions.extend(actions)
                current = mview
                if actions:
                    reread = discovery.materialized_views(
                        object_name = mview.object_name,
                        prefix      = request.prefix,
                        ignore      = request.ignore,
                    )
                    current = next(
                        (m for m in reread if m.object_name == mview.object_name),
                        mview,
                    )
                final_mviews.append(current)
                self.reporter.end_mview(current)
            self.reporter.end_mviews(mview_actions)
            unresolved_mviews = [action for action in mview_actions if not action.ok]
            return RecompileResult(
                locked        = locked,
                mviews        = final_mviews,
                mview_actions = mview_actions,
                success       = not unresolved_mviews,
            )

        overview = discovery.overview(**scope)
        locked = _collect_locked(discovery, scope)
        todo = discovery.objects_to_recompile(**scope, force=request.force)
        if not todo and not request.errors:
            return RecompileResult(
                compiled = [],
                invalid  = [],
                overview = overview,
                locked   = locked,
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

        # -errors: surface the full per-line compile messages so an AI agent can
        # pinpoint the offending line/position/text on whatever is still invalid.
        error_details = discovery.errors_detail(**scope) if request.errors else []

        return RecompileResult(
            compiled      = compiled,
            troublemakers = troublemakers,
            invalid       = invalid,
            overview      = overview,
            locked        = locked,
            error_details = error_details,
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


def _collect_locked(discovery: RecompileDiscovery, scope: dict[str, str]) -> list[LockedObject]:
    """Read session/object locks, degrading to an empty list without gv$ grants."""
    try:
        return discovery.locked_objects(**scope)
    except Exception:
        return []


def _mview_needs_compile(mview: MaterializedView) -> bool:
    state = (mview.compile_state or "").upper()
    return state not in ("", "VALID") or (mview.staleness or "").upper() == "NEEDS_COMPILE"


def _mview_needs_refresh(mview: MaterializedView, force: bool = False) -> bool:
    if force:
        return True
    return (mview.staleness or "").upper() in ("STALE", "UNUSABLE")


def _apply_mview_actions(
    gateway: QueryGateway,
    mviews: list[MaterializedView],
    request: RecompileRequest,
) -> list[MViewAction]:
    actions: list[MViewAction] = []
    for mview in mviews:
        if _mview_needs_compile(mview):
            actions.append(
                _exec_mview_action(
                    gateway,
                    mview.object_name,
                    "COMPILE",
                    build_compile_statement("MATERIALIZED VIEW", mview.object_name),
                    request,
                )
            )
        if _mview_needs_refresh(mview, request.force):
            actions.append(
                _exec_mview_action(
                    gateway,
                    mview.object_name,
                    "REFRESH",
                    # refresh with the view's *own* configured method so the tool
                    # never flips a COMPLETE view to FAST.
                    build_refresh_statement(mview.object_name, mview.refresh_method),
                    request,
                )
            )
    return actions


def _exec_mview_action(
    gateway: QueryGateway,
    object_name: str,
    action: str,
    statement: str,
    request: RecompileRequest,
) -> MViewAction:
    try:
        gateway.execute(statement)
        return MViewAction(object_name, action, True, None)
    except Exception as exc:
        if request.debug:
            raise
        return MViewAction(object_name, action, False, str(exc))
