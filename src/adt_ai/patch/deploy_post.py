"""What `-deploy` does after its last script, on rows the screen already has (ADT #988).

`#372` ran the post-deploy reads under `DEPLOYING PATCH:` with its table held
open, on the rule that a header covers its whole section. ADT #988 tightened that
rule -- a finished row is a result and announces nothing -- and the guard then
found the invalid-object recompile, the view-column check and the application
scan all running under a screen of finished rows. Jan's calls, both on rows that
already exist and neither a new line:

* **The recompile and the view check fold into the table's LAST row**, which
  stays open, clock running, until they finish (`HeldLastRow`).
* **The application scan streams under `VERIFYING APPLICATIONS:`**, one row per
  application, the same row `VALIDATING APEXLANG APPS:` prints: *"the outcome
  should be same as other apexlang validation, looks like you invented something
  new here"* (`verify_streamed`).
* **Each build-status lock is released under a row that is open** (`EarlyRelease`):
  a scanned application's inside its own scan row, after any revert, so `#720`'s
  `BUILD STATUS:` line can ride that row; every other one under the held table
  row. The lock's `finally` release is left for a run that never got that far.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from adt_ai.patch.apex_lock import BuildStatusLock, build_status_timeline
from adt_ai.patch.apex_scan import ApexScanReport, verify_applications
from adt_ai.shared.apex_store import ApexStore, apex_store_path
from adt_ai.validate.files import app_label
from adt_ai.validate.runner import record_timer, timer_estimate

#: The `apex.db` timer a scan row counts down from, beside `validate`'s own.
SCAN_TIMER = "scan"


class HeldLastRow:
    """The deploy reporter, with its newest finished row held OPEN until `release`.

    Only the row a script actually ran in is held: a `NOT RUN` row never opened,
    so it queues behind the held one and prints after it, and the finished table
    reads exactly as it always did. The next `begin_script` flushes both, so only
    the table's last row is ever still open when the loop ends. Its closing
    TIMER cell is the time the row stood open, the clock the live row showed.
    Everything else passes straight through to the wrapped reporter.
    """

    def __init__(self, reporter: Any) -> None:
        self.reporter = reporter
        self._began = 0.0
        self._running = False
        self._held: Any = None
        self._queued: list[Any] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.reporter, name)

    def begin_script(self, item: Any) -> None:
        self.release()
        self._began = time.monotonic()
        self._running = True
        self.reporter.begin_script(item)

    def end_script(self, result: Any) -> None:
        if self._running:
            self._running = False
            self._held = result
        elif self._held is not None:
            self._queued.append(result)
        else:
            self.reporter.end_script(result)

    def release(self) -> None:
        """Close the held row, then whatever queued behind it."""
        if self._held is not None:
            held = self._held
            self._held = None
            self.reporter.end_script(replace(held, seconds=time.monotonic() - self._began))
        queued, self._queued = self._queued, []
        for result in queued:
            self.reporter.end_script(result)


class EarlyRelease:
    """The deploy's lock ledger, each lock released the moment a row covers it.

    ``release`` is `apex_lock.release_targets` bound to the deploy's connections.
    A released lock leaves ``locks`` -- the dict `build_status_lock` releases in
    its `finally` -- so nothing is released twice; `ledger` is both halves.
    """

    def __init__(
        self,
        locks   : dict[int, BuildStatusLock],
        release : Callable[[dict[int, BuildStatusLock]], dict[int, BuildStatusLock]],
    ) -> None:
        self.locks = locks
        self._release = release
        self.released: dict[int, BuildStatusLock] = {}

    def __call__(self, app_ids: Iterable[int]) -> None:
        for app_id in sorted(set(app_ids) & set(self.locks)):
            self.released.update(self._release({app_id: self.locks.pop(app_id)}))

    def ledger(self) -> dict[int, BuildStatusLock]:
        return {**self.locks, **self.released}

    def note(self, report: ApexScanReport) -> list[str]:
        """`#720`'s `BUILD STATUS:` line for the scan row, byte for byte.

        Three moments rather than the log's four: what the application was, the
        lock, and what it is left on. None for an application nothing locked
        (Jan, 2026-09-09: *"dont show, it is a noise"*), nor under a failed scan,
        whose screen is `ERROR - VERIFICATION FAILED:` and nothing else (`#963`).
        """
        lock = self.released.get(report.app_id)
        timeline = build_status_timeline(lock) if lock is not None else ""
        return [f"  BUILD STATUS: {timeline}"] if timeline and not report.failed else []


def scan_labels(root: Any, app_ids: Iterable[int], items: Sequence[Any]) -> dict[int, str]:
    """Each scanned application named the way its validation row names one.

    The scan reads the application at the id the deploy LANDED it on, so that
    id labels the row, with the alias APEX holds there: an import carries its
    own (`alias_for_target`, the source alias unless `-app` retargeted it); an
    application a per-app install script landed carries the one `export_apex`
    recorded, or none.
    """
    labels = {item.target_id: app_label(item.target_id, item.alias_for_target) for item in items}
    missing = [app_id for app_id in app_ids if app_id not in labels]
    if missing and apex_store_path(root).is_file():
        with ApexStore.load(root) as store:
            for app_id in missing:
                entry = store.application(app_id)
                alias = str(entry.get("app_alias") or "") if isinstance(entry, Mapping) else ""
                labels[app_id] = app_label(app_id, alias)
    return labels


def verify_streamed(
    app_ids         : Iterable[int],
    gateway_for_app : Callable[[int], Any],
    *,
    rows            : Any,
    labels          : Mapping[int, str],
    root            : Any,
    after_scan      : Callable[[ApexScanReport], Sequence[str]] | None = None,
    **scan          : Any,
) -> list[ApexScanReport]:
    """`verify_applications`, one application at a time, each under its own row.

    ``rows`` is a `validate` row reporter (`begin`/`finish`, `expect` for the
    countdown, `note`); ``None`` scans exactly as `verify_applications` does.
    ``after_scan`` runs inside the row before it closes: the revert a failed
    scan triggers (`#727`) is part of that application's verification, and so
    is the lock release after it. The lines it answers print on the row's note
    line once it has closed (`#720`'s `BUILD STATUS:`).
    """
    reports: list[ApexScanReport] = []
    for app_id in sorted(set(app_ids)):
        label = labels.get(app_id, str(app_id))
        if rows is not None:
            expect = getattr(rows, "expect", None)
            if expect is not None:
                expect(timer_estimate(root, app_id, SCAN_TIMER))
            rows.begin(label)
        started = time.monotonic()
        notes: Sequence[str] = ()
        try:
            report = verify_applications([app_id], gateway_for_app, **scan)[0]
            record_timer(root, app_id, SCAN_TIMER, time.monotonic() - started)
            if after_scan is not None:
                notes = after_scan(report)
        except Exception:
            if rows is not None:
                rows.finish(label, "FAILED")
            raise
        if rows is not None:
            rows.finish(label, report.status)
            # The one fact the row's clock cannot carry: why a scan that did not
            # fail verified nothing (`UNSUPPORTED`), in `validate`'s note line.
            if report.reason and not report.failed:
                rows.note(report.reason)
            for line in notes:
                rows.note(line)
        reports.append(report)
    return reports


__all__ = ["SCAN_TIMER", "EarlyRelease", "HeldLastRow", "scan_labels", "verify_streamed"]
