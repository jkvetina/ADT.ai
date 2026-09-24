"""The terminal a driven SQLcl process sits on, whichever one the platform has (ADT #449).

`sqlcl_session.SqlclSession` needs SQLcl on a terminal rather than on a pipe,
because the JVM block-buffers stdout when it is not talking to one. On POSIX that
terminal is a `pty` with `termios` echo turned off, measured on 2026-08-19 against
SQLcl 26.2. Windows has neither module, and a `sqlcl_only` customer reported the
import taking the whole CLI down with it.

This module is the seam. Everything above it, the sentinel protocol, `_clean`,
`error_in` and the session prelude, is platform-free and reads bytes; each console
below it owns exactly one platform's terminal.

Measured on 2026-08-21, `windows-latest`, SQLcl 26.2.1.0, python 3.13.15,
pywinpty 3.0.5, with no database in play. Three findings shaped what is here, and
each one contradicted the POSIX habit it replaced:

* **A plain pipe is silent on Windows, not merely slow.** Ninety seconds produced
  the JVM's own `Picked up JAVA_TOOL_OPTIONS` notice and nothing else, no prompt
  and no answer. So a pseudo console is genuinely required rather than assumed,
  and `pywinpty` is a real dependency rather than a convenience.
* **A pseudo console ECHOES what the parent writes.** A POSIX pty does not,
  because `termios` is asked to stop it, and `_exchange` leans on that when it
  matches its sentinel with a plain substring test. On Windows that test matched
  the echo of its own `prompt <<<ADT-SQLCL-n>>>` and would have handed back an
  empty string as the answer. Hence `echoes`, and the shape-aware matching the
  session does with it.
* **A pseudo console RENDERS the session.** The stream carries cursor addressing
  (`ESC[2;1H`), private mode sets (`ESC[?9001h`) and window reports (`ESC[1t`),
  none of which a pty puts in front of a reader. Hence `clean`.

The fourth question, whether SQLcl reaches a `SQL>` prompt here at all, was
measured on 2026-08-22 across six settings, same runner and same versions, and
the answer is **no, under every one of them**. `tests/tools/
windows_console_probe.py` varied the three settings this module had been
choosing without knowing: the dumb terminal, the `-S` on the launcher, and the
line ending sent after each statement. No combination produced a prompt, and in
every one SQLcl echoed both written lines and executed neither.

* **The install is not the problem, and that is a control rather than an
  inference.** The same run drove `sql -S /nolog @script.sql` on the same
  machine: exit `0`, the script's own `prompt` line came back, and the statement
  answered `SP2-0640: Not connected`, which is `/nolog` behaving correctly. So
  SQLcl runs here; what it does not do is serve a driven interactive session.
* **The dumb terminal is not what withholds the prompt, and taking it off costs
  something.** Without it SQLcl starts its full JLine line editor and paints a
  `viins ... NOLOG` status widget over the session, with a scroll region and
  save/restore cursor around every redraw. No prompt either way, so the console
  below now asks for the dumb terminal exactly as the POSIX one does: same
  outcome, less for a reader to parse around. Until 2026-08-22 it deliberately
  did the opposite, on a reasoned argument that the measurement did not support.
* **A capable terminal uses escapes the first pass never saw.** `ESC 7` and
  `ESC 8`, save and restore cursor, are two-character escapes whose final byte
  is a digit, and the pattern written against the 2026-08-21 transcript walked
  past both. `clean` covers every escape form now, because one surviving byte
  reaches a caller as part of an answer.

**So nothing drives a console on Windows, and this module is POSIX-only in
practice.** `ConPtyConsole` stays, correct as far as anything has measured it and
ready for the day a Windows SQLcl prompts, but no shipped path opens it:
`sqlcl_script_session.open_session` sends Windows to `ScriptSession` instead, one
SQLcl script per request, which is the shape that machine actually runs.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

# A pseudo console renders rather than pipes, so the escapes come off before any
# reader matches on text. Three branches, tried in order: CSI, OSC, then every
# other escape sequence. That last branch reads ECMA-48's own shape, intermediate
# bytes then one final byte, rather than a list of the finals seen so far, which
# is what let `ESC 7` and `ESC 8` through until 2026-08-22 measured them.
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[ -/]*[0-~]")

# SQLcl drives JLine, which on a capable terminal writes a cursor position query
# at startup and blocks until something answers it. A bare pty never will, so the
# POSIX console asks for a dumb terminal. The Windows console asks for the same
# one, for a different and measured reason: see the module docstring.
DUMB_TERMINAL = {
    "TERM"             : "dumb",
    "JAVA_TOOL_OPTIONS": "-Dorg.jline.terminal.dumb=true",
}


class SqlclConsoleStateError(RuntimeError):
    """A console operation was attempted before its platform handle existed."""


def _with_dumb_terminal(environment: dict[str, str]) -> dict[str, str]:
    environment = dict(environment)
    environment["TERM"] = DUMB_TERMINAL["TERM"]
    environment["JAVA_TOOL_OPTIONS"] = (
        environment.get("JAVA_TOOL_OPTIONS", "") + " " + DUMB_TERMINAL["JAVA_TOOL_OPTIONS"]
    ).strip()
    return environment


class PtyConsole:
    """SQLcl on a POSIX pty, exactly as measured on 2026-08-19.

    `pty` and `termios` are imported inside `open` rather than at module scope.
    `cli/gateways.py` names `SqlclGateway` before any config is read, so this
    module loads for every command while only a `sqlcl_only` project ever opens
    a terminal, and at module scope the pair took the whole CLI down on Windows
    (ADT #449).

    **The child is a process group, and it ends as one (ADT #923).** `sql` is a
    bash script that starts `java` as its child rather than exec'ing it, so the
    launcher is spawned into a session of its own and `kill` signals that whole
    group. Signalling the launcher alone left the JVM running with its database
    session and the pty slave, and on macOS that wedged `close` as well: closing
    a pty master waits for a read still in flight on it, and the session's reader
    only returns once nothing holds the slave. Measured 2026-09-23 on
    `sql -S /nolog`, see `kill`. On macOS the pty also becomes that session's
    controlling terminal (`ps` shows `Ss+` on a plain `/bin/sh` spawned this
    way), so the JVM is hung up when its launcher dies, however it dies. That
    is also why `open` turns the terminal's signal characters off.
    """

    echoes = False

    def __init__(
        self,
        launcher: tuple[str, ...],
        environment: dict[str, str],
        cwd: Path | None = None,
    ) -> None:
        self.launcher = launcher
        self.environment = _with_dumb_terminal(environment)
        # SQLcl resolves `@` scripts and a `SPOOL "./..."` path against its own
        # working directory, and a driven process is spawned once and keeps the
        # one it was given (ADT #760). `run_sqlcl_script` passes `cwd=root` per
        # call, so a reusing caller has to restart when the root moves.
        self.cwd = cwd
        self._process: subprocess.Popen[bytes] | None = None
        self._master: int | None = None
        self._writer: Any = None

    def open(self) -> None:
        import pty
        import termios

        master, slave = pty.openpty()
        process: subprocess.Popen[bytes] | None = None
        writer: Any = None
        try:
            attributes = termios.tcgetattr(slave)
            # No echo, and no signals from typed bytes (ADT #923): bash makes this
            # pty SQLcl's controlling terminal, so a ^C inside a statement reached
            # SQLcl as SIGINT and ended it with exit 130, measured 2026-09-23.
            attributes[3] &= ~(termios.ECHO | termios.ISIG)
            termios.tcsetattr(slave, termios.TCSANOW, attributes)
            process = subprocess.Popen(
                list(self.launcher),
                stdin             = slave,
                stdout            = slave,
                stderr            = slave,
                env               = self.environment,
                cwd               = self.cwd,
                start_new_session = True,
            )
            writer_descriptor = os.dup(master)
            try:
                writer = os.fdopen(writer_descriptor, "wb", buffering=0)
            except BaseException:
                os.close(writer_descriptor)
                raise
        except BaseException:
            if process is not None:
                # The whole group, so a JVM the launcher already started goes
                # too, then the launcher by its own pid as this always did.
                with contextlib.suppress(Exception):
                    os.killpg(process.pid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    process.kill()
                with contextlib.suppress(Exception):
                    process.wait()
            with contextlib.suppress(OSError):
                os.close(master)
            raise
        finally:
            with contextlib.suppress(OSError):
                os.close(slave)
        self._process = process
        self._master = master
        self._writer = writer

    def read(self, size: int) -> bytes:
        if self._master is None:
            raise SqlclConsoleStateError("SQLcl console is not open: no pty master")
        try:
            return os.read(self._master, size)
        except OSError:
            return b""

    def write(self, data: bytes) -> None:
        self._writer.write(data)
        self._writer.flush()

    def clean(self, text: str) -> str:
        # A dumb terminal writes no escapes, so there is nothing to take off and
        # the POSIX path stays byte for byte what it was.
        return text

    @property
    def exit_code(self) -> int | None:
        """The child's exit status, or `None` while it is still running.

        A body carrying `WHENEVER SQLERROR EXIT ROLLBACK` ends the process
        instead of answering, and `run_sqlcl_script` classifies exactly that
        failure by its exit code (ADT #760). Reaped here rather than inferred,
        with a short wait because the reader already saw EOF by the time anyone
        asks.
        """
        if self._process is None:
            return None
        with contextlib.suppress(Exception):
            self._process.wait(timeout=5)
        return self._process.returncode

    def wait(self, timeout: float) -> None:
        if self._process is None:
            raise SqlclConsoleStateError("SQLcl console is not open: no child process")
        self._process.wait(timeout=timeout)

    def kill(self) -> None:
        """End the launcher and everything it started, the JVM included.

        One SIGKILL to the group `open` spawned, whose id is the launcher's pid.
        Sent only while the launcher is unreaped: until then it pins that id, and
        once it is reaped the number can belong to some other group. Measured
        2026-09-23 with `sql -S /nolog` at its prompt and a reader in flight. The
        old spawn and `kill` left the JVM running after its launcher was gone,
        and the old `close` was still blocked 5 s later, returning only once the
        JVM was killed by hand. With this one, `kill`, `wait` and `close` took
        0.001 s together and left nothing in the group; `close` alone, with no
        `kill` first, did the same.
        """
        process = self._process
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()

    def close(self) -> None:
        # The child goes before the terminal does. A master closed under a live
        # JVM waits on macOS for the reader still in flight on it, and that read
        # only ends once nothing holds the slave (ADT #923).
        if self._process is not None:
            with contextlib.suppress(Exception):
                self.kill()
            with contextlib.suppress(Exception):
                self._process.wait(timeout=5)
            self._process = None
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
            self._writer = None
        if self._master is not None:
            with contextlib.suppress(OSError):
                os.close(self._master)
            self._master = None


class ConPtyConsole:
    """SQLcl on a Windows pseudo console, through pywinpty.

    `pywinpty` hands back `str` where the POSIX side hands back bytes, so this
    encodes on the way out and decodes on the way in: the session above reads
    bytes on both platforms, which is what keeps the POSIX reader untouched.
    """

    echoes = True

    def __init__(
        self,
        launcher: tuple[str, ...],
        environment: dict[str, str],
        cwd: Path | None = None,
    ) -> None:
        self.launcher = launcher
        # The same dumb terminal the pty asks for. Measured 2026-08-22: the pair
        # is not what withholds the prompt, and without it SQLcl paints a line
        # editor widget over the session. See the module docstring.
        self.environment = _with_dumb_terminal(environment)
        self.cwd = cwd
        self._child: Any = None

    def open(self) -> None:
        try:
            import winpty
        except ImportError as error:  # pragma: no cover - platform dependent
            raise RuntimeError(
                "sqlcl_only on Windows drives SQLcl through a pseudo console, which "
                "needs pywinpty. Install it with: pip install pywinpty"
            ) from error
        # A wide console so SQLcl does not wrap a row the reader then has to
        # rejoin. The height is irrelevant to a reader that never scrolls back.
        self._child = winpty.PtyProcess.spawn(  # pragma: no cover - needs real pywinpty
            list(self.launcher),
            env        = self.environment,
            cwd        = str(self.cwd) if self.cwd is not None else None,
            dimensions = (24, 500),
        )

    def read(self, size: int) -> bytes:
        try:
            chunk = self._child.read(size)
        except EOFError:
            return b""
        except Exception:  # noqa: BLE001 - a closed console reports in its own way
            return b""
        if not chunk:
            return b""
        return chunk.encode("utf-8", "replace") if isinstance(chunk, str) else chunk

    def write(self, data: bytes) -> None:
        self._child.write(data.decode("utf-8", "replace"))

    def clean(self, text: str) -> str:
        return ANSI.sub("", text)

    @property
    def exit_code(self) -> int | None:
        """The child's exit status, or `None` while it is still running."""
        if self._child is None:
            return None
        with contextlib.suppress(Exception):
            if self._child.isalive():
                return None
        return getattr(self._child, "exitstatus", None)

    def wait(self, timeout: float) -> None:
        if self._child is None:
            raise SqlclConsoleStateError("SQLcl console is not open: no ConPTY child")
        deadline = time.monotonic() + timeout
        while self._child.isalive():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(self.launcher, timeout)
            time.sleep(min(0.05, remaining))

    def kill(self) -> None:
        if self._child is not None:
            with contextlib.suppress(Exception):
                self._child.terminate(force=True)

    def close(self) -> None:
        if self._child is not None:
            with contextlib.suppress(Exception):
                self._child.close()
            self._child = None


def open_console(
    launcher: tuple[str, ...],
    environment: dict[str, str],
    cwd: Path | None = None,
) -> Any:
    """The console this platform has, opened and ready to read.

    Keyed on `os.name` rather than on whether an import happens to succeed: a
    missing `pywinpty` on Windows is a message telling the user to install it,
    never a silent fall back onto a pty that cannot exist there.

    `cwd` defaults to the caller's own, which is what `SqlclSession` has always
    inherited; only a transport that resolves relative paths passes one.
    """
    console = ConPtyConsole(launcher, environment, cwd) if os.name == "nt" else PtyConsole(
        launcher, environment, cwd
    )
    console.open()
    return console
