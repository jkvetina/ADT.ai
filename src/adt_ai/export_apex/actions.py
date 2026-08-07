"""What the export formats are called, and how one action is run and timed.

Split out of `runner.py` (ADT #233), which crossed the 20 KB per-file context
budget — the same split `recompile/results.py` already made for that cap, rather
than taking an exception. `runner.py` keeps the orchestration: which schemas,
which applications, which slices in which order. This module owns the two things
that orchestration reaches for but does not decide — the format catalogue (with
the APEX-release gates that rule a format in or out) and the reporter/timer
wrapper every action goes through.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.progress import (
    FALLBACK_TARGET_SECONDS,
    ApexProgressReporter,
    _timer_value,
    _update_timer,
)
from adt_ai.export_apex.writers import CollectionWriteResult
from adt_ai.shared.apex_version import readable_yaml_removed, supports_apexlang
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.yaml_io import store_yaml_mapping

ACTION_HEADERS = {
    "full": "  FULL APP EXPORT",
    "split": "  SPLIT COMPONENTS",
    "readable": "  READABLE COMPONENTS",
    "embedded": "  EMBEDDED CODE REPORT",
    "apexlang": "  APEXLANG EXPORT",
    "checksum": "  APP CHECKSUM",
    "rest": "  REST SERVICES",
    "files": "  APPLICATION FILES",
    "files_ws": "  WORKSPACE FILES",
}

# `-rest` and `-files_ws` write workspace artifacts — paths carrying no app id —
# so they belong to the schema and run exactly once however many applications it
# has. `#190` established that for `-rest` alone; `-files_ws` kept re-exporting
# the identical files once per application until ADT #233.
SCHEMA_LEVEL_ACTIONS = ("files_ws", "rest")

# The APEX release that introduced `APEX_EXPORT.c_type_apexlang` and folded
# READABLE_YAML into it as a deprecated alias.
APEXLANG_MIN_APEX_RELEASE = "26.1"


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
    is actually on is deliberately not repeated — the connection block a few
    lines above already prints it, and the requirement is the only half the
    reader cannot get anywhere else (ADT #232). No dotted leader either: the
    dots exist to carry the eye across to a measured result, and this format
    never ran (ADT #233).
    """
    print(f"{ACTION_HEADERS['apexlang']} SKIPPED, NEEDS APEX {APEXLANG_MIN_APEX_RELEASE}")


class ApexActionTimingMixin:
    """Runs one export slice through the progress reporter and records its timer."""

    def _run_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        timers_file: Path,
        application: ApexApplication,
        action: str,
        operation: Callable[[], None],
    ) -> None:
        elapsed = reporter.run(
            ACTION_HEADERS[action],
            _timer_value(timers, application.app_id, action) or FALLBACK_TARGET_SECONDS,
            operation,
        )
        _update_timer(timers, application.app_id, action, elapsed)
        store_yaml_mapping(timers_file, timers)

    def _run_schema_action(
        self,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        timers_file: Path,
        action: str,
        operation: Callable[[], None],
    ) -> None:
        """Run a schema-level action, timed under the workspace slot.

        `apex_timers.yaml` is keyed by app id; app `0` is already the workspace
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
        store_yaml_mapping(timers_file, timers)

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
