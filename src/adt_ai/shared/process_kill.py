"""End a SQLcl child and everything it started (ADT #1094).

`sql` is a bash script that starts `java` as its child rather than exec'ing it,
so `process.kill()` on a timeout ended the launcher and left the JVM running the
script against the database after the CLI had printed that SQLcl "was killed".
`shared/sqlcl_console.py` fixed its own driven session under ADT #923: the
launcher is spawned into a session of its own (`start_new_session=True`), so its
pid names a process group, and one `SIGKILL` to that group ends the JVM with it.
The two script transports, `sqlcl_stream` and `sqlcl_script`, kept the
launcher-only kill; this module is the one answer both now call.

Windows has no process group to signal this way, so there the launcher alone is
killed, as it always was. The group id is the launcher's pid and is only safe
while the launcher is unreaped: reaped, the number can belong to another group,
so a child already waited for is never signalled by group.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def own_process_group() -> bool:
    """Whether a child is spawned into a session of its own: POSIX only."""
    return os.name != "nt"


def kill_process_group(process: subprocess.Popen[Any]) -> None:
    """Best effort: the group `process` leads, then the launcher itself, then reap it."""
    killpg = getattr(os, "killpg", None)
    if killpg is not None and own_process_group() and process.returncode is None:
        with contextlib.suppress(OSError):
            killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.kill()
    with contextlib.suppress(Exception):
        process.wait()


def run_in_own_group(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
) -> tuple[str, str, int]:
    """Run `command` to its end on closed pipes: its stdout, stderr and exit code.

    What `subprocess.run(capture_output=True, input="", timeout=...)` did for
    `sqlcl_script`, with the child in its own session and the whole group killed
    when the deadline passes or anything else ends the wait early. The
    `TimeoutExpired` is re-raised as `run` raised it, partial output included.
    `encoding`/`errors` are named so the console codepage never decides what the
    transcript reads (ADT #743).
    """
    with subprocess.Popen(
        list(command),
        cwd               = cwd,
        stdin             = subprocess.PIPE,
        stdout            = subprocess.PIPE,
        stderr            = subprocess.PIPE,
        encoding          = "utf-8",
        errors            = "replace",
        env               = dict(environment),
        start_new_session = own_process_group(),
    ) as process:
        try:
            stdout, stderr = process.communicate(input="", timeout=timeout_seconds)
        except BaseException:
            kill_process_group(process)
            raise
    return stdout or "", stderr or "", process.returncode


__all__ = ["kill_process_group", "own_process_group", "run_in_own_group"]
