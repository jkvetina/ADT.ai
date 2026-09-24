"""The `-trailing` pass: rewrite stored source so it matches what export_db writes.

Moved out of `recompile/runner.py` as it stood (ADT #923) to keep that file
inside its context-size budget. The runner still decides when the pass runs;
this is the pass itself, one object at a time.
"""

from __future__ import annotations

from adt_ai.recompile.contracts import RecompileReporter, RecompileRequest, TrailingAction
from adt_ai.recompile.inventory import RecompileDiscovery, TrailingObject
from adt_ai.recompile.queries import (
    build_disable_trigger_statement,
    build_trailing_source_ddl,
    build_trailing_view_ddl,
    count_trailing_view_lines,
)
from adt_ai.shared.db import QueryGateway


def trailing_view_candidates(
    discovery: RecompileDiscovery,
    *,
    object_name: str,
    object_type: str,
    prefix: str,
    ignore: str,
) -> list[TrailingObject]:
    """Which in-scope views actually carry trailing whitespace (#122).

    The user_source detection query cannot see views, they have no rows there,
    and its SQL trailing test cannot run against user_views.text either, because
    that column is a LONG. So the sweep fetches each in-scope view's text and
    the test happens here, in Python, against the same rstrip() rule export_db
    applies. Only views with something to strip become candidates; a clean
    schema adds nothing to the list.
    """
    candidates: list[TrailingObject] = []
    views = discovery.trailing_views(
        object_name = object_name,
        object_type = object_type,
        prefix      = prefix,
        ignore      = ignore,
    )
    for view in views:
        lines = count_trailing_view_lines(view.view_text)
        if lines:
            candidates.append(TrailingObject("VIEW", view.object_name, lines))
    return candidates


def build_trailing_ddl(discovery: RecompileDiscovery, candidate: TrailingObject) -> str | None:
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


def apply_trailing_fixes(
    reporter: RecompileReporter,
    gateway: QueryGateway,
    discovery: RecompileDiscovery,
    candidates: list[TrailingObject],
    request: RecompileRequest,
) -> list[TrailingAction]:
    """Rewrite each flagged object's source, one object at a time.

    Strictly per object, fetch this object's source, rewrite this object, move
    on. Never fetch every object up front and write them all afterwards: the
    database is live, and a batch pass would happily clobber somebody else's
    change made in the window between the read and the write.

    Each object is announced through the reporter *before* its rewrite runs, so
    the visible pause attaches to the object being worked on rather than to the
    connection block above the list.
    """
    actions: list[TrailingAction] = []
    reporter.begin_trailing(candidates)
    for candidate in candidates:
        try:
            ddl = build_trailing_ddl(discovery, candidate)
        except Exception as exc:
            # Rebuilding the DDL can refuse outright, a view whose column list
            # is not plainly quotable, say. That is one object's problem, so it
            # is reported and the sweep carries on, exactly as a failed rewrite is.
            if request.debug:
                raise
            reporter.trailing_object(candidate)
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
        reporter.trailing_object(candidate)
        actions.append(exec_trailing_fix(gateway, discovery, candidate, ddl, request))
    reporter.end_trailing(actions)
    return actions


def exec_trailing_fix(
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
