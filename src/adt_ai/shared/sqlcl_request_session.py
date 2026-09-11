"""`sqlcl_request` over a reused SQLcl PROCESS (ADT #760).

`run_sqlcl_script` starts a JVM per script, and a `patch -deploy` pays for that
once per install script. Measured 2026-09-10 against `SANDBOX@FREEPDB1`,
five trivial bodies: **9.76 s** as five processes, against **1.71 s to start one
process plus 0.18 s for all five** through a held-open one. A bare
`sql -S /nolog` start and exit is 1.3 s of every one of those.

**What is reused is the JVM, and deliberately not the database session.** Each
request still carries its own connect block, so the script SQLcl runs is the one
it runs today, statement for statement, and every property that block owns is
untouched: `WHENEVER SQLERROR EXIT FAILURE` around the connect (ADT #188), the
`CONNMGR DELETE` / `-save` registration dance (ADT #148), the injected
`STARTUP.sql` (ADT #177), the wallet's `-cloudconfig` line. A fresh connect also
drops the previous request's session, so no bind variable, `DEFINE` or PL/SQL
package global can travel between two requests, which a shared session would have
let happen on the path client deploys run. That connect measures 0.4 s against
the 1.55 s of JVM it replaces.

`shared/sqlcl_session.py` has driven one SQLcl process since ADT #396, and
`shared/sqlcl_gateway.py` says outright that `sqlcl_request` does NOT go through
it, because its callers "write ``WHENEVER SQLERROR EXIT FAILURE`` and expect a
process that can end". That is still true, and it is the design problem this
module answers rather than avoids: a body here IS allowed to end the process, and
when it does, the transcript it printed first comes back as the failure, in the
class `run_sqlcl_script` would have raised.

Four things were measured rather than reasoned about, all 2026-09-10 against
SQLcl 26.2 and Oracle 26ai Free:

* **`exit;` COMMITS.** An `insert` then a bare `exit;` left the row behind; the
  same insert then `exit rollback;` did not. Every body `sqlcl_request` built
  ended with `exit;`, so a transport that simply drops it would silently stop
  committing what an install script left open. The body ends with `commit;`.
* **A `WHENEVER SQLERROR EXIT ROLLBACK` body kills the held-open process**, and
  the driven session reported only `SQLcl exited mid-statement`, discarding the
  transcript. `patch/deploy_run.py` writes `str(error)` into the deployment log
  as that script's own record, so the transcript IS the deliverable of a failure.
* **`STORE SET` cannot round-trip SQLcl's settings, and that is why there is no
  snapshot here.** It looked like the exact answer to "one process, many scripts,
  whose `SET` wins", and it writes a 2.2 KB file that restores `pagesize`. It also
  writes lines SQLcl cannot read back: replaying one answers `SQLPLUS command
  failed - not enough arguments`, resets the theme and ENDS THE PROCESS, so the
  request that triggered it lost its script and its transcript. Measured live
  against the fixture schema, after a first reading that had only checked
  `pagesize` and called the round trip proved. What is reset per request is the
  two `WHENEVER` directives, measured silent; `spool off` was considered and
  dropped because it prints `not spooling currently` into every transcript.
* **The cwd cannot move.** A generated install script spools to
  ``SPOOL "./<folder>/<schema>.log"``, resolved against SQLcl's own working
  directory, which is fixed when the process is spawned. `run_sqlcl_script`
  passes `cwd=root` per call, so this transport restarts when `root` moves.

**What DOES carry between two requests is SQLcl's own `SET` state**, and nothing
here undoes it. The tree already answers that where it matters, which is why this
is a bounded gap rather than an open one: the connect block re-asserts
`SET DEFINE OFF` and the feedback dance on every request, and
`patch/deploy._deployment_payload` prepends `SET DEFINE OFF`, `SET TIMING OFF`,
`SET SQLBLANKLINES ON` and the run's `WHENEVER` directive to EVERY install script
-- written for a different reason (`-deploy` replays whatever script is on disk,
ADT #254/#283) and load-bearing here. The `WHENEVER` directives are the only
setting that changes control flow rather than formatting, so those two are reset
explicitly above.

The body travels as a file, not over the terminal: a pty is line disciplined and
`MAX_CANON` truncates a long line, while an exported package body is far longer
than that. So the same temp script `run_sqlcl_script` writes is written here, with
the same owner-only permissions, and only ``@"<path>"`` crosses the console --
which also keeps `@@` resolving against the script's own folder exactly as it
does today.

Windows keeps the process-per-script transport, because nothing drives a console
there: `sqlcl_console` measured no `SQL>` prompt across six settings on
2026-08-22, and `sqlcl_script_session.open_session` already sends that platform
to a script per request for the same reason.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adt_ai.shared.sqlcl_console import open_console
from adt_ai.shared.sqlcl_errors import (
    SqlclNotConnectedError,
    SqlclScriptError,
    SqlclTimeoutError,
)
from adt_ai.shared.sqlcl_script import (
    _connect_secrets,
    _died_before_the_script_ran,
    _ran_without_a_session,
    _scrub_secrets,
    _sqlcl_environment,
    write_sqlcl_script,
)
from adt_ai.shared.sqlcl_session import DrivenSqlcl

# Nothing drives a SQLcl console on Windows, so the reuse is keyed on the
# platform exactly as `sqlcl_script_session.open_session` is.
SESSION_REUSE_SUPPORTED = os.name != "nt"

# `sqlcl_request` has no deadline of its own -- `patch -deploy` and `diff` are
# deliberately unbounded, only `export_apex -rest` passes one -- so a body with no
# `timeout_seconds` gets a budget measured in days rather than none at all. A
# held-open process cannot afford none: an unbounded read on a child that died
# without closing its terminal would never return.
DEFAULT_TIMEOUT_SECONDS = 86_400.0

# Sent ahead of every request, before its connect block. These two are the only
# SQLcl state that changes CONTROL FLOW rather than formatting, so a
# `WHENEVER SQLERROR EXIT ROLLBACK` left armed by one deploy script would
# otherwise end the process on the next request's first warning. Both measured
# silent on a session that never armed them; `spool off` was considered for the
# same slot and dropped, because it prints `not spooling currently` into every
# transcript that does not need it.
REQUEST_PREAMBLE = (
    "whenever oserror continue",
    "whenever sqlerror continue",
)

# What a caller puts where the `exit;` was. See the module docstring: `exit`
# commits, measured, so a script that reaches this transport without it would
# silently stop committing. Held here beside the transport that makes it
# necessary, and appended by `db._run`, which is the one place that knows which
# transport a request is going to.
#
# `SET FEEDBACK OFF` first, and that half is not cosmetic. `exit;` commits
# SILENTLY; a bare `commit;` prints `Commit complete.`, which measured its way
# into the end of every transcript this transport returned -- and
# `export_apex -rest` writes its transcript to disk as the exported file, which
# is ADT #149 exactly. Nothing runs after it, so the setting is never restored.
#
# Joined rather than written with an escape, because the repo's client-data guard
# reads a backslash between two words as a Windows DOMAIN\HOST stamp.
REQUEST_EPILOGUE = "\n".join(("SET FEEDBACK OFF", "commit;"))


class SqlclRequestSession(DrivenSqlcl):
    """One SQLcl process serving many `sqlcl_request` scripts.

    Takes the console, reader thread and sentinel protocol from `DrivenSqlcl`,
    and answers on its own the two questions that make this transport different
    from the driven session beside it: it never connects, because every request
    carries its own connect block; and a request that ends the process is an
    outcome to report rather than a broken session.
    """

    def __init__(
        self,
        connection: Any,
        *,
        project_root: Path | None = None,
        startup_sql: str | None = None,
        config: Any = None,
        oci: bool = False,
        client_lib_dir: str | None = None,
        tns_admin: str | None = None,
    ) -> None:
        super().__init__(
            connection,
            project_root = project_root,
            startup_sql  = startup_sql,
            config       = config,
        )
        # The `oci` trio rides here for the reason it rides `_run`'s `extra`
        # (ADT #395): a SEPS connection needs SQLcl to find the OCI client, and
        # every other connection still runs thin.
        self.oci = oci
        self.client_lib_dir = client_lib_dir
        self.tns_admin = tns_admin
        self.launcher = ("sql", *(("-L", "-oci") if oci else ()), "-S", "/nolog")
        self._root: Path | None = None

    # -- the request/response protocol -------------------------------------

    def run(
        self,
        script: str,
        root: Path,
        *,
        timeout_seconds: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        """Run ``script`` and answer exactly as ``run_sqlcl_script`` would.

        ``script`` is the whole thing SQLcl is asked to run, connect block
        included, which is what keeps this a transport swap rather than a change
        to what a request does.
        """
        root.mkdir(parents=True, exist_ok=True)
        self._ensure(root)
        self._secrets = _connect_secrets(script)
        script_path = write_sqlcl_script(self._request_script(script), self.project_root)
        budget = timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT_SECONDS
        try:
            text, ending = self._collect(
                f'@"{script_path}"', timeout_seconds=budget, on_line=on_line
            )
        finally:
            script_path.unlink(missing_ok=True)
        output = _scrub_secrets(text, self._secrets)
        if ending == "timeout":
            self._abort()
            self._root = None
            raise SqlclTimeoutError(
                f"SQLcl did not finish within {budget:g} seconds and was killed."
                + (f"\n{output.strip()}" if output.strip() else "")
            )
        if ending == "eof":
            return self._answer_from_a_finished_process(output)
        return self._checked(output, returncode=0)

    def close(self) -> None:
        self._root = None
        super().close()

    # -- internals ---------------------------------------------------------

    def _ensure(self, root: Path) -> None:
        """Open a process for this root, reusing the one already open.

        `root` is the key because SQLcl fixes its working directory at spawn and
        a generated install script spools to a path relative to it. Nothing else
        varies: the connect travels with each request.
        """
        if self._console is not None and self._root == root:
            return
        super().close()
        self._root = root
        self._lines = type(self._lines)()  # a queue no previous reader holds (#670)
        try:
            self._spawn()
            self._await_prompt()
        except BaseException:
            self._abort()
            self._root = None
            raise

    def _spawn(self) -> None:
        """The base's spawn, plus the two things a request transport needs.

        `cwd`, because a generated install script spools to a relative path; and
        the `oci` environment, because a SEPS connection's SQLcl has to find its
        client (ADT #395). The base opens neither: a gateway statement resolves
        no paths, and `sqlcl_only` has no external-auth path.
        """
        self._console = open_console(
            self.launcher,
            dict(_sqlcl_environment(self.oci, self.client_lib_dir, self.tns_admin)),
            self._root,
        )
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def _request_script(self, script: str) -> str:
        """The caller's script, with the reset a fresh process would have given it.

        Nothing else is added. What terminates the script is the caller's, and
        `db._run` chooses it, because the choice is between `exit;` and `commit;`
        and only the caller knows which transport it is talking to.
        """
        parts = [*REQUEST_PREAMBLE, script.rstrip()]
        return "\n".join(parts) + "\n"

    def _answer_from_a_finished_process(self, output: str) -> str:
        """The script ended the process, which is a legal thing for one to do.

        `WHENEVER SQLERROR EXIT ROLLBACK` is on every generated install script and
        an `apex import` script carries its own `exit;`, so the exit CODE decides
        exactly as it does in `run_sqlcl_script`, and the transcript printed
        before the exit is what the caller logs either way.
        """
        console, self._console = self._console, None
        returncode = None
        if console is not None:
            with contextlib.suppress(Exception):
                returncode = console.exit_code
            with contextlib.suppress(Exception):
                console.close()
        self._join_pump()
        self._root = None
        # An unreadable exit code cannot be read as success: a process that
        # vanished without saying why is a failed script, not a silent one.
        return self._checked(output, returncode=1 if returncode is None else returncode)

    def _checked(self, output: str, *, returncode: int) -> str:
        """`run_sqlcl_script`'s three verdicts, in its order, on this transcript."""
        if _died_before_the_script_ran(output):
            raise SqlclScriptError(
                "SQLcl failed before it ran the script "
                f"(exit code {returncode}). Full SQLcl output:\n{output.strip()}"
            )
        if _ran_without_a_session(output):
            raise SqlclNotConnectedError(
                "SQLcl ran the script without a connected session "
                f"(exit code {returncode}). Full SQLcl output:\n{output.strip()}"
            )
        if returncode != 0:
            raise SqlclScriptError(
                output.strip() or f"SQLcl failed with exit code {returncode}"
            )
        return output


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "REQUEST_EPILOGUE",
    "REQUEST_PREAMBLE",
    "SESSION_REUSE_SUPPORTED",
    "SqlclRequestSession",
]
