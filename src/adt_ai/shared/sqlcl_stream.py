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
is EOF. So Windows takes a pipe, and `stream_on_pipe` says what was measured
on it.
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


class SqlclStreamStateError(RuntimeError):
    """A streaming transport did not create the reader its lifecycle requires."""


def _require_reader(reader: threading.Thread | None) -> threading.Thread:
    if reader is None:
        raise SqlclStreamStateError("SQLcl reader thread was not started")
    return reader


def keep_every_line(line: str) -> str:
    """The default `scrub`: a transport given no scrubber changes nothing."""
    return line


def _timed_out(
    timeout_seconds: float | None,
    collected: Sequence[str],
    scrub: Callable[[str], str],
) -> SqlclTimeoutError:
    """The one message both transports report a killed child with.

    The transcript goes through `scrub` first, exactly as the non-streaming
    branch scrubs its own partial output (`sqlcl_script._run_sqlcl`): the lines
    handed to `on_line` were scrubbed by the caller's lambda, but `collected`
    holds them raw, so a timeout could put a cleartext connect line into the
    error message and from there into a deployment log (ADT #661).
    """
    transcript = scrub("\n".join(collected)).strip()
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
    scrub: Callable[[str], str] = keep_every_line,
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
    process: subprocess.Popen[bytes] | None = None
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
        slave = -1
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise _timed_out(timeout_seconds, collected, scrub)
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
        return "\n".join(collected), process.wait()
    except BaseException:
        if process is not None:
            _kill_and_reap(process)
        raise
    finally:
        if slave >= 0:
            with contextlib.suppress(OSError):
                os.close(slave)
        with contextlib.suppress(OSError):
            os.close(master)


def stream_on_pipe(
    command: Sequence[str],
    root: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
    on_line: Callable[[str], None],
    scrub: Callable[[str], str] = keep_every_line,
) -> tuple[str, int]:
    """Windows: run ``command`` on a pipe, handing over each finished line.

    `patch -deploy` passes `on_line` whenever it has a reporter, so before this
    existed the command reached `stream_on_pty`'s `import pty` and died with
    `ModuleNotFoundError` before SQLcl was launched. The `windows-latest` run
    for ADT #449 recorded that as three pty tests needing a platform skip, which
    read as a test-environment nuisance rather than as a shipped command with no
    transport on the platform.

    **A script run on this pipe is live, and that is measured.** A pipe is not
    a terminal, so the JVM could have block-buffered its stdout and delivered
    the transcript in one block at exit; ADT #449 measured exactly that shape
    for an *interactive* SQLcl on Windows, ninety seconds and nothing but the
    JVM's own notice. A script run does not buffer that way. Measured on
    2026-09-01 on `windows-latest` (SQLcl 26.2.2.0, python 3.14.7) by
    `tests/tools/windows_script_probe.py`: a script that prints a marker, waits
    three seconds and prints another handed the reader the first marker 2.83s
    after launch and the second at 5.94s, the wait sitting between them. The
    same script on a POSIX pipe read 0.72s and 3.74s, so the buffering the
    interactive session measured belongs to the held-open prompt, not to the
    pipe. The module docstring says why a pseudo console is not the
    alternative even so.

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
    collected: list[str] = []
    callback_errors: list[BaseException] = []

    def pump() -> None:
        if process.stdout is None:
            return
        try:
            for raw in process.stdout:
                # `rstrip("\r\n")` and not a bare `rstrip()`: a transcript
                # line's trailing spaces belong to it, and every parser
                # downstream reads the same string the pty transport produces.
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                collected.append(line)
                on_line(line)
        except BaseException as error:
            callback_errors.append(error)
            with contextlib.suppress(Exception):
                process.kill()
        finally:
            with contextlib.suppress(Exception):
                process.stdout.close()

    reader: threading.Thread | None = None
    try:
        # Closed at once, never inherited: ADT #188's invariant through a pipe
        # rather than a device, and the reason a failed CONNECT ends on EOF.
        if process.stdin is not None:
            process.stdin.close()
        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as expired:
        _kill_and_reap(process)
        if reader is not None:
            reader.join(timeout=5.0)
        raise _timed_out(timeout_seconds, collected, scrub) from expired
    except BaseException:
        _kill_and_reap(process)
        if reader is not None:
            reader.join(timeout=5.0)
        raise
    # The child is gone, so the pipe is at EOF and the pump ends on its own; the
    # bound is a backstop, never the normal path.
    _require_reader(reader).join(timeout=5.0)
    if callback_errors:
        raise callback_errors[0]
    return "\n".join(collected), process.returncode


def _kill_and_reap(process: subprocess.Popen[bytes]) -> None:
    """Best-effort teardown used by every exceptional streaming path."""
    with contextlib.suppress(Exception):
        process.kill()
    with contextlib.suppress(Exception):
        process.wait()


def open_stream(
    command: Sequence[str],
    root: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
    on_line: Callable[[str], None],
    scrub: Callable[[str], str] = keep_every_line,
) -> tuple[str, int]:
    """The live-reader transport this platform has.

    Keyed on `os.name`, the same way `shared/sqlcl_console.open_console` picks
    its console, and for the same reason: a missing `pty` on Windows is a
    platform fact to route around, never an import to catch.
    """
    if os.name == "nt":
        return stream_on_pipe(command, root, environment, timeout_seconds, on_line, scrub)
    return stream_on_pty(command, root, environment, timeout_seconds, on_line, scrub)
