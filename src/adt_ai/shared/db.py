from __future__ import annotations

import contextlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from adt_ai.shared.connections import DEFAULT_PORT, Connection, InvalidConnectionError
from adt_ai.shared.oracle_session import DDL_LOCK_TIMEOUT_STATEMENT
from adt_ai.shared.sqlcl_connect import (
    SqlclConnect,
    _ensure_wallet_folder,
    sqlcl_connect,
)
from adt_ai.shared.sqlcl_names import credential_fingerprint, record_sqlcl_registration
from adt_ai.shared.sqlcl_script import run_sqlcl_script
from adt_ai.shared.startup import apply_startup

# Fail-fast network timeouts, applied on the single shared OracleGateway connect
# path so every module that talks to the database inherits them, there is no
# second connection code path to keep in sync. The two timeouts are independent
# budgets, configurable via ``connect_timeout_seconds`` / ``query_timeout_seconds``
# in config.yaml (see ``_resolve_timeout_seconds``): a query must never inherit
# the short connect budget, which was the bug that made every long-running query
# fail.
#
# CONNECT_TIMEOUT_SECONDS bounds the connect phase (``tcp_connect_timeout``),
# including a reconnect to a different schema: a dead or unreachable host fails
# *inside* ``connect()`` in ~15s instead of waiting on the OS TCP default for
# minutes; paired with ``retry_count=0`` so the driver never silently retries a
# down host and stretches that budget.
#
# QUERY_TIMEOUT_SECONDS (as ``CALL_TIMEOUT_MS``) bounds a single round-trip on an
# *established* connection (``connection.call_timeout``): a mid-query socket
# death (ORA-03113) aborts as DPY-4011/ORA-03136 rather than hanging until the OS
# gives up, while a legitimate long-running query gets its own generous 20-minute
# budget instead of being cut off by the connect timeout. The abort carries the
# offending SQL, so the CLI classifies it as a query failure, not a connection
# failure.
CONNECT_TIMEOUT_SECONDS = 15
QUERY_TIMEOUT_SECONDS = 1_200
CALL_TIMEOUT_MS = QUERY_TIMEOUT_SECONDS * 1000


def _resolve_timeout_seconds(config: Mapping[str, Any] | None, key: str, default: int) -> int:
    if not config or config.get(key) is None:
        return default
    return int(config[key])


def _attach_sql(error: BaseException, sql: str) -> None:
    """Record the failing SQL on the exception for the CLI error banner.

    The top-level handler distinguishes a query error (which happens after a
    successful connect) from a connection failure by the presence of this
    attribute, and prints the offending query. Best-effort: some driver
    exception types may reject attribute assignment, in which case the banner
    simply falls back to message-marker classification.
    """
    try:
        if getattr(error, "adt_sql", None) is None:
            error.adt_sql = sql  # type: ignore[attr-defined]
    except Exception:
        pass


def _close_resource(resource: object) -> None:
    """Close a DB-API resource while tolerating deliberately minimal fakes."""
    close = getattr(resource, "close", None)
    if callable(close):
        close()


