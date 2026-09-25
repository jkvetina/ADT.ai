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

from collections.abc import Callable, Mapping
from typing import Any

from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.progress import (
    FALLBACK_TARGET_SECONDS,
    ApexProgressReporter,
    CompactApexProgressReporter,
    _timer_value,
    _update_timer,
    row_key,
)
from adt_ai.export_apex.schema_level import SCHEMA_LEVEL_ACTIONS, schema_level_only
from adt_ai.export_apex.writers import CollectionWriteResult
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.apex_version import readable_yaml_removed, supports_apexlang
from adt_ai.shared.progress import (
    ROW_INDENT,
    print_adt_header,
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
    # Not an export format: the compile `-apexlang` runs over the tree it just
    # wrote (ADT #971), timed under its own `apex.db` timer (ADT #973).
    "validate": "VALIDATING APEXLANG",
}

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
        if request.validate_apexlang and (application.app_id, "apexlang") in planned:
            planned.append((application.app_id, "validate"))
        if index == 0:
            planned.extend(_schema_level_pairs(request))
    if not applications:
        planned.extend(_schema_level_pairs(request))
    return planned


def _schema_level_pairs(request: Any) -> list[tuple[int, str]]:
    return [(0, action) for action in SCHEMA_LEVEL_ACTIONS if request.actions.get(action)]


def segment_row_budgets(
    timers: Mapping[Any, Any],
    planned: list[tuple[int, str]],
) -> dict[tuple[str, Any], float]:
    """What each ROW of this segment is expected to cost, in seconds.

    A row's budget is the sum of the stored time of the pairs that run on it,
    which is what makes it **time-weighted rather than item-counted**: a
    `FULL APP EXPORT` and a `SPLIT COMPONENTS` slice differ by an order of
    magnitude, so a row advanced one step per action would sit at 50% with 90%
    of the work still to come. A pair with no history falls back rather than
    counting as free, for the same reason `FALLBACK_TARGET_SECONDS` exists on
    the per-action bar.

    Built here rather than in `progress.py` because grouping the pairs needs
    `ACTION_HEADERS`, and a schema-level row is keyed on the header it prints
    (`-rest` and `-files_ws` share timer slot `0` and are two separate rows).
    Insertion order is the order the rows open, which is what lets the bar draw
    its first row before any work starts.
    """
    budgets: dict[tuple[str, Any], float] = {}
    for app_id, action in planned:
        key = row_key(app_id, ACTION_HEADERS[action])
        budgets[key] = budgets.get(key, 0.0) + (
            _timer_value(timers, app_id, action) or FALLBACK_TARGET_SECONDS
        )
    return budgets


def open_segment_bar(
    request: Any,
    applications: list[ApexApplication],
    timers: Any,
    schema: str,
) -> CompactApexProgressReporter | None:
    """The `-compact` bar for one schema segment, or ``None`` outside the mode.

    One bar object for the segment, drawing one ROW per unit of work inside it:
    an application, or a schema-level slice. What `-compact` removes is the
    per-application HEADER BLOCK and the row per action under it, not the ability
    to see which application finished. `#376` removed both, and left a run's whole
    screen reading as whichever slice happened to run last (`#772`).

    The dot track is not sized here any more. `#380` measured it once per
    segment, from the widest label the segment could print, which left every
    shorter slice's leader stopping short of the right margin; `#767` moved the
    sizing back into the renderer, against the label being drawn.
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
    #
    # A segment exporting only workspace artifacts is not exporting apps, whatever
    # the schema happens to host, so it takes the schema spelling (`#385`).
    if applications and not schema_level_only(request.actions):
        print_adt_header(SEGMENT_APPS_HEADER.format(schema=schema_label(schema)))
    else:
        print_adt_header(SEGMENT_SCHEMA_HEADER.format(schema=schema_label(schema)))
    bar = CompactApexProgressReporter(segment_row_budgets(timers, planned))
    bar.begin()
    return bar


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
        operation: Callable[[], object],
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
        operation: Callable[[], object],
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
