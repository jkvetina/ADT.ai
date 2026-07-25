from __future__ import annotations

import contextlib
import sys
import time
from collections.abc import Callable, Sequence
from typing import TextIO

from adt_ai.cli.context import _print_completion_timer


def run_schema_sections(
    schemas: Sequence[str],
    run_schema: Callable[[str], int],
    *,
    first_started_at: float | None = None,
    timer_stdout: TextIO | None = None,
) -> int:
    """Run ``run_schema`` once per schema as a self-contained console segment.

    Reproduces N single-schema invocations concatenated: the caller prints the
    module banner once, then for every schema this drives the callback's own
    connection block and work, followed by a per-segment ``TIMER`` footer
    measuring that segment only. ``schemas`` is iterated lazily by index so a
    callback may append additional schemas mid-run (export_apex's missing-app
    owner routing) and still get a segment and timer for each appended schema.

    Every schema runs even when one fails; the first non-zero return becomes
    the aggregate exit code. An exception in ``run_schema`` propagates
    immediately, so the suppression latch below is set only once every
    requested/appended schema has completed without raising — leaving the
    shared teardown footer active to cover a mid-loop failure.
    """
    if not schemas:
        return 0
    exit_code = 0
    index = 0
    while index < len(schemas):
        schema = schemas[index]
        segment_start = (
            first_started_at
            if index == 0 and first_started_at is not None
            else time.monotonic()
        )
        schema_exit_code = run_schema(schema)
        exit_code = exit_code or schema_exit_code
        _print_completion_timer(segment_start, stdout=timer_stdout)
        index += 1
    _mark_final_timer_emitted()
    return exit_code


def _mark_final_timer_emitted() -> None:
    # sys.stdout is the runtime's _StdoutTracker; set defensively since a
    # direct/unit-test caller may leave a plain stream in place.
    with contextlib.suppress(AttributeError):
        sys.stdout.final_timer_emitted = True


__all__ = [name for name in globals() if not name.startswith("__")]
