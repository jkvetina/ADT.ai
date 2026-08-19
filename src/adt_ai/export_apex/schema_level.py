"""The export formats that belong to a schema rather than to an application.

`-rest` and `-files_ws` write under `apex/workspace/`, a path carrying no app id,
so neither belongs to an application and neither may run once per application.
`#190` established that for `-rest`; `#233` extended it to `-files_ws` and routed
both through the first application's block so a single row did not cost a section.

ADT #385 is the third pass and the one that took the concern out of the loop: a
run asking for *nothing but* these formats has no per-application work at all, so
it walks no application and prints one bare `EXPORTING:` header. The pieces that
answer that question were spread across `runner.py` (orchestration, already at the
20 KB per-file context budget), `actions.py` (the catalogue) and `recent.py` (the
headers), which is the signal to give them a module rather than to shave comments
off three files.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.progress import (
    ApexProgressReporter,
    CompactApexProgressReporter,
)
from adt_ai.export_apex.recent import _print_schema_export_header
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.progress import print_adt_header

# The tuple order is the order they run in, so `actions.planned_actions` can read
# it straight through.
SCHEMA_LEVEL_ACTIONS = ("files_ws", "rest")


def schema_level_only(actions: Mapping[str, Any]) -> bool:
    """Does this run ask for nothing but the schema-level formats?

    A run selecting only those has no per-application work in it: it walks no
    application, it lists none, and its rows sit under one bare `EXPORTING:`
    header instead of a block per application. `#233` routed such a run through
    the application loop anyway, to give its single row a block to sit in, which
    on a real schema printed the applications table plus one header per
    application and left all but the first empty (17 of them on `DA`).

    Jan, 2026-08-17: *"When user asks just for the -rest or workspace files, you
    will not list apps and you will not go through the app exports ... If you need
    app (for security context, you will silently pick first one you know)"*.

    Reads the actions mapping rather than the request, because the CLI settles
    the applications table from the same answer before a request exists.
    """
    selected = [action for action, enabled in actions.items() if enabled]
    return bool(selected) and all(action in SCHEMA_LEVEL_ACTIONS for action in selected)


def _print_schema_level_only_header() -> None:
    """The one header a run exporting only workspace artifacts prints.

    No application is named because none is exported, and no schema because the
    connection block three lines above already named it. Jan, 2026-08-17: *"you
    will just print "EXPORTING:" header and progress bar for rest and/or
    workspace files"*.

    The literal sits at the print site rather than behind a module constant: the
    console inventory reads call arguments and cannot see through a helper, so a
    header assembled elsewhere is one the review step that approved it misses
    (`tests/helpers/console_surface.py`).
    """
    print_adt_header("EXPORTING:")


class ApexSchemaLevelMixin:
    """The two segment shapes that export workspace artifacts and nothing else.

    Both print a header and then run the same slices, and they differ only in
    which header and whether an application supplies the security context, so
    they are siblings here rather than one method with a flag deciding what it
    prints.

    `_run_schema_action`, `_write_static_files` and `_write_rest_export` come
    from the runner's other mixins, the same way every mixin in this package
    reaches its peers.
    """

    def _run_schema_level_segment(
        self,
        request: Any,
        applications: list[ApexApplication],
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        segment_bar: CompactApexProgressReporter | None,
        segment_reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        store: ApexStore,
    ) -> None:
        """One `EXPORTING:` header and its rows, for a run with no app work.

        The header goes up before the security-context statement, so the screen
        names the work instead of parking on the connection block's closing
        blank, and every row streams its own label after it.

        The first application is used and never named: `GET_APPLICATION` is what
        puts the workspace security context in place, and which application
        supplies it makes no difference to an `apex/workspace/` path. A schema
        hosting none needs no context at all, which is the case `#190` fixed and
        this preserves.
        """
        if segment_bar is None:
            _print_schema_level_only_header()
        application = applications[0] if applications else None
        if application is not None:
            gateway.execute(
                self.EXPORT_START_QUERY,  # type: ignore[attr-defined]
                {"app_id": application.app_id},
            )
        self._run_schema_level_operations(
            request, application, gateway, resolver, segment_reporter, timers, store
        )
        if segment_bar is not None:
            segment_bar.close()

    def _run_schema_artifacts_tail(
        self,
        request: Any,
        schema: str,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        segment_bar: CompactApexProgressReporter | None,
        segment_reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        store: ApexStore,
    ) -> None:
        """The workspace slices a schema hosting no APEX application still owns.

        Reached on a MIXED run only, one that asked for per-application formats
        too: its application loop ran for nothing, so the slices riding the first
        application were never reached. This is the one case left that prints a
        `SCHEMA <name>, EXPORTING:` header, there being no application block for
        the rows to sit under. Skipping the schema entirely here is what made
        `-rest` finish printing nothing at all (ADT #190); `-files_ws` had the
        same hole and never got that fix until `#233`.
        """
        operations = self._schema_level_operations(request, None, gateway, resolver)
        if operations and segment_bar is None:
            _print_schema_export_header(schema)
        for action, operation in operations:
            self._run_schema_action(  # type: ignore[attr-defined]
                segment_reporter, timers, store, action, operation
            )

    def _run_schema_level_operations(
        self,
        request: Any,
        application: ApexApplication | None,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        segment_reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        store: ApexStore,
    ) -> None:
        for action, operation in self._schema_level_operations(
            request, application, gateway, resolver
        ):
            self._run_schema_action(  # type: ignore[attr-defined]
                segment_reporter, timers, store, action, operation
            )

    def _schema_level_operations(
        self,
        request: Any,
        application: ApexApplication | None,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
    ) -> list[tuple[str, Callable[[], None]]]:
        """The schema-level slices, bound to whichever application carries them.

        `application` is the one the workspace security context came from, or
        ``None`` on a schema hosting no APEX application at all. Report-only
        `-recent` runs export nothing, so they get nothing.
        """
        if request.recent_report_only:
            return []
        actions: list[tuple[str, Callable[[], None]]] = []
        if request.actions.get("files_ws"):
            actions.append(
                ("files_ws", lambda: self._write_static_files(gateway, resolver, application, 0))  # type: ignore[attr-defined]
            )
        if request.actions.get("rest"):
            actions.append(
                ("rest", lambda: self._write_rest_export(gateway, resolver, request.config))  # type: ignore[attr-defined]
            )
        return actions


__all__ = [name for name in globals() if not name.startswith("__")]
