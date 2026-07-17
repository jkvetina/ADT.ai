from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from multiprocessing.pool import ThreadPool
from typing import Any, Protocol

from adt_ai.shared.progress import DottedProgressBar

# Assumed action duration when no timing history exists yet: keeps the progress
# bar crawling instead of jumping to 99% on the first-ever run of an action.
FALLBACK_TARGET_SECONDS = 999.0


class ApexProgressReporter(Protocol):
    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], None],
    ) -> float:
        ...


class ConsoleApexProgressReporter:
    line_width = 78

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = interval
        self._progress = DottedProgressBar(line_width=self.line_width)

    def run(
        self,
        header: str,
        target_seconds: float,
        operation: Callable[[], None],
    ) -> float:
        started_at = time.monotonic()
        progress = 0.0
        with ThreadPool(processes=1) as pool:
            result = pool.apply_async(operation)
            while not result.ready():
                progress = self._print_progress(header, progress, target_seconds, started_at)
            result.get()
        elapsed = time.monotonic() - started_at
        self._print_done(header, elapsed)
        return elapsed

    def _print_progress(
        self,
        header: str,
        progress: float,
        target_seconds: float,
        started_at: float,
    ) -> float:
        target = target_seconds if target_seconds > 0 else FALLBACK_TARGET_SECONDS
        elapsed = time.monotonic() - started_at
        visible_progress = min(max(progress, elapsed), target)
        percent = min(int((visible_progress / target * 100) + 0.5), 99)
        remaining = max(0, int((target - visible_progress) + 0.5))
        self._print_line(header, percent, remaining)
        time.sleep(self.interval)
        return min(max(progress + self.interval, elapsed), target)

    def _print_done(self, header: str, elapsed: float) -> None:
        self._print_line(header, 100, int(elapsed), close=True)

    def _print_line(
        self,
        header: str,
        percent: int,
        seconds: int,
        close: bool = False,
    ) -> None:
        self._progress.print_line(header, percent, seconds, close=close)

    def _line_text(self, header: str, percent: int, seconds: int) -> str:
        return self._progress.line_text(header, percent, seconds)

def _timer_value(timers: Mapping[Any, Any], app_id: int, action: str) -> float:
    app_timers = timers.get(app_id) or timers.get(str(app_id)) or {}
    if not isinstance(app_timers, Mapping):
        return 0.0
    value = app_timers.get(action) or 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0

def _update_timer(timers: dict[Any, Any], app_id: int, action: str, elapsed: float) -> None:
    key: Any = app_id if app_id in timers or str(app_id) not in timers else str(app_id)
    app_timers = timers.get(key)
    if not isinstance(app_timers, dict):
        app_timers = {}
        timers[key] = app_timers
    previous = _timer_value(timers, app_id, action)
    timer = (elapsed + previous) / 2 if previous > 0 else elapsed
    app_timers[action] = round(timer, 2)
