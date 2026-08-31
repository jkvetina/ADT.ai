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
import subprocess
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
    """

    echoes = False

    def __init__(self, launcher: tuple[str, ...], environment: dict[str, str]) -> None:
        self.launcher = launcher
        self.environment = _with_dumb_terminal(environment)
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
            attributes[3] &= ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, attributes)
            process = subprocess.Popen(
                list(self.launcher),
                stdin  = slave,
                stdout = slave,
                stderr = slave,
                env    = self.environment,
            )
            writer_descriptor = os.dup(master)
            try:
                writer = os.fdopen(writer_descriptor, "wb", buffering=0)
            except BaseException:
                os.close(writer_descriptor)
                raise
        except BaseException:
            if process is not None:
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
        assert self._master is not None
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

    def wait(self, timeout: float) -> None:
        assert self._process is not None
        self._process.wait(timeout=timeout)

    def kill(self) -> None:
        if self._process is not None:
            self._process.kill()

    def close(self) -> None:
        if self._writer is not None:
            with contextlib.suppress(Exception):
                self._writer.close()
            self._writer = None
        if self._master is not None:
            with contextlib.suppress(OSError):
                os.close(self._master)
            self._master = None
        if self._process is not None:
            if self._process.poll() is None:
                with contextlib.suppress(Exception):
                    self._process.kill()
            with contextlib.suppress(Exception):
                self._process.wait()
            self._process = None


class ConPtyConsole:
    """SQLcl on a Windows pseudo console, through pywinpty.

    `pywinpty` hands back `str` where the POSIX side hands back bytes, so this
    encodes on the way out and decodes on the way in: the session above reads
    bytes on both platforms, which is what keeps the POSIX reader untouched.
    """

    echoes = True

    def __init__(self, launcher: tuple[str, ...], environment: dict[str, str]) -> None:
        self.launcher = launcher
        # The same dumb terminal the pty asks for. Measured 2026-08-22: the pair
        # is not what withholds the prompt, and without it SQLcl paints a line
        # editor widget over the session. See the module docstring.
        self.environment = _with_dumb_terminal(environment)
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

    def wait(self, timeout: float) -> None:
        self._child.wait()

    def kill(self) -> None:
        if self._child is not None:
            with contextlib.suppress(Exception):
                self._child.terminate(force=True)

    def close(self) -> None:
        if self._child is not None:
            with contextlib.suppress(Exception):
                self._child.close()
            self._child = None


def open_console(launcher: tuple[str, ...], environment: dict[str, str]) -> Any:
    """The console this platform has, opened and ready to read.

    Keyed on `os.name` rather than on whether an import happens to succeed: a
    missing `pywinpty` on Windows is a message telling the user to install it,
    never a silent fall back onto a pty that cannot exist there.
    """
    console = ConPtyConsole(launcher, environment) if os.name == "nt" else PtyConsole(
        launcher, environment
    )
    console.open()
    return console
