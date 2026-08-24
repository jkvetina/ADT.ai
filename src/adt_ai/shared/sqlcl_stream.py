"""The transport a live reader watches a SQLcl script run through (ADT #457).

Split out of ``shared.sqlcl_script`` when Windows gained a second transport and
that module went over the context-size guard. It is the same seam
``shared.sqlcl_console`` is for ``sqlcl_session``: everything above reads lines
and knows nothing about the platform, and each transport below owns exactly one
platform's answer to the same question.

The question is why a live reader needs a transport at all, and it was measured
rather than assumed: ``shared/sqlcl_session.py`` established on 2026-08-19
against SQLcl 26.2 that the JVM block-buffers stdout when it is not talking to a
terminal, "60 seconds, not one byte". So ``patch -deploy`` was never withholding
progress, it had none to print, and moving a print statement could not have
fixed it.

**The two platforms do not get the same answer, and that is the point.** POSIX
puts stdout and stderr on a pty while stdin stays a device that is already at
EOF, which is ADT #188's invariant. Windows cannot copy that: `pywinpty`'s
`PtyProcess.spawn` takes `argv`, `cwd`, `env`, `dimensions` and `backend` and
offers no separate stdin, so a pseudo console necessarily hands the child all
three handles at once. That is right for `sqlcl_session`, which drives
statements into the console it opened, and wrong for a script run, where a
failed CONNECT falls back to a username prompt and the only thing that ends it
is EOF. So Windows takes a pipe, and `stream_on_pipe` says what that costs.
"""

from __future__ import annotations

import contextlib
import os
import select
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from adt_ai.shared.sqlcl_errors import SqlclTimeoutError


def _timed_out(timeout_seconds: float | None, collected: Sequence[str]) -> SqlclTimeoutError:
    """The one message both transports report a killed child with."""
    transcript = "\n".join(collected).strip()
    return SqlclTimeoutError(
        f"SQLcl did not finish within {timeout_seconds:g} seconds and was killed."
        + (f"\n{transcript}" if transcript else "")
    )


def stream_on_pty(
    command: Sequence[str],
    root: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
    on_line: Callable[[str], None],
) -> tuple[str, int]:
    """POSIX: run ``command`` with its output on a pty, line by line (ADT #434).

    Three mechanics come straight from `sqlcl_session`, for the same reasons it
    gives: `TERM=dumb` plus the JLine property, or SQLcl writes a cursor-position
    query at startup and blocks until something answers it; terminal echo off, or
    the script comes back as its own output. The fourth is this function's own.
    **Only stdout and stderr take the pty; stdin stays `DEVNULL`**: a failed
    CONNECT makes SQLcl fall back to prompting for a username, and on a terminal
    that prompt has nothing to end it, which is the hang ADT #188 fixed by
    refusing the child the caller's stdin. `sqlcl_session` needs a writable stdin
    because it drives statements; a script run does not.

    `DEVNULL` here and an empty pipe on the non-streaming path is not an
    inconsistency: `/dev/null` answers the `available()` call SQLcl's console
    builder makes, and Windows' `NUL` does not, which is the whole of ADT #457.
    This function never runs there.

    Returns the transcript and the exit code, so the caller classifies a failure
    exactly as it does on the other two paths.
    """
    # POSIX-only, imported here rather than at module scope: this module is
    # reached by every command through `sqlcl_script` and only a live reader
    # ever opens a pty (ADT #449).
    import pty
    import termios

    environment = dict(environment)
    environment["TERM"] = "dumb"
    environment["JAVA_TOOL_OPTIONS"] = (
        environment.get("JAVA_TOOL_OPTIONS", "") + " -Dorg.jline.terminal.dumb=true"
    ).strip()

    master, slave = pty.openpty()
    attributes = termios.tcgetattr(slave)
    attributes[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attributes)
    process = subprocess.Popen(
        list(command),
        cwd    = root,
        stdin  = subprocess.DEVNULL,
        stdout = slave,
        stderr = slave,
        env    = environment,
    )
    os.close(slave)

    collected: list[str] = []
    buffer = b""
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds

    def emit(raw: bytes) -> None:
        # A pty turns every `\n` into `\r\n`, so the marker has to come back off
        # or the transcript stops matching what the pipe path returned and every
        # parser reading it (`_deployment_succeeded`, the progress echoes) sees a
        # different string.
        line = raw.decode("utf-8", "replace").rstrip("\r")
        collected.append(line)
        on_line(line)

    try:
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                process.kill()
                process.wait()
                raise _timed_out(timeout_seconds, collected)
            if not select.select([master], [], [], remaining if remaining else 1.0)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                # The child closed its end. Linux reports EIO here where macOS
                # returns empty; both mean the same thing.
                chunk = b""
            if not chunk:
                break
            buffer += chunk
            *complete, buffer = buffer.split(b"\n")
            for raw in complete:
                emit(raw)
        if buffer.strip():
            emit(buffer)
    finally:
        with contextlib.suppress(OSError):
            os.close(master)
    return "\n".join(collected), process.wait()


