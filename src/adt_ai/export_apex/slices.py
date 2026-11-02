"""Each slice of one application's export, and what happens when one fails.

Split out of `runner.py` for the 24 KB per-file context budget, the move
`actions.py` and `schema_level.py` already made (ADT #233, #385): ADT #1092
added a guard around every slice, and the runner crossed the cap carrying it.
`runner.py` keeps the orchestration, which schemas, which applications and what
an application's block does before and after its slices. This module owns the
slices themselves: the collection formats in `TEXT_ACTIONS` order, the
application's static files and the two schema-level slices, each through
`_slice`, so one that fails is listed at the end instead of ending the run.

`_run_action`, `_run_schema_action`, `_run_partial_action`, the writers and the
query constants come from the runner's other mixins and its class body, the way
every mixin in this package reaches its peers.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from typing import Any

from oracledb import Error as OracleError

from adt_ai.export_apex.actions import (
    TEXT_ACTIONS,
    print_apexlang_skip_row,
    skipped_by_apex_release,
)
from adt_ai.export_apex.deep import ApexDeepFilterError
from adt_ai.export_apex.failures import ApexSliceFailure, explain_refusal
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.filters import ApexExplicitFilter
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.partial import _has_explicit, _is_partial, _print_components
from adt_ai.export_apex.postprocess import _bind_params, _export_options
from adt_ai.export_apex.progress import ApexProgressReporter
from adt_ai.export_apex.recent import RecentComponentFilter
from adt_ai.export_apex.writers import CollectionWriteResult
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.db import QueryGateway


@dataclass
class SetupOutcome:
    """Whether the application's setup was refused, read after its guard closes."""

    failed: bool = False


