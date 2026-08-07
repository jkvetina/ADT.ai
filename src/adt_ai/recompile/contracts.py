"""Request/result contracts and the streaming reporter protocol for recompile.

Split out of ``runner.py`` to keep it inside the repo's context-size contract;
``runner.py`` re-exports everything here, exactly as the ``queries`` package's
``__init__`` does for its topic modules. Callers keep importing these from
``adt_ai.recompile.runner`` and cannot tell the difference.

These are the shapes passed *between* the CLI, the runner, and the renderers, so
they live below all three: this module imports from ``inventory``/``db`` and
never from ``runner``, which is what keeps the two free of an import cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from adt_ai.recompile.inventory import (
    CompileError,
    DisabledObject,
    MaterializedView,
    ObjectError,
    ObjectOverview,
    RecompileObject,
    SchedulerJobRun,
    SynonymInfo,
    TrailingObject,
)
from adt_ai.recompile.root_causes import RootCauseReport
from adt_ai.shared.db import QueryGateway

# A no-arg factory that returns a fresh gateway, mirroring old ADT's reconnect
# between the compile loop, the retry pass, and the final re-check.
GatewayFactory = Callable[[], QueryGateway]

# Maps each still-invalid ``TYPE.NAME`` node to the invalid objects that depend on
# it, read from ``config/dependencies.db``. The CLI injects one; the default
# returns nothing, so a runner with no mirror (every unit test, any non-CLI
# caller) ranks on error evidence alone instead of failing.
DependentsProvider = Callable[[list[str]], dict[str, list[str]]]


@dataclass(frozen=True)
class RecompileRequest:
    object_name: str = "%"
    object_type: str = "%"
    prefix: str = ""
    ignore: str = ""
    # The connected schema. Only the root-cause ranking reads it, to tell an
    # own-schema qualifier (`DBADMIN.X`, redundant) from a foreign one
    # (`SYS.X`, a different object that must never resolve to a local invalid).
    schema: str = ""
    force: bool = False
    native: bool = False
    interpreted: bool = False
    optimize_level: int | None = None
    scope: list[str] | None = None
    warnings: list[str] | None = None
    # Every action is a bare flag scoped by the shared object_name/object_type filters
    # (-name and -type). None carries a name pattern of its own: that duplicated -name
    # for no gain and made the command harder to hold in your head.
    mview: bool = False
    synonyms: bool = False
    disabled: bool = False
    jobs: bool = False
    trailing: bool = False
    debug: bool = False


@dataclass(frozen=True)
class MViewAction:
    object_name: str
    action: str          # "COMPILE" or "REFRESH"
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class TrailingAction:
    object_type: str
    object_name: str
    # how many source lines had trailing whitespace stripped.
    trailing_lines: int
    ok: bool
    error: str | None = None


@dataclass(frozen=True)
class RecompileResult:
    compiled: list[RecompileObject] = field(default_factory=list)
    troublemakers: list[RecompileObject] = field(default_factory=list)
    invalid: list[ObjectError] = field(default_factory=list)
    overview: list[ObjectOverview] = field(default_factory=list)
    mviews: list[MaterializedView] = field(default_factory=list)
    mview_actions: list[MViewAction] = field(default_factory=list)
    synonyms: list[SynonymInfo] = field(default_factory=list)
    disabled_objects: list[DisabledObject] = field(default_factory=list)
    jobs: list[SchedulerJobRun] = field(default_factory=list)
    trailing: list[TrailingObject] = field(default_factory=list)
    trailing_actions: list[TrailingAction] = field(default_factory=list)
    error_details: list[CompileError] = field(default_factory=list)
    # Which of the still-invalid objects to check first, and which are knock-ons
    # of another one in the same list (#205). None when the run never got as far
    # as a re-check (a report-only flag, an empty todo, or a caller that predates
    # the field).
    root_causes: RootCauseReport | None = None
    success: bool = True


class RecompileReporter:
    """No-op streaming hooks for the materialized-view and trailing passes.

    The runner calls these around each materialized view so a console reporter
    can print the object name, let the refresh hang *attached to that view*,
    then finish the row. The base does nothing, so a plain ``run()`` (the unit
    tests, any non-console caller) behaves exactly as before.

    The trailing hooks work the same way: ``begin_trailing`` opens the section,
    ``trailing_object`` announces each object as it is rewritten (so the visible
    pause sits on the object being worked on), and ``end_trailing`` closes it.
    """

    def begin_mviews(self, mviews: list[MaterializedView]) -> None: ...

    def begin_mview(self, mview: MaterializedView) -> None: ...

    def end_mview(self, mview: MaterializedView) -> None: ...

    def end_mviews(self, mview_actions: list[MViewAction]) -> None: ...

    def begin_trailing(self, candidates: list[TrailingObject]) -> None: ...

    def trailing_object(self, candidate: TrailingObject) -> None: ...

    def end_trailing(self, trailing_actions: list[TrailingAction]) -> None: ...
