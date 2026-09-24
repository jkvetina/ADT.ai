"""`OracleGateway`'s SQLcl half: one request, one connect plan, two transports.

Split out of ``shared/db`` by ADT #760, which took that module past the 24 KB
context cap, and along the seam the module already had: everything here is about
the SQLcl child process, and everything left in ``db`` is about the
python-oracledb connection. `db_fakes` came out of the same module for the same
reason (`#670`).

The mixin, not a second gateway class: `tests/contracts/test_gateway_startup_
wiring.py` allows exactly one ``OracleGateway(...)`` construction in the tree, so
a second class here would be a second place session setup could drift, which is
the thing that contract exists to prevent (ADT #179, #181).

**Two transports serve one request, and the request does not know which.**
``run_sqlcl_script`` starts a process per script; ``SqlclRequestSession`` keeps
one and feeds it many. The script handed to either is the same, connect block
included, and only the terminator differs -- see :func:`_run`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from adt_ai.shared.connections import Connection
from adt_ai.shared.sqlcl_connect import (
    SqlclConnect,
    _ensure_wallet_folder,
    resolve_wallet_path,
    sqlcl_connect,
)
from adt_ai.shared.sqlcl_errors import SqlclNotConnectedError
from adt_ai.shared.sqlcl_names import (
    credential_fingerprint,
    read_sqlcl_registration,
    record_sqlcl_registration,
)
from adt_ai.shared.sqlcl_request_session import (
    REQUEST_EPILOGUE,
    SESSION_REUSE_SUPPORTED,
    SqlclRequestSession,
)
from adt_ai.shared.sqlcl_script import run_sqlcl_script


class SqlclRequestMixin:
    """Everything ``OracleGateway`` does through SQLcl rather than through a driver."""

    # Provided by `OracleGateway.__init__`; declared so this mixin type-checks on
    # its own rather than inheriting the promise implicitly.
    connection          : Connection
    project_root        : Path | None
    startup_sql         : str | None
    sqlcl_named_enabled : bool
    _sqlcl_request_session : SqlclRequestSession | None

    def sqlcl_request(
        self,
        request: str,
        root: Path,
        timeout_seconds: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        root.mkdir(parents=True, exist_ok=True)
        # The terminator belongs to the transport rather than to the request
        # (ADT #760), so `_run` appends it. See there.
        body = request.rstrip()
        plan = self._sqlcl_plan()
        if plan.registers is None:
            if plan.name is None:
                # Plain credentialed connect: nothing is registered, so a
                # failure here is the caller's to see.
                return self._run(plan, body, root, timeout_seconds, on_line)
            try:
                return self._run(plan, body, root, timeout_seconds, on_line)
            except SqlclNotConnectedError:
                # Fresh fingerprint, but the local SQLcl store has never seen
                # the name (the YAML travels with the project, the store does
                # not). Re-register and retry once.
                #
                # `SqlclNotConnectedError` is the ONLY failure this retry
                # answers, and it has to be caught by name: a lost store entry
                # does not exit non-zero the way this path once assumed (SQLcl
                # reports `SP2-0640` and exits 0), so before ADT #232 the retry
                # never ran for the failure it was written for.
                #
                # Catching bare `RuntimeError` re-ran the body for every other
                # failure too, because `SqlclScriptError` and `SqlclTimeoutError`
                # subclass it (ADT #661): a timeout (`export_apex -rest` is the
                # caller that passes one) killed SQLcl and then waited the whole
                # deadline a second time, and any body exiting non-zero ran twice
                # with the first transcript discarded.
                #
                # The reused transport raises the same class off the same
                # transcript, so this retry is unchanged by ADT #760.
                plan = self._sqlcl_plan(force_register=True)
        output = self._run(plan, body, root, timeout_seconds, on_line)
        if plan.registers is not None and plan.registers.sqlcl_source and plan.name:
            record_sqlcl_registration(
                plan.registers.sqlcl_source,
                plan.registers.environment,
                plan.registers.schema,
                plan.name,
                credential_fingerprint(plan.registers),
            )
        return output

    def _run(
        self,
        plan: SqlclConnect,
        body: str,
        root: Path,
        timeout_seconds: float | None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        # The terminator is the one thing that differs between the transports,
        # and it is chosen here because this is the only place that knows which
        # one is about to run. SQLcl's `exit;` COMMITS -- measured 2026-09-10, an
        # insert followed by a bare `exit;` left the row behind -- so a process
        # that is ending takes the `exit;` it always took, and one that is being
        # kept takes the `commit;` that is the same promise without the ending
        # (ADT #760).
        session = self._request_session()
        if session is not None:
            return session.run(
                f"{plan.script}{body}\n{REQUEST_EPILOGUE}\n",
                root,
                timeout_seconds = timeout_seconds,
                on_line         = on_line,
            )
        # `oci` is passed ONLY when it is true, so the ordinary call is
        # byte-for-byte the one every existing caller and test fake already
        # takes. Per connection, off the auth mode, never a global switch: a
        # SEPS connection needs the OCI driver and every other one still runs
        # thin (ADT #395).
        extra: dict[str, Any] = (
            {
                "oci": True,
                "client_lib_dir": self.connection.client_lib_dir,
                "tns_admin": self._tns_admin(),
            }
            if self.connection.external_auth
            else {}
        )
        # `on_line` rides the same rule for the same reason (ADT #434): only a
        # deploy with a live console passes one, so every other call stays the
        # byte-for-byte one it was, test fakes included.
        if on_line is not None:
            extra["on_line"] = on_line
        return run_sqlcl_script(
            f"{plan.script}{body}\nexit;\n",
            root,
            self.project_root,
            timeout_seconds = timeout_seconds,
            **extra,
        )

    def _request_session(self) -> SqlclRequestSession | None:
        """The reused SQLcl process, or `None` where one cannot be driven.

        Opened lazily, so a command that never reaches SQLcl never starts a JVM,
        and held for the gateway's lifetime, which is one command. `None` on
        Windows, where `sqlcl_console` measured no prompt at all across six
        settings, so that platform keeps the process-per-script transport it has
        always had (ADT #449).
        """
        if not SESSION_REUSE_SUPPORTED:
            return None
        if self._sqlcl_request_session is None:
            external = self.connection.external_auth
            self._sqlcl_request_session = SqlclRequestSession(
                self.connection,
                project_root   = self.project_root,
                startup_sql    = self.startup_sql,
                oci            = external,
                client_lib_dir = self.connection.client_lib_dir if external else None,
                tns_admin      = self._tns_admin() if external else None,
            )
        return self._sqlcl_request_session

    def _tns_admin(self) -> str | None:
        """The folder SQLcl reads `tnsnames.ora` from, resolved once for all callers.

        `#670`: this handed SQLcl the raw `wallet_path`, while `_apply_wallet`
        and `_initialize_thick_client` resolve the same field. A
        `config/Wallet_X.zip` value therefore pointed TNS_ADMIN at a zip file,
        relative to a cwd nobody had set, and the alias never resolved. Same two
        steps as the driver paths: anchor a relative path to the project root,
        then name the extracted folder rather than the archive.
        """
        if not self.connection.wallet_path:
            return None
        anchored = resolve_wallet_path(self.connection.wallet_path, self.project_root)
        return str(_ensure_wallet_folder(anchored.expanduser()))

    def _sqlcl_plan(self, *, force_register: bool = False) -> SqlclConnect:
        return sqlcl_connect(
            self._registered_connection(),
            startup_sql       = self.startup_sql,
            project_root      = self.project_root,
            named_connections = self.sqlcl_named_enabled,
            force_register    = force_register,
        )

    def _registered_connection(self) -> Connection:
        """`self.connection` with the SQLcl registration its file records now.

        `self.connection` is loaded once per command, so a registration recorded
        after that, by this gateway's own first request or by another gateway on
        the same file, was never seen and each later request registered again
        (#924 F59). The file's current `sqlcl` / `sqlcl_sync` win; a key it
        does not hold, or a file that cannot be read, keeps the loaded value.
        """
        connection = self.connection
        if not connection.sqlcl_source or connection.external_auth:
            return connection
        recorded = read_sqlcl_registration(
            connection.sqlcl_source, connection.environment, connection.schema
        )
        if not recorded:
            return connection
        return replace(
            connection,
            sqlcl_name = recorded.get("sqlcl", connection.sqlcl_name),
            sqlcl_sync = recorded.get("sqlcl_sync", connection.sqlcl_sync),
        )


__all__ = ["SqlclRequestMixin"]
