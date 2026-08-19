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
"""

from __future__ import annotations

import contextlib
import os
import pty
import re
import subprocess
import termios
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from adt_ai.shared.connections import ConnectFailedError, Connection
from adt_ai.shared.oracle_session import DDL_LOCK_TIMEOUT_STATEMENT
from adt_ai.shared.sqlcl_connect import sqlcl_connect
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
        self._process: subprocess.Popen[bytes] | None = None
        self._master: int | None = None
        self._writer: Any = None
        self._lines: Queue[str | None] = Queue()
        self._counter = 0
        self._secrets: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._process is not None:
            return
        plan = sqlcl_connect(
            self.connection,
            startup_sql       = self.startup_sql,
            project_root      = self.project_root,
            named_connections = self.config.get("sqlcl_named_connections") is not False,
        )
        self._secrets = _connect_secrets(plan.script)
        self._spawn()
        self._await_prompt()
        # The connect line is the ONE exchange that can carry a credential, so it
        # is the one whose reply is scrubbed. Scrubbing every reply looks safer and
        # is worse: the password is a database password, so it can equal a schema
        # name, a workspace name or any other value a query legitimately returns,
        # and a four character one measured here turned `export_apex -reveal`'s
        # workspace column into `***`. The same short-credential trap ADT #397 hit
        # from the other side.
        connected = self._exchange(
            plan.script, timeout_seconds=START_TIMEOUT_SECONDS, scrub=True
        )
        if _ran_without_a_session(connected):
            # A failure to connect is a CONNECTION failure, not a configuration
            # one: §Console output contract routes those to the shared database
            # banner with its wallet and credential advice. The class is what
            # carries that, so this raise picks it deliberately (ADT #407). As a
            # plain `ConnectionError` an `SP2-0640` printed under `CONFIGURATION
            # NOT FOUND:` and told the reader to run from a project folder, with
            # their connection file found, read and sitting right there.
            raise ConnectFailedError(
                "SQLcl did not connect: "
                + (connected.strip().splitlines() or ["no output"])[-1]
                + ". A named connection resolves only from SQLcl's own store; "
                "register it once with a run that has the password, or give the "
                "connection file one."
            )
        self._exchange(_SESSION_PRELUDE, timeout_seconds=START_TIMEOUT_SECONDS)

    def close(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            self._writer.write(b"exit;\n")
            self._writer.flush()
            process.wait(timeout=15)
        except Exception:
            process.kill()
        finally:
            if self._writer is not None:
                with contextlib.suppress(Exception):
                    self._writer.close()
            if self._master is not None:
                with contextlib.suppress(OSError):
                    os.close(self._master)
                self._master = None

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
        environment = dict(_sqlcl_environment())
        # See the module docstring: without this SQLcl blocks on a terminal
        # negotiation nothing is going to answer.
        environment["TERM"] = "dumb"
        environment["JAVA_TOOL_OPTIONS"] = (
            environment.get("JAVA_TOOL_OPTIONS", "") + " -Dorg.jline.terminal.dumb=true"
        ).strip()
        master, slave = pty.openpty()
        attributes = termios.tcgetattr(slave)
        attributes[3] &= ~termios.ECHO
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        self._process = subprocess.Popen(
            list(self.launcher),
            stdin  = slave,
            stdout = slave,
            stderr = slave,
            env    = environment,
        )
        os.close(slave)
        self._master = master
        self._writer = os.fdopen(os.dup(master), "wb", buffering=0)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self._master is not None
        buffer = b""
        while True:
            try:
                chunk = os.read(self._master, 65536)
            except OSError:
                chunk = b""
            if not chunk:
                self._lines.put(None)
                return
            buffer += chunk
            *complete, buffer = buffer.split(b"\n")
            for line in complete:
                self._lines.put(line.decode("utf-8", "replace").rstrip("\r"))
            if buffer.rstrip().endswith(_PROMPT.encode()):
                self._lines.put(buffer.decode("utf-8", "replace"))
                buffer = b""

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
        self._writer.write((payload + "\n").encode("utf-8"))
        self._writer.write(f"prompt {marker}\n".encode())
        self._writer.flush()

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
                self._process = None
                raise SqlclSessionError("SQLcl exited mid-statement")
            if marker in line:
                break
            collected.append(line)
        text = "\n".join(_clean(collected))
        return _scrub_secrets(text, self._secrets) if scrub else text


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
