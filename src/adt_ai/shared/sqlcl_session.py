"""One SQLcl process, driven as a database session (ADT #396).

`run_sqlcl_script` spawns a process per request, which is right for what it
serves: a deploy or a `DIFF` is a script with its own `WHENEVER SQLERROR EXIT`
and it is supposed to end. A gateway is not that. python-oracledb holds one
session for a whole command, and two ADT flows lean on it: `export_apex`
populates an `APEX_COLLECTION` in one call and reads `apex_collections` in the
next, and `ut -coverage` starts the profiler, runs the tests, and stops it across
three. A process per call gives each of those a fresh session, so the collection
is gone and the profiler recorded nothing.

Hence a driven session. Four mechanics decide whether that works at all, and each
one was measured on 2026-08-19 against SQLcl 26.2 rather than reasoned about:

* **The child must be on a pty.** The JVM block-buffers stdout when it is not a
  terminal, so over a plain pipe the sentinel sits in the child's buffer and the
  reader waits forever. Measured: 60 seconds, not one byte.
* **`TERM` must be `dumb`.** SQLcl drives JLine, which on a capable terminal
  writes a cursor-position query (`ESC[6n`) at startup and BLOCKS until something
  answers it. On a dumb terminal it skips the negotiation and prints its prompt.
* **The prompt carries no newline.** A reader that splits output on newlines never
  emits `SQL>`, so waiting for the prompt hangs on output that already arrived.
  The tail is flushed when it ends in a prompt.
* **Terminal echo must be off**, or every statement written comes straight back
  as output and lands in the middle of the reply it precedes.

Costs, measured on the same run: 3.02 s to start and connect, then 52 ms per
round trip. An error does not end the session, so a failed statement is a failed
call and not a dead gateway.

**The terminal itself moved out to `sqlcl_console.py` (ADT #449)**, because
Windows has neither `pty` nor `termios` and importing them here took every
command down on that platform, `--help` included. Two of the four mechanics
above are POSIX readings rather than universal ones, measured on 2026-08-21
against `windows-latest`, SQLcl 26.2.1.0 and pywinpty 3.0.5:

* **Echo is not always off.** A pseudo console echoes what the parent writes, so
  `_exchange` can no longer end on a plain `marker in line`: that matched the
  echo of its own `prompt <<<...>>>` and returned an empty string as the answer.
  `_is_sentinel` tells the two apart by shape, and a console that echoes says so,
  which is what lets the echoed lines be dropped without touching the pty path.
* **A terminal is not always a plain stream.** A pseudo console renders the
  session, cursor addressing and all, so the reader takes the escapes off through
  the console's own `clean`, which is the identity on a pty.

The pty path is unchanged and its 2026-08-19 numbers stand. What is NOT settled
is whether SQLcl reaches a prompt over ConPTY at all: on the 2026-08-21 run it
did not inside ninety seconds, with `TERM=dumb` set. `sqlcl_console` explains why
it now leaves that pair alone on Windows, and says plainly that the step is
reasoned rather than measured.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from adt_ai.shared.connections import ConnectFailedError, Connection
from adt_ai.shared.oracle_session import DDL_LOCK_TIMEOUT_STATEMENT
from adt_ai.shared.sqlcl_connect import sqlcl_connect
from adt_ai.shared.sqlcl_console import open_console
from adt_ai.shared.sqlcl_script import (
    _connect_secrets,
    _ran_without_a_session,
    _scrub_secrets,
    _sqlcl_environment,
)

SQLCL_LAUNCHER = ("sql", "-S", "/nolog")

# How long to wait for SQLcl to reach its first prompt. A cold JVM plus a connect
# measured at 3 s; the budget is generous because failing here means the whole
# command fails, and a slow laptop is not an error.
START_TIMEOUT_SECONDS = 90.0

# Default per-statement budget. Deliberately the same order as the python gateway's
# `query_timeout_seconds`, so a long-running query is not cut off by the transport.
STATEMENT_TIMEOUT_SECONDS = 1_200.0

# How long a shutdown waits for the reader thread to notice its console is gone.
# The console has already been killed by then, so `read` is returning rather than
# blocking and this is measured in milliseconds; the budget only exists so a
# console that somehow never returns cannot wedge the shutdown that killed it.
PUMP_JOIN_SECONDS = 5.0

_SENTINEL = "<<<ADT-SQLCL-{n}>>>"
_PROMPT = "SQL>"

# `SET LONG` is the knob that truncates a large value, measured: at 80 a 30000
# character CLOB came back 80 characters long, while `SET LONGCHUNKSIZE 80` with a
# large `LONG` returned all 30000. SQLcl prints a Java memory warning above a
# certain size, which is why this is generous rather than enormous, and why the
# reader tolerates that warning appearing in the stream.
SET_LONG = 200_000_000

# NLS is pinned so a value's text form is a property of ADT rather than of the
# database's defaults. python-oracledb hands back native types and never sees
# these; SQLcl hands back text, so without pinning a timestamp arrives with
# whatever decimal separator the session happens to carry (measured: a comma).
_SESSION_PRELUDE = f"""
set feedback off
set heading off
set pagesize 0
set linesize 32767
set long {SET_LONG}
set longchunksize 32767
set define off
set sqlblanklines on
alter session set nls_date_format = 'YYYY-MM-DD HH24:MI:SS';
alter session set nls_timestamp_format = 'YYYY-MM-DD HH24:MI:SS.FF6';
alter session set nls_timestamp_tz_format = 'YYYY-MM-DD HH24:MI:SS.FF6 TZR';
alter session set nls_numeric_characters = '.,';
{DDL_LOCK_TIMEOUT_STATEMENT};
"""

# SQLcl's own noise, dropped before anything tries to read a reply. The memory
# warning is what a large `SET LONG` earns, and the JVM prints its own notice when
# JAVA_TOOL_OPTIONS is set in the environment.
_NOISE = re.compile(
    r"^(Picked up JAVA_TOOL_OPTIONS|Warning: This LONG setting|It is recommended to reduce)"
)

# An Oracle error in SQLcl's own report block. Matched on the report markers rather
# than on the code alone: `recompile` and `ut` both SELECT error text containing
# `ORA-` and `PLS-` codes, so a bare code search would read a successful query's
# own rows as a failure.
_ERROR_REPORT = re.compile(
    r"^(Error starting at line|Error report -|Error at Command Line|SP2-\d+|USAGE:)"
)
_ERROR_CODE = re.compile(r"\b(ORA-\d{5}|PLS-\d{5}|SP2-\d{4})\b")


class SqlclSessionError(RuntimeError):
    """Raised when the driven SQLcl session cannot serve a statement."""


class SqlclSession:
    """A SQLcl process kept open for the lifetime of one command."""

    # Two calls reach one database session here, which is the whole reason this
    # class exists. `shared/session_scope` reads it, and `ScriptSession` answers
    # the same question with False (ADT #449).
    holds_a_session = True

    def __init__(
        self,
        connection: Connection,
        *,
        project_root: Path | None = None,
        startup_sql: str | None = None,
        config: Any = None,
        launcher: tuple[str, ...] = SQLCL_LAUNCHER,
    ) -> None:
        self.connection = connection
        self.project_root = project_root
        self.startup_sql = startup_sql
        self.config = config or {}
        self.launcher = launcher
        self._console: Any = None
        self._lines: Queue[str | None] = Queue()
        self._pump_thread: threading.Thread | None = None
        self._counter = 0
        self._secrets: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._console is not None:
            return
        plan = sqlcl_connect(
            self.connection,
            startup_sql       = self.startup_sql,
            project_root      = self.project_root,
            named_connections = self.config.get("sqlcl_named_connections") is not False,
        )
        self._secrets = _connect_secrets(plan.script)
        # A retry after failed startup must not consume the EOF marker or prompt
        # fragments left by the previous pump thread.
        self._lines = Queue()
        try:
            self._spawn()
            self._await_prompt()
            # The connect line is the ONE exchange that can carry a credential,
            # so it is the one whose reply is scrubbed. Scrubbing every reply
            # looks safer and is worse: the password can equal a schema or
            # workspace value a query legitimately returns (ADT #397).
            connected = self._exchange(
                plan.script, timeout_seconds=START_TIMEOUT_SECONDS, scrub=True
            )
            if _ran_without_a_session(connected):
                # A failure to connect is a CONNECTION failure, not a
                # configuration one; the class selects the shared database
                # banner and its credential advice (ADT #407).
                raise ConnectFailedError(
                    "SQLcl did not connect: "
                    + (connected.strip().splitlines() or ["no output"])[-1]
                    + ". A named connection resolves only from SQLcl's own store; "
                    "register it once with a run that has the password, or give the "
                    "connection file one."
                )
            self._exchange(_SESSION_PRELUDE, timeout_seconds=START_TIMEOUT_SECONDS)
        except BaseException:
            self._abort()
            raise

    def close(self) -> None:
        console, self._console = self._console, None
        if console is None:
            return
        try:
            console.write(b"exit;\n")
            console.wait(15)
        except Exception:
            console.kill()
            with contextlib.suppress(Exception):
                console.wait(5)
        finally:
            console.close()
            self._join_pump()

    def _abort(self) -> None:
        """Kill and reap a console that cannot become a usable session."""
        console, self._console = self._console, None
        if console is None:
            return
        with contextlib.suppress(Exception):
            console.kill()
        with contextlib.suppress(Exception):
            console.wait(5)
        with contextlib.suppress(Exception):
            console.close()
        self._join_pump()

    def _join_pump(self) -> None:
        """Reap the reader thread, after the console it reads has been closed.

        A console that is killed and reaped leaves its pump behind unless
        somebody waits for it (ADT #670), and `_abort` exists precisely so the
        caller can try `start` again: one leaked thread per retry, each still
        holding a handle on a console the session has already forgotten.

        Called last on both shutdown paths, because the join is only short when
        the console is already closed -- that is what makes the blocked `read`
        return. The wait is bounded for the same reason every other wait here
        is: the thread is a daemon, so a console that never returns costs a
        leaked reader at exit rather than a command that will not end.
        """
        pump, self._pump_thread = self._pump_thread, None
        if pump is None:
            return
        pump.join(timeout=PUMP_JOIN_SECONDS)

    def __enter__(self) -> SqlclSession:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- the request/response protocol -------------------------------------

    def run(self, body: str, *, timeout_seconds: float | None = None) -> str:
        """Send ``body`` and return everything SQLcl printed for it."""
        self.start()
        return self._exchange(
            body,
            timeout_seconds = timeout_seconds or STATEMENT_TIMEOUT_SECONDS,
        )

    def _spawn(self) -> None:
        # The terminal itself lives in `sqlcl_console`, which owns the one thing
        # that differs per platform (ADT #449). Everything below this line is the
        # same on a pty and on a pseudo console.
        self._console = open_console(self.launcher, dict(_sqlcl_environment()))
        # Kept on the instance so both shutdown paths can reap it (ADT #670).
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def _pump(self) -> None:
        if self._console is None:
            raise SqlclSessionError("SQLcl console is not open")
        console = self._console
        # Both bound ONCE, so this reader keeps writing to the queue it was
        # started for. `start` hands a retry a fresh `Queue`, and a reader that
        # re-read `self._lines` at every put would drop its EOF marker into that
        # one, telling a console spawned seconds ago that it had already exited
        # (ADT #670). `_join_pump` should mean no reader is ever still running
        # by then; this is what makes the failure impossible rather than
        # unlikely.
        lines = self._lines
        buffer = b""
        try:
            while True:
                chunk = console.read(65536)
                if not chunk:
                    lines.put(None)
                    return
                buffer += chunk
                *complete, buffer = buffer.split(b"\n")
                for line in complete:
                    # `clean` is the identity on a pty and takes the rendering
                    # escapes off a pseudo console (ADT #449).
                    lines.put(
                        console.clean(line.decode("utf-8", "replace")).rstrip("\r")
                    )
                # The prompt carries no newline, so flush a tail ending in one.
                tail = console.clean(buffer.decode("utf-8", "replace"))
                if tail.rstrip().endswith(_PROMPT):
                    lines.put(tail)
                    buffer = b""
        except BaseException:
            # A dead reader must wake startup/the active exchange. Without the
            # EOF marker the foreground waits until its full 90/1200s deadline.
            lines.put(None)

    def _await_prompt(self, timeout: float = START_TIMEOUT_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self._lines.get(timeout=1.0)
            except Empty:
                continue
            if line is None:
                raise SqlclSessionError("SQLcl exited before it was ready")
            if _PROMPT in line:
                return
        raise SqlclSessionError(
            f"SQLcl did not reach a prompt within {timeout:g} seconds"
        )

    def _exchange(self, body: str, *, timeout_seconds: float, scrub: bool = False) -> str:
        self._counter += 1
        marker = _SENTINEL.format(n=self._counter)
        payload = body.rstrip()
        written = f"{payload}\nprompt {marker}"
        self._console.write((payload + "\n").encode("utf-8"))
        self._console.write(f"prompt {marker}\n".encode())

        # A pseudo console echoes every line the parent writes, so on Windows the
        # statement comes straight back before SQLcl has answered anything. The
        # set is what we wrote, and it is consulted only when the console says it
        # echoes, which leaves the pty path reading exactly what it read before.
        echoed = {line.strip() for line in written.splitlines() if line.strip()}
        drops_echo = bool(getattr(self._console, "echoes", False))

        collected: list[str] = []
        deadline = time.monotonic() + timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise SqlclSessionError(
                    f"SQLcl did not answer within {timeout_seconds:g} seconds"
                )
            try:
                line = self._lines.get(timeout=min(1.0, remaining))
            except Empty:
                continue
            if line is None:
                self._abort()
                raise SqlclSessionError("SQLcl exited mid-statement")
            if _is_sentinel(line, marker):
                break
            if drops_echo and line.strip() in echoed:
                continue
            collected.append(line)
        text = "\n".join(_clean(collected))
        return _scrub_secrets(text, self._secrets) if scrub else text


def _is_sentinel(line: str, marker: str) -> bool:
    """True for the marker SQLcl printed, never for the `prompt` line we wrote.

    This used to be a plain `marker in line`, which is correct exactly as long as
    the terminal does not echo. Measured on 2026-08-21, a Windows pseudo console
    does: the echoed `prompt <<<ADT-SQLCL-n>>>` matched first, the exchange ended
    before SQLcl had answered, and the caller got an empty string as its reply.

    The two are told apart by shape rather than by timing. What we write is a
    `prompt` COMMAND; what SQLcl writes back is the bare marker. The `SQL>`
    fragments come off first because the prompt carries no newline, so a marker
    can arrive with one glued to its front.
    """
    text = line.strip()
    while text.startswith(_PROMPT):
        text = text[len(_PROMPT):].strip()
    return marker in text and not text.lower().startswith("prompt")


def _clean(lines: list[str]) -> list[str]:
    """Drop the prompt fragments and SQLcl's own startup noise."""
    cleaned = []
    for line in lines:
        text = line
        while text.lstrip().startswith(_PROMPT):
            text = text.lstrip()[len(_PROMPT):]
        if _NOISE.match(text.strip()):
            continue
        cleaned.append(text)
    return cleaned


def error_in(output: str) -> str | None:
    """The Oracle error SQLcl reported, or ``None``.

    Keyed on SQLcl's own report markers rather than on a bare code search: a
    successful `recompile` or `ut` query returns rows whose text carries `ORA-`
    and `PLS-` codes, and reading those as a failure would break the two commands
    most likely to be run against a broken schema.
    """
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if not _ERROR_REPORT.match(line.strip()):
            continue
        for candidate in lines[index:]:
            found = _ERROR_CODE.search(candidate)
            if found:
                return candidate.strip()
        return line.strip()
    return None
