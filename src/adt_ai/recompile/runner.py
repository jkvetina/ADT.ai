"""Orchestration for the recompile module.

Mirrors old ADT ``recompile.py``: read overview, recompile invalid (or all
with force), retry failures in reverse after reconnecting, then re-check which
objects are still invalid and summarize their errors.
"""

from __future__ import annotations

from adt_ai.recompile.contracts import (
    GatewayFactory as GatewayFactory,
)
from adt_ai.recompile.contracts import (
    MViewAction as MViewAction,
)
from adt_ai.recompile.contracts import (
    RecompileReporter as RecompileReporter,
)
from adt_ai.recompile.contracts import (
    RecompileRequest as RecompileRequest,
)
from adt_ai.recompile.contracts import (
    RecompileResult as RecompileResult,
)
from adt_ai.recompile.contracts import (
    TrailingAction as TrailingAction,
)
from adt_ai.recompile.inventory import (
    LockedObject,
    MaterializedView,
    ObjectError,
    RecompileDiscovery,
    RecompileObject,
    TrailingObject,
)
from adt_ai.recompile.queries import (
    build_compile_statement,
    build_disable_trigger_statement,
    build_refresh_statement,
    build_trailing_source_ddl,
    build_trailing_view_ddl,
    count_trailing_view_lines,
)
from adt_ai.shared.db import QueryGateway


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
        # The single-object-class reports (-mviews, -synonyms, -jobs) have nothing for
        # -type to select, so their SQL declares no :object_type bind. Pass only the
        # binds each statement actually declares: python-oracledb hands the dict
        # straight to cursor.execute, and an unused named bind fails against a real
        # database while passing silently through FakeGateway.
        name_scope = {
            "object_name" : request.object_name,
            "prefix"      : request.prefix,
            "ignore"      : request.ignore,
        }

        gateway = self.gateway_factory()
        discovery = RecompileDiscovery(gateway)

        # -synonyms is a report-only run: no compile/refresh action, no lock pass,
        # no post-action re-read. Read the synonym health table and return it; the
        # OBJECTS OVERVIEW / invalid recompile / compile errors are all skipped.
        if request.synonyms:
            synonyms = discovery.synonyms(**name_scope)
            return RecompileResult(synonyms=synonyms, success=True)

        # -disabled is a report-only run: no compile/refresh action, no lock pass,
        # no post-action re-read. Read disabled constraints/indexes/triggers once
        # and return it; the OBJECTS OVERVIEW / invalid recompile / compile errors
        # are all skipped.
        # -disabled is the one report-only flag spanning several object types, so it
        # takes the full scope: -type picks CONSTRAINT / INDEX / TRIGGER, -name filters
        # within them.
        if request.disabled:
            disabled_objects = discovery.disabled_objects(**scope)
            return RecompileResult(disabled_objects=disabled_objects, success=True)

        # -jobs is a report-only run: no compile/refresh action, no lock pass,
        # no post-action re-read. Read today's scheduler job health once and
        # return it; the OBJECTS OVERVIEW / invalid recompile / compile errors
        # are all skipped.
        if request.jobs:
            jobs = discovery.scheduler_jobs(**name_scope)
            return RecompileResult(jobs=jobs, success=True)

        # -trailing is a source-hygiene run: skip the invalid-object recompile, the
        # OBJECTS OVERVIEW, and the lock pass. It rewrites each flagged object in
        # place so the stored source matches what export_db writes, which is what
        # removes the diff noise. There is no preview mode: asking for -trailing is
        # asking for the strip. The safety is structural, not a second flag — an
        # object with nothing to strip is never touched (build_trailing_source_ddl
        # returns None), and stripping trailing whitespace cannot change behaviour.
        if request.trailing:
            candidates = discovery.trailing_objects(**scope) + self._trailing_view_candidates(
                discovery, scope
            )
            actions = self._apply_trailing_fixes(gateway, discovery, candidates, request)
            return RecompileResult(
                trailing         = candidates,
                trailing_actions = actions,
                success          = all(action.ok for action in actions),
            )

        # -mviews is a materialized-view-focused run: skip the invalid-object
        # recompile and the OBJECTS OVERVIEW entirely, only collect locks (which
        # can block an MV refresh) and act on the materialized views themselves.
        if request.mview:
            locked = _collect_locked(discovery, scope)
            self.reporter.locked(locked)
            gateway = self.gateway_factory()  # fresh connection for the MV action pass
            discovery = RecompileDiscovery(gateway)
            mviews = discovery.materialized_views(**name_scope)
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
        if not todo:
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

        # Surface the full per-line compile messages so an AI agent can pinpoint
        # the offending line/position/text on whatever is still invalid.
        error_details = discovery.errors_detail(**scope) if invalid else []

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

    @staticmethod
    def _trailing_view_candidates(
        discovery: RecompileDiscovery,
        scope: dict[str, str],
    ) -> list[TrailingObject]:
        """Which in-scope views actually carry trailing whitespace (#122).

        The user_source detection query cannot see views — they have no rows there —
        and its SQL trailing test cannot run against user_views.text either, because
        that column is a LONG. So the sweep fetches each in-scope view's text and
        the test happens here, in Python, against the same rstrip() rule export_db
        applies. Only views with something to strip become candidates; a clean
        schema adds nothing to the list.
        """
        candidates: list[TrailingObject] = []
        for view in discovery.trailing_views(**scope):
            lines = count_trailing_view_lines(view.view_text)
            if lines:
                candidates.append(TrailingObject("VIEW", view.object_name, lines))
        return candidates

    @staticmethod
    def _build_trailing_ddl(discovery: RecompileDiscovery, candidate: TrailingObject) -> str | None:
        """The rebuilt DDL for one object, or None when it has nothing to strip.

        Both paths re-read the object's source at rewrite time rather than trusting
        the detection pass, and both treat their transform as authoritative: a None
        here means the object is left completely untouched.
        """
        if candidate.object_type == "VIEW":
            return build_trailing_view_ddl(
                candidate.object_name,
                discovery.view_columns(candidate.object_name),
                discovery.view_text(candidate.object_name),
            )
        lines = discovery.object_source(candidate.object_type, candidate.object_name)
        return build_trailing_source_ddl(lines)

    def _apply_trailing_fixes(
        self,
        gateway: QueryGateway,
        discovery: RecompileDiscovery,
        candidates: list[TrailingObject],
        request: RecompileRequest,
    ) -> list[TrailingAction]:
        """Rewrite each flagged object's source, one object at a time.

        Strictly per object — fetch this object's source, rewrite this object, move
        on. Never fetch every object up front and write them all afterwards: the
        database is live, and a batch pass would happily clobber somebody else's
        change made in the window between the read and the write.

        Each object is announced through the reporter *before* its rewrite runs, so
        the visible pause attaches to the object being worked on rather than to the
        connection block above the list.
        """
        actions: list[TrailingAction] = []
        self.reporter.begin_trailing(candidates)
        for candidate in candidates:
            try:
                ddl = self._build_trailing_ddl(discovery, candidate)
            except Exception as exc:
                # Rebuilding the DDL can refuse outright — a view whose column list
                # is not plainly quotable, say. That is one object's problem, so it
                # is reported and the sweep carries on, exactly as a failed rewrite is.
                if request.debug:
                    raise
                self.reporter.trailing_object(candidate)
                actions.append(
                    TrailingAction(
                        candidate.object_type,
                        candidate.object_name,
                        candidate.trailing_lines,
                        False,
                        str(exc),
                    )
                )
                continue
            if ddl is None:
                # The detection pass offered it up but the source has nothing to
                # strip. The transform is authoritative: leave the object completely
                # alone, and do not list it as modified.
                continue
            self.reporter.trailing_object(candidate)
            actions.append(self._exec_trailing_fix(gateway, discovery, candidate, ddl, request))
        self.reporter.end_trailing(actions)
        return actions

    @staticmethod
    def _exec_trailing_fix(
        gateway: QueryGateway,
        discovery: RecompileDiscovery,
        candidate: TrailingObject,
        ddl: str,
        request: RecompileRequest,
    ) -> TrailingAction:
        # Read the trigger's state *before* the replace: CREATE OR REPLACE TRIGGER
        # always leaves the trigger ENABLED, so a disabled one has to be switched back
        # off or the sweep silently arms triggers somebody disabled on purpose.
        status = (
            discovery.trigger_status(candidate.object_name)
            if candidate.object_type == "TRIGGER"
            else None
        )
        try:
            gateway.execute(ddl)
            if (status or "").upper() == "DISABLED":
                gateway.execute(build_disable_trigger_statement(candidate.object_name))
            return TrailingAction(
                candidate.object_type,
                candidate.object_name,
                candidate.trailing_lines,
                True,
                None,
            )
        except Exception as exc:
            if request.debug:
                raise
            return TrailingAction(
                candidate.object_type,
                candidate.object_name,
                candidate.trailing_lines,
                False,
                str(exc),
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
