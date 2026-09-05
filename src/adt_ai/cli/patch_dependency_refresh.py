"""Bring the dependency mirror level with the objects `patch -create` will order.

`patch/staleness.py` has refused a stale graph since `#261`, and refusing is the
right answer to a question nobody can answer offline: the ordering it would
produce fails in the target database rather than in ADT. But the fix it printed,
`adtai dependencies -refresh -schema X`, is a command the operator then typed
verbatim before re-running the one they wanted, so the refusal was a round trip
through a human to run a command ADT could run itself.

`#367` runs it. Jan, 2026-08-15, choosing between auto-refresh and a narrowed
refusal: *"Auto-refresh affected schemas"*. Three properties keep that safe:

* **Only the stale scopes are refreshed.** `graph_freshness` already reports
  staleness per schema, so the refresh names exactly the schemas whose objects
  outran the graph, never the whole project. That is also the narrowing the card
  asked for: a patch no longer stops because a schema it does not touch is
  behind, because that schema is simply brought current.
* **The refusal survives.** A run with no reachable connection, or one whose
  refresh leaves a scope stale anyway, lands on the same `PatchError` and the
  same message it always did. Auto-refresh replaces the round trip, not the gate.
* **It says what it is doing before it does it.** `#367` leaned on the
  connection block for that, reading `CONNECTING TO SCHEMA <schema>, <env>:` as
  a header that lands before the dictionary reads. It is a header, and it is
  also a section that CLOSES: the blank line under its version rows is exactly
  where the console contract retires a header's claim, so the counts arrived
  under no title at all. Jan, 2026-08-19: *"This whole block is actually missing
  a header: UPDATING DEPENDENCIES"* (ADT `#413`). `#372` had deleted an invented
  `REFRESHING <SCHEMA> DEPENDENCIES:` from this same spot on *"I did not asked
  you to ADD NEW HEADERS, I asked you to print PRECEEDING header!"*, and the
  difference between the two is the whole rule: that one was an agent's reading
  of a guard, this one is Jan's own words, so the string is his rather than a
  paraphrase of his. It is also what covers the PL/Scope catalog scan now that
  the recompile row stands down on a run that recompiles nothing.

`#569` extends it to an ABSENT graph. `#367` covered the stale case and left the
missing one refusing, on the reading that a graph which is not there names no
schema to narrow a refresh to. That reading was wrong about where the names come
from: `graph_freshness` derives its scopes from the object FILES, never from the
mirror, so a missing graph names its schemas exactly as well as a stale one and
the only thing the short-circuit bought was the round trip this module exists to
remove. Jan, 2026-08-26, on a `-create` that refused a project whose mirror had
never been built: *"if you have issues with dependencies during patch, you should
have fetch them without forcing user to do it!"* All three properties above hold
unchanged, the second one in particular: the re-measure is still what decides,
so a root that cannot reach a database lands on the same refusal, worded the
same way.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    DependencyIndexRequest,
    DependencyIndexRunner,
    GatewayFactory,
    QueryGateway,
)
from adt_ai.cli.context import (
    _load_startup_context,
    _print_connection_block,
)
from adt_ai.cli.gateways import build_gateway, cached_schema_gateway_factory
from adt_ai.patch.staleness import GraphFreshness, graph_freshness
from adt_ai.shared.progress import FixedWidthProgressPrinter, print_adt_header

#: The section the count rows sit under. Jan's own wording, 2026-08-19, plus the
#: colon every ADT section header carries.
REFRESH_HEADER = "UPDATING DEPENDENCIES:"

#: Refresh one schema's mirror. Returns nothing; failures raise, and the caller
#: turns them back into the gate's own refusal.
DependencyRefresher = Callable[[list[str]], None]


def ensure_fresh_dependency_graph(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
    gateway_factory: GatewayFactory | None = None,
) -> None:
    """Refresh whatever `-create` would order from a graph that predates it.

    A no-op on a current graph, which is the normal case and costs the same
    handful of `stat` calls the gate has always cost: nothing connects unless
    something is actually stale.
    """
    report = graph_freshness(root, config)
    if report.is_fresh:
        return
    schemas = [scope.schema for scope in report.stale if scope.schema]
    if len(schemas) != len(report.stale):
        # A scope with no owner to refresh by: a `path_objects` layout with no
        # `<schema>` placeholder collects a tree spanning every owner, so it
        # names no target and there is nothing to narrow a refresh to.
        #
        # `report.graph_missing` used to short-circuit this test, which is how
        # an ABSENT graph stayed refused while a STALE one was fixed (`#569`).
        # It looked like a second unnameable case and was not: the scopes come
        # from the object files, not from the mirror.
        _refuse(report)
    _refresh_schemas(args, root, config, gateway_factory, schemas)
    # Measured again rather than assumed: the refresh may have connected, run
    # and still left a scope behind (a schema the connection file does not
    # carry, a partial failure), and a gate that trusts its own remedy is not a
    # gate. `#210` is the standing example of asserting the branch instead of
    # the premise that selects it.
    _refuse(graph_freshness(root, config))


def _refuse(report: GraphFreshness) -> None:
    """Raise the gate's own message unless the report says the graph is usable.

    One seam, so the message a refusal carries is the message
    `require_fresh_dependency_graph` has always carried, and the re-measure
    above cannot drift into a second wording of the same refusal.
    """
    from adt_ai.patch.runner import PatchError

    if report.is_fresh:
        return
    raise PatchError(report.failure_message())


def _refresh_schemas(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
    gateway_factory: GatewayFactory | None,
    schemas: list[str],
) -> None:
    """Run the `dependencies -refresh` pass for the named schemas.

    Every failure is swallowed to a return: the caller re-measures immediately
    afterwards, so a refresh that could not connect surfaces as the staleness
    refusal that names the command to run, which is a better error than an
    `ORA-` traceback out of a build that was never asked to open a database.
    """
    debug = getattr(args, "debug", False)
    silent = getattr(args, "silent", False)
    # A section closes itself, and this one never did (ADT `#456`). Every other
    # block on the `-create` screen leaves a blank line behind it, the banner
    # under its `COMMITS |` row and `print_adt_table` under its last row, so the
    # header that follows opens on two empty lines: its own, plus the one the
    # section above it wrote. The count rows end on a bare newline, so
    # `RELEVANT COMMITS:` landed a line higher whenever a refresh had run. Jan,
    # 2026-08-21: *"Every header must have 1 empty line above, right. So why
    # dont you print extra line after you run the dependencies? That would match
    # both cases."* It is the closing blank of THIS section rather than a lead
    # gap on the next one, which is why it is written here and not as a
    # `print_adt_header` knob at whatever prints next: nothing downstream knows
    # a refresh happened, and `#444` already tried making the gap a property of
    # the following header (`lead_gap=True`) and had to be undone by `#451`.
    try:
        startup = _load_startup_context(args)
        connections = startup.connections
    except Exception:
        return
    environment = getattr(args, "env", None) or connections.default_environment

    def connect(schema: str) -> QueryGateway:
        if gateway_factory is not None:
            return gateway_factory(schema)
        return build_gateway(
            startup,
            connections.resolve(environment=environment, schema=schema),
        )

    # The cache and the `-debug` wrap both come from `cli/gateways.py` (ADT
    # #670). They travel together because they are one decision, and the
    # hand-rolled pair this replaces had the wrap the wrong way round:
    # `build_gateway` returns an `AnnouncedGateway` under `strict_mode`, so
    # putting `DebugQueryGateway` outside it let the logger's own
    # `mark_announced()` run ahead of the console guard, and a `-debug` refresh
    # could not report a violation whatever it was showing.
    resolve = cached_schema_gateway_factory(connect, debug=debug)

    runner = DependencyIndexRunner(resolve)
    for schema in schemas:
        # The connection block above closes its own section, so the counts need
        # a header of their own and Jan named it (module docstring). One per
        # schema, inside the loop, because each schema opens its own connection
        # block and a header printed once would sit above the wrong one.
        try:
            if gateway_factory is None:
                _print_connection_block(
                    resolve(schema),
                    connections.resolve(environment=environment, schema=schema),
                    debug = debug,
                )
            # Printed under `-silent` too: the console contract keeps banners,
            # connection blocks, section headers and the timer whatever else a
            # silent run suppresses.
            print_adt_header(REFRESH_HEADER)
            runner.refresh(
                DependencyIndexRequest(
                    root     = root,
                    schemas  = [schema],
                    config   = config,
                    progress = None if silent else FixedWidthProgressPrinter(),
                )
            )
        except Exception:
            # One schema's failure does not stop the others: per-schema
            # isolation is the same rule every multi-schema module follows, and
            # the re-measure above reports whichever scopes are still behind.
            continue
        finally:
            # In `finally` so an abandoned section closes too: the next schema
            # opens on its own connection block either way, and a section that
            # ends on a raise is still a section that ended.
            print()


__all__ = [
    "DependencyRefresher",
    "ensure_fresh_dependency_graph",
]