class QueryGateway(Protocol):
    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def read_only_fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        ...

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        ...

    def sqlcl_request(
        self,
        request: str,
        root: Path,
        timeout_seconds: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        ...

    def close(self) -> None:
        ...


class FakeGateway:
    def __init__(
        self,
        results: Mapping[str, list[dict[str, Any]]] | None = None,
        sqlcl_output: str = "",
    ) -> None:
        self.results = dict(results or {})
        self.sqlcl_output = sqlcl_output
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.read_only_queries: list[tuple[str, dict[str, Any]]] = []
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.sqlcl_requests: list[tuple[str, Path]] = []
        self.sqlcl_timeouts: list[float | None] = []
        self.closed = False

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.queries.append((sql, dict(params or {})))
        return self.results.get(sql, [])

    def read_only_fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.read_only_queries.append((sql, dict(params or {})))
        return self.results.get(sql, [])

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.statements.append((sql, dict(params or {})))

    def sqlcl_request(
        self,
        request: str,
        root: Path,
        timeout_seconds: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        self.sqlcl_requests.append((request, root))
        self.sqlcl_timeouts.append(timeout_seconds)
        if on_line is not None:
            # The real gateway hands a live reader each line as SQLcl prints it,
            # so the fake replays its canned transcript the same way (ADT #434).
            # A fake that only returned the finished text would let a
            # non-streaming implementation pass every progress test.
            for line in self.sqlcl_output.splitlines():
                on_line(line)
        return self.sqlcl_output

    def close(self) -> None:
        self.closed = True


class OracleGateway:
    FETCH_ARRAYSIZE = 5000

    def __init__(
        self,
        connection: Connection,
        driver: Any | None = None,
        project_root: Path | None = None,
        startup_sql: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.connection = connection
        self.driver = driver
        self.project_root = project_root
        self.startup_sql = startup_sql
        self.connect_timeout_seconds = _resolve_timeout_seconds(
            config, "connect_timeout_seconds", CONNECT_TIMEOUT_SECONDS
        )
        self.query_timeout_seconds = _resolve_timeout_seconds(
            config, "query_timeout_seconds", QUERY_TIMEOUT_SECONDS
        )
        self.sqlcl_named_enabled = (
            config is None or config.get("sqlcl_named_connections") is not False
        )
        self._connection: Any | None = None
        self._thick_initialized = False

    def connect(self) -> Any:
        if self._connection is not None:
            return self._connection

        driver = self._driver()
        self._initialize_thick_client(driver)
        connection = driver.connect(**self._connect_kwargs(driver))
        try:
            # Bound every round-trip on the established connection so a dead
            # socket mid-query aborts quickly instead of hanging on the OS
            # default, while a legitimate long-running query gets its own
            # independent budget.
            connection.call_timeout = self.query_timeout_seconds * 1000
            self._install_output_type_handler(connection, driver)
            self._apply_default_session_settings(connection)
            if self.startup_sql:
                apply_startup(connection, self.startup_sql)
        except BaseException:
            # Teardown is best-effort on the failing path: its own driver error
            # must not replace the STARTUP/connect failure the user can act on.
            with contextlib.suppress(Exception):
                _close_resource(connection)
            raise
        # Do not cache a half-initialized session. A failed STARTUP.sql or
        # default-session statement closes the new connection above; a later
        # call gets a genuinely fresh attempt rather than the broken object.
        self._connection = connection
        return connection

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            _close_resource(connection)

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self.connect().cursor()
        cursor.arraysize = self.FETCH_ARRAYSIZE
        try:
            cursor.execute(sql, dict(params or {}))
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        except Exception as error:
            _attach_sql(error, sql)
            raise
        finally:
            _close_resource(cursor)

    def read_only_fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch rows under a ``READ ONLY`` transaction.

        Issues ``SET TRANSACTION READ ONLY`` on the session before the query,
        then rolls back to end the transaction; the committing ``execute`` path
        is never used. This constrains the caller's transaction. It cannot undo
        work committed independently by an autonomous-transaction function
        invoked from an otherwise valid SELECT, so callable-code grants remain
        the outer security boundary.
        """
        connection = self.connect()
        cursor = connection.cursor()
        cursor.arraysize = self.FETCH_ARRAYSIZE
        try:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute(sql, dict(params or {}))
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        except Exception as error:
            _attach_sql(error, sql)
            raise
        finally:
            try:
                connection.rollback()
            finally:
                _close_resource(cursor)

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        connection = self.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(sql, dict(params or {}))
            connection.commit()
        except Exception as error:
            _attach_sql(error, sql)
            raise
        finally:
            _close_resource(cursor)

    def sqlcl_request(
        self,
        request: str,
        root: Path,
        timeout_seconds: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        root.mkdir(parents=True, exist_ok=True)
        body = f"{request.rstrip()}\nexit;\n"
        plan = self._sqlcl_plan()
        if plan.registers is None:
            if plan.name is None:
                # Plain credentialed connect: nothing is registered, so a
                # failure here is the caller's to see.
                return self._run(plan, body, root, timeout_seconds, on_line)
            try:
                return self._run(plan, body, root, timeout_seconds, on_line)
            except RuntimeError:
                # Fresh fingerprint, but the local SQLcl store has never seen
                # the name (the YAML travels with the project, the store does
                # not). Re-register and retry once.
                #
                # This catches `SqlclNotConnectedError` too, and has to: a lost
                # store entry does not exit non-zero the way this path once
                # assumed (SQLcl reports `SP2-0640` and exits 0) so before
                # ADT #232 the retry never ran for the failure it was written
                # for.
                plan = self._sqlcl_plan(force_register=True)
        output = self._run(plan, body, root, timeout_seconds, on_line)
        if plan.registers is not None and plan.registers.sqlcl_source:
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
        # `oci` is passed ONLY when it is true, so the ordinary call is
        # byte-for-byte the one every existing caller and test fake already
        # takes. Per connection, off the auth mode, never a global switch: a
        # SEPS connection needs the OCI driver and every other one still runs
        # thin (ADT #395).
        extra: dict[str, Any] = (
            {
                "oci": True,
                "client_lib_dir": self.connection.client_lib_dir,
                "tns_admin": self.connection.wallet_path,
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
            f"{plan.script}{body}",
            root,
            self.project_root,
            timeout_seconds = timeout_seconds,
            **extra,
        )

    def _sqlcl_plan(self, *, force_register: bool = False) -> SqlclConnect:
        return sqlcl_connect(
            self.connection,
            startup_sql       = self.startup_sql,
            project_root      = self.project_root,
            named_connections = self.sqlcl_named_enabled,
            force_register    = force_register,
        )

    def _apply_default_session_settings(self, connection: Any) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute(DDL_LOCK_TIMEOUT_STATEMENT)
        finally:
            _close_resource(cursor)

    def _driver(self) -> Any:
        if self.driver is not None:
            return self.driver

        import oracledb

        return oracledb

    def _initialize_thick_client(self, driver: Any) -> None:
        if not self.connection.thick or self._thick_initialized:
            return

        kwargs: dict[str, Any] = {}
        if self.connection.client_lib_dir:
            kwargs["lib_dir"] = self.connection.client_lib_dir
        # A TNS alias is resolved by the client library, not by the connect call,
        # so a SEPS connection has to name its `tnsnames.ora` folder HERE.
        # Passing `config_dir` to `connect()` alone leaves the alias unresolvable
        # in thick mode (ADT #395).
        if self.connection.external_auth and self.connection.wallet_path:
            kwargs["config_dir"] = str(
                _ensure_wallet_folder(Path(self.connection.wallet_path).expanduser())
            )
        driver.init_oracle_client(**kwargs)
        self._thick_initialized = True

    def _install_output_type_handler(self, connection: Any, driver: Any) -> None:
        clob_type = getattr(driver, "CLOB", None) or getattr(driver, "DB_TYPE_CLOB", None)
        long_string_type = getattr(driver, "LONG_STRING", None)
        if clob_type is None or long_string_type is None:
            return

        def output_type_handler(
            cursor: Any,
            name: str,
            default_type: Any,
            size: Any,
            precision: Any,
            scale: Any,
        ) -> Any:
            del name, size, precision, scale
            if default_type == clob_type:
                return cursor.var(long_string_type, arraysize=cursor.arraysize)
            return None

        connection.outputtypehandler = output_type_handler

    def _connect_kwargs(self, driver: Any) -> dict[str, Any]:
        # `auth: external` (ADT #395) passes no user and no password at all: the
        # Oracle client library reads the credential out of `cwallet.sso` itself,
        # so it never exists as a Python string and there is nothing here for an
        # agent reading this process to catch. Every other option on the security
        # page moves where the secret rests; this one removes it from the call.
        if self.connection.external_auth:
            kwargs: dict[str, Any] = {
                "dsn": self._external_dsn(),
                "externalauth": True,
                "tcp_connect_timeout": self.connect_timeout_seconds,
                "retry_count": 0,
            }
            self._apply_wallet(kwargs)
            return kwargs

        kwargs = {
            "user": self.connection.username,
            "password": self.connection.password.reveal(),
            "dsn": self._dsn(driver),
            # Fail a dead/unreachable host inside the connect phase (also applies
            # when reconnecting to a different schema), with no driver-level
            # connect retries to stretch that budget.
            "tcp_connect_timeout": self.connect_timeout_seconds,
            "retry_count": 0,
        }
        self._apply_wallet(kwargs)
        if self.connection.wallet_password:
            kwargs["wallet_password"] = self.connection.wallet_password.reveal()
        return kwargs

    def _apply_wallet(self, kwargs: dict[str, Any]) -> None:
        if not self.connection.wallet_path:
            return
        wallet_path = _ensure_wallet_folder(Path(self.connection.wallet_path).expanduser())
        kwargs["config_dir"] = str(wallet_path)
        kwargs["wallet_location"] = str(wallet_path)

    def _external_dsn(self) -> str:
        """The TNS alias the wallet files the credential under.

        A SEPS wallet is keyed by alias, not by host and service, so this is a
        name resolved through `tnsnames.ora` beside the wallet rather than a
        descriptor built from the connection's own parts.
        """
        alias = self.connection.tns or self.connection.service
        if not alias:
            raise InvalidConnectionError(
                f"{self.connection.environment}.{self.connection.schema} sets "
                "auth: external but names no TNS alias. Add `tns: <alias>`, the "
                "name the wallet stores the credential under."
            )
        return str(alias)

    def _dsn(self, driver: Any) -> str:
        if not self.connection.hostname:
            return self.connection.service or self.connection.sid or ""

        return driver.makedsn(
            self.connection.hostname,
            self.connection.port or DEFAULT_PORT,
            service_name=self.connection.service,
            sid=self.connection.sid,
        )
