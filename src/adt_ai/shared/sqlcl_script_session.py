"""SQLcl driven one script at a time, which is what Windows can do (ADT #449).

`sqlcl_session.SqlclSession` holds one SQLcl process open and talks to it. That
needs a prompt, and Windows does not give one. Measured 2026-08-22 on
`windows-latest`, SQLcl 26.2.1.0, pywinpty 3.0.5, across six combinations of
terminal type, `-S` on the launcher and line ending: **not one reached a `SQL>`
prompt**, and in every one SQLcl echoed both written lines and executed neither.
The same run drove `sql -S /nolog @script.sql` on that machine to exit `0` with
its statement run, so the install is fine and the interactive session is not.

So Windows takes the shape that is measured to work there, which is also the
shape `shared/sqlcl_stream.py` already takes for a streamed run and the one
`sqlcl_request` has always taken: one script per request, over a pipe.

**What it costs is the session, and that is stated rather than absorbed.** A
fresh process is a fresh database session, so this transport re-sends the connect
and the prelude with every request and cannot carry anything else across two of
them. The two flows that need it, `export_apex`'s collection and `ut
-coverage`'s profiler, refuse rather than return an empty answer, through
`shared/session_scope.require_database_session`. `holds_a_session` is how they
tell, and it is a class attribute on both transports so the question has an
answer before anything is opened.

Two smaller things this shape gets for free, both from `#457`: the child's stdin
is an immediately closed pipe rather than `NUL`, which is what SQLcl's console
builder can introspect on Windows, and an exit-0 run whose whole transcript is a
startup stack trace raises instead of being handed back as data.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from adt_ai.shared.connections import ConnectFailedError, Connection
from adt_ai.shared.sqlcl_connect import sqlcl_connect
from adt_ai.shared.sqlcl_script import (
    _ran_without_a_session,
    run_sqlcl_script,
)
from adt_ai.shared.sqlcl_session import (
    _SESSION_PRELUDE,
    STATEMENT_TIMEOUT_SECONDS,
    SqlclSession,
    SqlclSessionError,
    error_in,
)

# The line that separates the session's own setup from the body's answer. It is a
# `prompt` command rather than a comment because SQLcl prints it, and a comment
# it would swallow: the reader needs the boundary IN the transcript.
BODY_MARKER = "<<<ADT-SQLCL-BODY>>>"


class ScriptSession:
    """The `SqlclSession` contract, served one SQLcl script per request.

    Deliberately not a subclass. It shares no mechanism with the driven session,
    only an interface, and inheriting would offer `_exchange`, `_pump` and a
    console to a class that has none of them.
    """

    holds_a_session = False

    def __init__(
        self,
        connection   : Connection,
        *,
        project_root : Path | None = None,
        startup_sql  : str | None = None,
        config       : Any = None,
    ) -> None:
        self.connection = connection
        self.project_root = project_root
        self.startup_sql = startup_sql
        self.config = config or {}
        self._plan: Any = None
        self._marker = BODY_MARKER
        # Held as an attribute so a test can replace the runner without patching
        # a module global every other test in the file also reads.
        self._runner = run_sqlcl_script

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Resolve the connect plan. No process is opened until a request needs one."""
        if self._plan is not None:
            return
        self._plan = sqlcl_connect(
            self.connection,
            startup_sql       = self.startup_sql,
            project_root      = self.project_root,
            named_connections = self.config.get("sqlcl_named_connections") is not False,
        )

    def close(self) -> None:
        """Nothing is held open, so there is nothing to end.

        Defined rather than omitted because every caller closes a gateway, and a
        transport that raises on the way out would turn a clean run into a
        failure at the last line.
        """

    def __enter__(self) -> ScriptSession:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # -- the request/response protocol -------------------------------------

    def run(self, body: str, *, timeout_seconds: float | None = None) -> str:
        """Run `body` in its own SQLcl process and return only what it printed."""
        self.start()
        output = self._runner(
            self._script(body),
            self._temp_root(),
            project_root    = self.project_root,
            timeout_seconds = timeout_seconds or STATEMENT_TIMEOUT_SECONDS,
        )
        return self._answer(output)

    # -- internals ---------------------------------------------------------

    def _script(self, body: str) -> str:
        """Connect, set the session up, mark the boundary, then the caller's body.

        Every request carries all four, because every request is a new session.
        The prelude is the same constant the driven transport sends once, so the
        two cannot drift into pinning different NLS settings.
        """
        return "\n".join(
            (
                self._plan.script,
                _SESSION_PRELUDE,
                f"prompt {self._marker}",
                body.rstrip(),
                "",
            )
        )

    def _temp_root(self) -> Path:
        root = self.project_root or Path.cwd()
        return Path(root) / "config" / "temp"

    def _answer(self, output: str) -> str:
        """Everything after the boundary, or a failure explaining why there is none.

        A missing marker means the script never reached the body. Returning `""`
        there is the defect this transport exists to avoid: a caller would write
        the emptiness into an export as though it were the data.
        """
        _, separator, tail = output.partition(self._marker)
        if separator:
            return tail.lstrip("\n").rstrip()
        if _ran_without_a_session(output):
            raise ConnectFailedError(
                "SQLcl did not connect: "
                + (output.strip().splitlines() or ["no output"])[-1]
                + ". A named connection resolves only from SQLcl's own store; "
                "register it once with a run that has the password, or give the "
                "connection file one."
            )
        reported = error_in(output)
        raise SqlclSessionError(
            "SQLcl produced no answer for this statement. "
            + (
                f"It reported: {reported}"
                if reported
                else "Its whole transcript was: " + (output.strip() or "(nothing)")
            )
        )


def open_session(
    connection   : Connection,
    *,
    project_root : Path | None = None,
    startup_sql  : str | None = None,
    config       : Any = None,
) -> Any:
    """The transport this platform can drive.

    Keyed on `os.name`, exactly as `sqlcl_console.open_console` and
    `sqlcl_stream.open_stream` are, so all three answer the platform question the
    same way and a reader who has met one has met all of them.
    """
    transport = ScriptSession if os.name == "nt" else SqlclSession
    return transport(
        connection,
        project_root = project_root,
        startup_sql  = startup_sql,
        config       = config,
    )