class ApexSliceMixin:
    """Runs every slice of an application, carrying on past one that fails."""

    fatal_error: Callable[[Exception], bool] | None = None
    _failures: list[ApexSliceFailure]
    _keep_going: bool = False
    # The peers this mixin reaches, declared rather than suppressed: the runner's
    # timing and writer mixins define them, so the annotations add no attribute.
    _run_action: Callable[..., None]
    _run_schema_action: Callable[..., None]
    _run_partial_action: Callable[..., CollectionWriteResult]
    _write_collection_files: Callable[..., CollectionWriteResult]
    _write_static_files: Callable[..., None]
    _write_rest_export: Callable[..., None]

    def _slice(
        self,
        application: ApexApplication | None,
        action: str,
        call: Callable[[], object],
    ) -> bool:
        """Run one slice; under `keep_going` a failure is recorded, not raised.

        The slice's own row already closed on `FAILED` inside the reporter, so
        nothing is printed here. Every failure is listed once the run is done
        (ADT #1092). A failure no later slice can get past, a lost connection,
        still ends the run at once.
        """
        try:
            call()
        except Exception as error:
            fatal = self.fatal_error is not None and self.fatal_error(error)
            if not self._keep_going or fatal:
                raise
            self._failures.append(ApexSliceFailure(application, action, error))
            return False
        return True

    @contextmanager
    def _guarded_setup(self, application: ApexApplication) -> Iterator[SetupOutcome]:
        """An application's own setup, before any slice, guarded like one (ADT #1114).

        The collection start, the enrichments and the comments are database
        reads that one application can refuse while the next is healthy, so under
        `keep_going` that refusal is listed against the application and the run
        carries on. What no later application can get past still ends it: a
        lost connection (`fatal_error`), and an argument refusal, which belongs
        to the run and never to an application.
        """
        outcome = SetupOutcome()
        try:
            yield outcome
        except ApexDeepFilterError:
            raise
        except (OracleError, RuntimeError) as error:
            fatal = self.fatal_error is not None and self.fatal_error(error)
            if not self._keep_going or fatal or "ORA-20987:" not in str(error):
                raise
            self._failures.append(ApexSliceFailure(application, None, error))
            outcome.failed = True

    def _run_text_actions(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        request: Any,
        enrichments: Mapping[int, str],
        developers: Mapping[str, Mapping[str, str]],
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        store: ApexStore,
        recent_filter: RecentComponentFilter,
        explicit_filter: ApexExplicitFilter,
        page_names: dict[int, str],
        deep_rows: list[dict[str, Any]],
    ) -> bool:
        """Every collection format the request names; whether all of them wrote."""
        exported = True
        # Shared `TEXT_ACTIONS` order, never a second list here: the compact
        # bar's budget walks the same tuple (see `actions.planned_actions`).
        for action in TEXT_ACTIONS:
            sql = getattr(self, f"EXPORT_{action.upper()}_QUERY")
            if not request.actions.get(action):
                continue
            if skipped_by_apex_release(action, request.apex_version):
                # The pre-26.1 `apexlang` miss is announced only to the user who
                # named that format, the line answers "where is my APEXlang
                # export?", a question `-all` never asked, so under `-all` it was
                # a notice about something nobody requested (ADT #235). The 26.1
                # `readable` skip is silent always (Jan, 2026-07-27).
                if action == "apexlang" and action in request.explicit_actions:
                    print_apexlang_skip_row()
                continue

            def operation(sql: str = sql, action: str = action) -> CollectionWriteResult:
                binds = _bind_params(
                    sql, {"app_id": application.app_id, **_export_options(request.config)}
                )
                try:
                    gateway.execute(sql, binds)
                    # Inside the `try` too: an `ords:` connection sends the
                    # export block with this read, so its refusal surfaces here.
                    return self._write_collection_files(
                        gateway,
                        resolver,
                        application,
                        action,
                        enrichments,
                        request.config,
                        developers,
                        request.release,
                        recent_filter,
                        explicit_filter,
                        page_names,
                    )
                except Exception as error:
                    explained = explain_refusal(gateway, sql, binds, error)
                    if explained is error:
                        raise
                    raise explained from error

            def run_slice(
                action: str = action,
                operation: Callable[[], CollectionWriteResult] = operation,
            ) -> None:
                if _is_partial(request):
                    result = self._run_partial_action(
                        reporter,
                        timers,
                        application,
                        action,
                        operation,
                    )
                    if _has_explicit(request) or recent_filter.lists_written:
                        _print_components([*deep_rows, *result.rows])
                else:
                    self._run_action(
                        reporter,
                        timers,
                        store,
                        application,
                        action,
                        operation,
                    )

            exported = self._slice(application, action, run_slice) and exported
        return exported

    def _run_file_and_schema_slices(
        self,
        gateway: QueryGateway,
        resolver: ApexFileResolver,
        application: ApexApplication,
        request: Any,
        schema_slice: bool,
        reporter: ApexProgressReporter,
        timers: dict[Any, Any],
        store: ApexStore,
    ) -> bool:
        """`-files`, then the schema-level slices; whether the app's files wrote.

        `-files_ws` and `-rest` belong to no application, so a failure there is
        listed on its own and holds none of the app's state back.
        """
        exported = True
        if request.actions.get("files"):
            exported = self._slice(
                application,
                "files",
                partial(
                    self._run_action,
                    reporter,
                    timers,
                    store,
                    application,
                    "files",
                    partial(
                        self._write_static_files,
                        gateway,
                        resolver,
                        application,
                        application.app_id,
                    ),
                ),
            )
        if request.actions.get("files_ws") and schema_slice:
            self._slice(
                None,
                "files_ws",
                partial(
                    self._run_schema_action,
                    reporter,
                    timers,
                    store,
                    "files_ws",
                    partial(self._write_static_files, gateway, resolver, application, 0),
                ),
            )
        if request.actions.get("rest") and schema_slice:
            self._slice(
                None,
                "rest",
                partial(
                    self._run_schema_action,
                    reporter,
                    timers,
                    store,
                    "rest",
                    partial(self._write_rest_export, gateway, resolver, request.config),
                ),
            )
        return exported


__all__ = ["ApexSliceMixin"]
