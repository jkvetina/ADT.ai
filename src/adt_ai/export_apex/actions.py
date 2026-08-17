"""What the export formats are called, and how one action is run and timed.

Split out of `runner.py` (ADT #233), which crossed the 20 KB per-file context
budget, the same split `recompile/results.py` already made for that cap, rather
than taking an exception. `runner.py` keeps the orchestration: which schemas,
which applications, which slices in which order. This module owns the two things
that orchestration reaches for but does not decide, the format catalogue (with
the APEX-release gates that rule a format in or out) and the reporter/timer
wrapper every action goes through.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.progress import (
    FALLBACK_TARGET_SECONDS,
    ApexProgressReporter,
    CompactApexProgressReporter,
    _timer_value,
    _update_timer,
    segment_budget,
    segment_row_label,
)
from adt_ai.export_apex.writers import CollectionWriteResult
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.apex_version import readable_yaml_removed, supports_apexlang
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.progress import (
    ROW_INDENT,
    print_adt_header,
    progress_dot_capacity,
    schema_label,
)

# Labels only. The two-space indent moved into `shared/progress.row_left_margin`
# with `#380`, so no label spells its own margin any more and not one printed row
# changed. A caller that prints one of these outside the bar adds `ROW_INDENT`
# itself -- `print_apexlang_skip_row` below is the only one.
ACTION_HEADERS = {
    "full": "FULL APP EXPORT",
    "split": "SPLIT COMPONENTS",
    "readable": "READABLE COMPONENTS",
    "embedded": "EMBEDDED CODE REPORT",
    "apexlang": "APEXLANG EXPORT",
    "rest": "REST SERVICES",
    "files": "APPLICATION FILES",
    "files_ws": "WORKSPACE FILES",
}

# `-rest` and `-files_ws` write workspace artifacts, paths carrying no app id,
# so they belong to the schema and run exactly once however many applications it
# has. `#190` established that for `-rest` alone; `-files_ws` kept re-exporting
# the identical files once per application until ADT #233. The tuple order is
# the order they run in, so `planned_actions` can read it straight through.
SCHEMA_LEVEL_ACTIONS = ("files_ws", "rest")

# The formats exported from a collection query, in the order the runner walks
# them. Named here rather than inline in `runner._run_text_actions` because
# `planned_actions` below has to walk the same list in the same order (`#376`).
TEXT_ACTIONS = ("full", "split", "readable", "embedded", "apexlang")

# The APEX release that introduced `APEX_EXPORT.c_type_apexlang` and folded
# READABLE_YAML into it as a deprecated alias.
APEXLANG_MIN_APEX_RELEASE = "26.1"

# The `-compact` segment's own header, in Jan's wording (2026-08-16). Two
# spellings because a segment with no application is exporting the schema's
# workspace artifacts and nothing that could be called an app.
SEGMENT_APPS_HEADER = "EXPORTING {schema} APPS:"
SEGMENT_SCHEMA_HEADER = "EXPORTING {schema} SCHEMA:"


def planned_actions(request: Any, applications: list[ApexApplication]) -> list[tuple[int, str]]:
    """Every ``(app_id, action)`` pair this schema segment will run, in order.

    The compact bar's budget is only honest while the pairs it sums are the pairs
    that actually run, so this is the single enumeration and
    `runner._run_text_actions` walks the same `TEXT_ACTIONS` tuple. A second
    hand-written list would drift on the first format added to one of them, which
    is what `tests/export_apex/test_compact_progress.py` pins by replaying the
    runner and comparing.

    Schema-level slices carry app id `0`, the workspace timer slot they are
    already recorded under, and ride the first application's block (`#233`).
    """
    if request.recent_report_only:
        # A report-only run exports nothing at all, so it plans nothing.
        return []
    planned: list[tuple[int, str]] = []
    for index, application in enumerate(applications):
        for action in TEXT_ACTIONS:
            if not request.actions.get(action):
                continue
            if skipped_by_apex_release(action, request.apex_version):
                continue
            planned.append((application.app_id, action))
        if request.actions.get("files"):
            planned.append((application.app_id, "files"))
        if index == 0:
            planned.extend(_schema_level_pairs(request))
    if not applications:
        planned.extend(_schema_level_pairs(request))
    return planned


def _schema_level_pairs(request: Any) -> list[tuple[int, str]]:
    return [(0, action) for action in SCHEMA_LEVEL_ACTIONS if request.actions.get(action)]


def open_segment_bar(
    request: Any,
    applications: list[ApexApplication],
    timers: Any,
    schema: str,
) -> CompactApexProgressReporter | None:
    """The `-compact` bar for one schema segment, or ``None`` outside the mode.

    Opened once per segment, before the first application, because the point of
    the flag is that the segment has ONE row: a bar opened per application would
    be the per-application blocks again with a percentage on them.

    The dot track is sized here, once, from the widest label the segment can
    print, and every frame measures against it, so a percentage draws the same
    number of dots whichever slice happens to be running (`#380`).
    """
    if not request.compact:
        return None
    planned = planned_actions(request, applications)
    if not planned:
        # A report-only run, or one with no format selected, exports nothing and
        # has nothing for a bar to stand in for.
        return None
    # Both spellings are printed from their own literal here rather than through
    # a helper that picks one. The console inventory folds a `NAME.format(...)`
    # argument and cannot see through a function call, so a header chosen inside
    # a helper is a header the review step that approved it cannot see
    # (`tests/helpers/console_surface.py`).
    if applications:
        print_adt_header(SEGMENT_APPS_HEADER.format(schema=schema_label(schema)))
    else:
        print_adt_header(SEGMENT_SCHEMA_HEADER.format(schema=schema_label(schema)))
    bar = CompactApexProgressReporter(
        segment_budget(timers, planned),
        dot_capacity = segment_dot_capacity(planned),
    )
    bar.begin()
    return bar


def segment_dot_capacity(planned: list[tuple[int, str]]) -> int:
    """The dot track every row of this segment measures against.

    Sized from the widest label the segment will print, so the longest row still
    fits and every shorter one ends its dots early rather than growing extra
    ones. Jan, 2026-08-16: *"the dots should always match available space to
    calculate 100%"*.
    """
    widest = max(
        (segment_row_label(app_id, ACTION_HEADERS[action]) for app_id, action in planned),
        key = len,
        default = "",
    )
    return progress_dot_capacity(widest, CompactApexProgressReporter.line_width)


def skipped_by_apex_release(action: str, apex_version: str | None) -> bool:
    """Whether this instance's APEX release rules the format out.

    Two one-way gates, both reading the release the connection block already
    probed. An unknown release gates nothing in either direction.
    """
    if action == "apexlang":
        return not supports_apexlang(apex_version)
    if action == "readable":
        return readable_yaml_removed(apex_version)
    return False


def print_apexlang_skip_row() -> None:
    """Say why the APEXlang format produced nothing, instead of failing the run.

    A pre-26.1 instance has no `APEXLANG` export type. The release the instance
    is actually on is deliberately not repeated, the connection block a few
    lines above already prints it, and the requirement is the only half the
    reader cannot get anywhere else (ADT #232). No dotted leader either: the
    dots exist to carry the eye across to a measured result, and this format
    never ran (ADT #233).
    """
    print(
        f"{ROW_INDENT}{ACTION_HEADERS['apexlang']} "
        f"SKIPPED, NEEDS APEX {APEXLANG_MIN_APEX_RELEASE}"
    )


class ApexActionTimingMixin:
    """Runs one export slice through the progress reporter and records its timer."""

    def _run_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        store: ApexStore,
        application: ApexApplication,
        action: str,
        operation: Callable[[], None],
    ) -> None:
        elapsed = reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, application.app_id, action) or FALLBACK_TARGET_SECONDS,
            operation,
            app_id = application.app_id,
        )
        _update_timer(timers, application.app_id, action, elapsed)
        # One row, not the whole cache. The rolling average is still computed in
        # memory, so the estimate a later action reads inside this same run is
        # unchanged; only the write narrowed (`#369`).
        store.store_timer(
            application.app_id, action, _timer_value(timers, application.app_id, action)
        )

    def _run_schema_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        store: ApexStore,
        action: str,
        operation: Callable[[], None],
    ) -> None:
        """Run a schema-level action, timed under the workspace slot.

        The timer table is keyed by app id; app `0` is already the workspace
        slot (`-files_ws` writes workspace files as app 0), so a schema-level
        action reuses it rather than borrowing the estimate of whichever
        application happens to carry its row.
        """
        elapsed = reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, 0, action) or FALLBACK_TARGET_SECONDS,
            operation,
        )
        _update_timer(timers, 0, action, elapsed)
        store.store_timer(0, action, _timer_value(timers, 0, action))

    def _run_partial_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        application: ApexApplication,
        action: str,
        operation: Callable[[], CollectionWriteResult],
    ) -> CollectionWriteResult:
        result: CollectionWriteResult | None = None

        def wrapped_operation() -> None:
            nonlocal result
            result = operation()

        reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, application.app_id, action) or FALLBACK_TARGET_SECONDS,
            wrapped_operation,
            app_id = application.app_id,
        )
        return result or CollectionWriteResult([])

    def _schema_only_actions(
        self,
        request: Any,
        applications: list[ApexApplication],
        gateway: QueryGateway,
        resolver: ApexFileResolver,
    ) -> list[tuple[str, Callable[[], None]]]:
        """The schema-level slices owed by a schema with no application.

        With applications present these ride the first one's block instead, so
        this returns nothing; report-only `-recent` runs export nothing at all.
        """
        if applications or request.recent_report_only:
            return []
        actions: list[tuple[str, Callable[[], None]]] = []
        if request.actions.get("files_ws"):
            actions.append(
                ("files_ws", lambda: self._write_static_files(gateway, resolver, None, 0))  # type: ignore[attr-defined]
            )
        if request.actions.get("rest"):
            actions.append(
                ("rest", lambda: self._write_rest_export(gateway, resolver, request.config))  # type: ignore[attr-defined]
            )
        return actions