def stream_on_pipe(
    command: Sequence[str],
    root: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
    on_line: Callable[[str], None],
) -> tuple[str, int]:
    """Windows: run ``command`` on a pipe, handing over each finished line.

    `patch -deploy` passes `on_line` whenever it has a reporter, so before this
    existed the command reached `stream_on_pty`'s `import pty` and died with
    `ModuleNotFoundError` before SQLcl was launched. The `windows-latest` run
    for ADT #449 recorded that as three pty tests needing a platform skip, which
    read as a test-environment nuisance rather than as a shipped command with no
    transport on the platform.

    **What this cannot promise, said here rather than left to be discovered.** A
    pipe is not a terminal, so the JVM may block-buffer its stdout and deliver
    the transcript in one block at exit instead of line by line. ADT #449
    measured exactly that shape for an *interactive* SQLcl on Windows: ninety
    seconds produced the JVM's own notice and nothing else. Whether a *script*
    run, which ends on its own, buffers the same way is **not measured**, and
    there is no Windows machine here to measure it on. The reader is fed every
    line either way and the transcript is identical, so the failure mode is a
    progress bar that fills late, never a wrong result. The module docstring
    says why a pseudo console is not the alternative.

    Reading runs on its own thread because `select` does not serve pipes on
    Windows.
    """
    process = subprocess.Popen(
        list(command),
        cwd    = root,
        stdin  = subprocess.PIPE,
        stdout = subprocess.PIPE,
        stderr = subprocess.STDOUT,
        env    = dict(environment),
    )
    # Closed at once, never inherited: ADT #188's invariant through a pipe
    # rather than a device, which is what lets SQLcl's console builder start at
    # all on Windows, and the reason a failed CONNECT ends on EOF instead of
    # sitting at an invisible username prompt.
    if process.stdin is not None:
        process.stdin.close()

    collected: list[str] = []

    def pump() -> None:
        if process.stdout is None:
            return
        for raw in process.stdout:
            # `rstrip("\r\n")` and not a bare `rstrip()`: a transcript line's own
            # trailing spaces belong to it, and every parser downstream reads the
            # same string the pty transport produces.
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            collected.append(line)
            on_line(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as expired:
        process.kill()
        process.wait()
        reader.join(timeout=5.0)
        raise _timed_out(timeout_seconds, collected) from expired
    # The child is gone, so the pipe is at EOF and the pump ends on its own; the
    # bound is a backstop, never the normal path.
    reader.join(timeout=5.0)
    return "\n".join(collected), process.returncode


def open_stream(
    command: Sequence[str],
    root: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
    on_line: Callable[[str], None],
) -> tuple[str, int]:
    """The live-reader transport this platform has.

    Keyed on `os.name`, the same way `shared/sqlcl_console.open_console` picks
    its console, and for the same reason: a missing `pty` on Windows is a
    platform fact to route around, never an import to catch.
    """
    if os.name == "nt":
        return stream_on_pipe(command, root, environment, timeout_seconds, on_line)
    return stream_on_pty(command, root, environment, timeout_seconds, on_line)
