"""Orchestration for the recompile module.

Mirrors old ADT ``recompile.py``: read overview, recompile invalid (or all
with force), retry failures in reverse after reconnecting, then re-check which
objects are still invalid and summarize their errors.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import TypedDict

from adt_ai.recompile.contracts import (
    DependentsProvider as DependentsProvider,
)
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
    MaterializedView,
    RecompileDiscovery,
    RecompileObject,
)
from adt_ai.recompile.queries import (
    build_compile_statement,
    build_refresh_statement,
)
from adt_ai.recompile.results import enrich_invalid, with_validated
from adt_ai.recompile.root_causes import rank_for_run
from adt_ai.recompile.trailing_fix import apply_trailing_fixes, trailing_view_candidates
from adt_ai.recompile.vpd import SourceProvider, read_vpd
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.scan_helpers import drop_stray_scan_helpers


class _ObjectScope(TypedDict):
    """The four binds every object-wide statement declares."""

    object_name: str
    object_type: str
    prefix: str
    ignore: str


class _NameScope(TypedDict):
    """`_ObjectScope` minus `object_type`, for the single-object-class reports.

    Spelled as its own shape rather than a total=False variant of the one
    above: an unused named bind fails against a real database, so which keys
    a statement gets is the contract, not an optional extra.
    """

    object_name: str
    prefix: str
    ignore: str


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
        # Dependency-graph edges for the root-cause ranking, injected the same way
        # for the same reason: the runner must not know about SQLite, and a caller
        # with no mirror gets the error-evidence ranking rather than an error.
        self.dependents_for: DependentsProvider = lambda _nodes: {}
        # The policy functions' source text, from the export_db files (#884).
        # Injected like dependents_for, so this module never touches the repo.
        self.vpd_sources: SourceProvider = lambda _wanted: {}

    def run(self, request: RecompileRequest) -> RecompileResult:
        scope: _ObjectScope = {
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
        name_scope: _NameScope = {
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

        # -vpd is report-only as well, and like -disabled takes the full scope:
        # -type picks whether -name matches the TABLE, the POLICY or the FUNCTION.
        if request.vpd:
            report = read_vpd(
                gateway, **scope, column=request.vpd_column, sources=self.vpd_sources
            )
            return RecompileResult(vpd=report, success=True)

        # -trailing is a source-hygiene run: skip the invalid-object recompile, the
        # OBJECTS OVERVIEW, and the lock pass. It rewrites each flagged object in
        # place so the stored source matches what export_db writes, which is what
        # removes the diff noise. There is no preview mode: asking for -trailing is
        # asking for the strip. The safety is structural, not a second flag, an
        # object with nothing to strip is never touched (build_trailing_source_ddl
        # returns None), and stripping trailing whitespace cannot change behaviour.
        if request.trailing:
            drop_stray_scan_helpers(gateway)
            candidates = discovery.trailing_objects(**scope) + trailing_view_candidates(
                discovery, **scope
            )
            actions = apply_trailing_fixes(self.reporter, gateway, discovery, candidates, request)
            return RecompileResult(
                trailing         = candidates,
                trailing_actions = actions,
                success          = all(action.ok for action in actions),
            )

        # -mviews is a materialized-view-focused run: skip the invalid-object
        # recompile and the OBJECTS OVERVIEW entirely, and act on the
        # materialized views themselves.
        if request.mview:
            gateway = self.gateway_factory()  # fresh connection for the MV action pass
            discovery = RecompileDiscovery(gateway)
            # Title first, listing second: the table fills in behind a header
            # that is already on screen (`#372`).
            self.reporter.reading_mviews()
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
                acted = _apply_mview_actions(gateway, [mview], request)
                mview_actions.extend(acted)
                current = mview
                if acted:
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
                mviews        = final_mviews,
                mview_actions = mview_actions,
                success       = not unresolved_mviews,
            )

        # APEX scan helpers (`DEPSCAN$<n>#<n>`) are generated scratch, never objects
        # to compile or count, so a run that acts on the schema removes the ones
        # it finds first, silently, as `dependencies` and `patch` do after their
        # own scan (ADT #888). The report-only runs above never change the schema.
        drop_stray_scan_helpers(gateway)
        overview = discovery.overview(**scope)
        # Pass the compile modifiers so a modifier-combined -force narrows the sweep to
        # objects whose settings drift from the requested target state (#146). The
        # invalid-object re-check below stays a plain force=False read.
        todo = discovery.objects_to_recompile(
            **scope,
            force          = request.force,
            native         = request.native,
            interpreted    = request.interpreted,
            optimize_level = request.optimize_level,
            scope          = request.scope,
            warnings       = request.warnings,
        )
        if not todo:
            return RecompileResult(
                compiled = [],
                invalid  = [],
                overview = overview,
                success  = True,
            )

        # The invalid set as it stood before the first compile, the baseline the
        # VALIDATED column is measured against (#186). Without -force the todo list
        # already *is* that set, so nothing extra is read; -force sweeps valid
        # objects too, so the baseline is read on its own. It runs after the todo
        # selection deliberately: that keeps the force sweep the run's first
        # OBJECTS_TO_RECOMPILE read, and an empty todo has already returned above.
        before_invalid = (
            discovery.objects_to_recompile(**scope, force=False) if request.force else todo
        )

        compiled: list[RecompileObject] = []
        troublemakers: list[RecompileObject] = []
        for obj in todo:
            # The statement is built inside the guard: a name the identifier
            # check refuses fails this object, not the pass (ADT #923).
            try:
                gateway.execute(self._statement_for(obj, request))
                compiled.append(obj)
            except Exception:
                if request.debug:
                    raise
                troublemakers.append(obj)

        # Retry the leftovers in reverse on a fresh connection (errors swallowed),
        # and keep retrying while a pass still resolves something.
        #
        # **One reversed pass is not enough, and the shortfall is not exotic.**
        # The compile order is alphabetical within object type, so a dependency
        # that runs against the alphabet is repaired by reversing it, which is
        # what the single pass was for. A dependency graph that criss-crosses,
        # A needs C, C needs B, B needs D, is resolvable in no fixed order at
        # all: one pass leaves A and C invalid and the run reports failure on a
        # schema that two more passes would have finished, with nothing on
        # screen to say a retry was even close.
        #
        # A pass that compiles nothing new cannot be helped by another one, so
        # that is where it stops. Every other pass strictly shrinks the list, so
        # the loop is bounded by the number of troublemakers, and the console
        # shape is unchanged: this whole block prints nothing, then and now.
        if troublemakers:
            gateway = self.gateway_factory()
            pending = list(troublemakers)
            while pending:
                remaining = self._retry_pass(gateway, pending, request)
                if len(remaining) == len(pending):
                    break
                pending = remaining

        # reconnect for the final re-check, mirroring old ADT
        gateway = self.gateway_factory()
        discovery = RecompileDiscovery(gateway)

        overview = discovery.overview(**scope)
        errors = discovery.errors_summary(**scope)
        remaining = discovery.objects_to_recompile(**scope, force=False)
        invalid = enrich_invalid(remaining, errors)
        overview = with_validated(overview, before_invalid, remaining)

        # Surface the full per-line compile messages so an AI agent can pinpoint
        # the offending line/position/text on whatever is still invalid.
        error_details = discovery.errors_detail(**scope) if invalid else []

        return RecompileResult(
            compiled      = compiled,
            troublemakers = troublemakers,
            invalid       = invalid,
            overview      = overview,
            error_details = error_details,
            root_causes   = rank_for_run(
                discovery, request.schema, invalid, error_details, self.dependents_for),
            success       = not invalid,
        )

    @classmethod
    def _retry_pass(
        cls,
        gateway: QueryGateway,
        pending: list[RecompileObject],
        request: RecompileRequest,
    ) -> list[RecompileObject]:
        """One reversed sweep over the leftovers; what still failed comes back.

        Reversed because that is the cheap half of the fix: the first pass runs
        alphabetically within type, so a spec compiled after its body is exactly
        what one reversal repairs. Successive passes alternate direction as a
        side effect, which costs nothing and helps a graph that reads either way.
        """
        remaining: list[RecompileObject] = []
        for obj in reversed(pending):
            try:
                gateway.execute(cls._statement_for(obj, request))
            except Exception:
                # Unreachable with request.debug=True: the *first* compile loop
                # in run() already re-raises the moment any object fails, before
                # troublemakers is ever populated, so this pass only ever runs
                # with debug False. Kept for structural symmetry with that
                # loop's except.
                if request.debug:
                    raise  # pragma: no cover, see comment above
                remaining.append(obj)
        return remaining

    @staticmethod
    def _statement_for(obj: RecompileObject, request: RecompileRequest) -> str:
        return build_compile_statement(
            obj.object_type,
            obj.object_name,
            native         = request.native,
            interpreted    = request.interpreted,
            optimize_level = request.optimize_level,
            scope          = request.scope,
            warnings       = request.warnings,
        )

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
                    partial(build_compile_statement, "MATERIALIZED VIEW", mview.object_name),
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
                    partial(build_refresh_statement, mview.object_name, mview.refresh_method),
                    request,
                )
            )
    return actions


def _exec_mview_action(
    gateway: QueryGateway,
    object_name: str,
    action: str,
    statement: Callable[[], str],
    request: RecompileRequest,
) -> MViewAction:
    # Built here, inside the guard: a name the identifier check refuses is this
    # view's failed action, not the end of the `-mviews` run (ADT #923).
    try:
        gateway.execute(statement())
        return MViewAction(object_name, action, True, None)
    except Exception as exc:
        if request.debug:
            raise
        return MViewAction(object_name, action, False, str(exc))
