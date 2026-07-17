from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from zipfile import ZipFile

from adt_ai.shared import text_files
from adt_ai.shared.connections import DEFAULT_PORT, Connection
from adt_ai.shared.oracle_session import DDL_LOCK_TIMEOUT_STATEMENT
from adt_ai.shared.startup import apply_startup

_TEMP_GITIGNORE_ENTRY = "config/temp/"

# Fail-fast network timeouts, applied on the single shared OracleGateway connect
# path so every module that talks to the database inherits them — there is no
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


def coerce_password(password: str | bytes | None) -> str | None:
    """Normalise a connection password to ``str`` before it reaches a driver.

    Passwords originate from the connection YAML's ``db.pwd``. A value written
    with a YAML binary tag (``pwd: !!binary <base64>``) is parsed by
    ``yaml.safe_load`` as ``bytes``, which the python-oracledb thin driver
    rejects deep inside ``ConnectParamsImpl._transform_password`` with an opaque
    ``TypeError: Expected str, got bytes`` — long after the offending field is
    out of sight. Decode such values here so a binary password connects instead
    of crashing; ``str`` and ``None`` pass through untouched.
    """
    if isinstance(password, bytes):
        return password.decode("utf-8")
    return password


def _ensure_temp_ignored(root: Path) -> None:
    """Idempotently ensure ``config/temp/`` is git-ignored in ``root``.

    Appends the entry to an existing ``.gitignore`` (fixing a missing
    trailing newline) or
    creates the file when absent.
    """
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if _TEMP_GITIGNORE_ENTRY in {line.strip() for line in existing.splitlines()}:
        return
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    text_files.write_text(gitignore, prefix + _TEMP_GITIGNORE_ENTRY + "\n")


def _sqlcl_temp_dir(project_root: Path | None) -> Path | None:
    """Return the gitignored scratch dir for throwaway SQLcl scripts.

    SQLcl ``@`` scripts are ephemeral; they must never land beside exported code
    in the project repo. When the project root is known, route them to
    ``<project_root>/config/temp/`` and ensure that folder is git-ignored
    Otherwise fall back to the OS temp dir
    (``dir=None``) so the script still never touches the repo.
    """
    if project_root is None:
        return None
    temp_dir = project_root / "config" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _ensure_temp_ignored(project_root)
    return temp_dir


# Pulls the cleartext password out of a SQLcl ``connect`` line. The connect
# lines we build all embed it the same way -- ``user/"password"@dsn`` -- so we
# can recover it from the script we are about to run and scrub it from anything
# SQLcl echoes back into stdout/stderr.
_CONNECT_PWD_RE = re.compile(r'/"(?P<pwd>[^"\n]+)"@')


def _connect_secrets(script: str) -> set[str]:
    return {match.group("pwd") for match in _CONNECT_PWD_RE.finditer(script)}


def _scrub_secrets(text: str, secrets: set[str]) -> str:
    """Replace any captured connect-line password with ``***``.

    SQLcl may echo the ``connect`` command into its captured output, which we
    both return to callers and embed into ``RuntimeError`` messages and on-disk
    deployment logs. Eliding the password keeps cleartext credentials out of all
    three sinks.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def run_sqlcl_script(script: str, root: Path, project_root: Path | None = None) -> str:
    root.mkdir(parents=True, exist_ok=True)
    secrets = _connect_secrets(script)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding = "utf-8",
        newline  = "\n",
        suffix   = ".sql",
        dir      = _sqlcl_temp_dir(project_root),
        delete   = False,
    ) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    # The script embeds a cleartext connect credential; pin owner-only perms even
    # if the platform's tempfile defaults ever differ from mkstemp's 0600.
    os.chmod(script_path, 0o600)
    try:
        # ``-S`` (silent) suppresses the banner and command echo so SQLcl does not
        # print the connect line in the first place; the scrub below is the
        # belt-and-braces backstop for the cases where it still does.
        completed = subprocess.run(
            ["sql", "-S", "/nolog", f"@{script_path}"],
            cwd            = root,
            check          = False,
            capture_output = True,
            text           = True,
        )
    finally:
        script_path.unlink(missing_ok=True)
    output = _scrub_secrets((completed.stdout or "") + (completed.stderr or ""), secrets)
    if completed.returncode != 0:
        raise RuntimeError(
            output.strip() or f"SQLcl failed with exit code {completed.returncode}"
        )
    return output


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

    def sqlcl_request(self, request: str, root: Path) -> str:
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

    def sqlcl_request(self, request: str, root: Path) -> str:
        self.sqlcl_requests.append((request, root))
        return self.sqlcl_output


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
        self._connection: Any | None = None
        self._thick_initialized = False

    def connect(self) -> Any:
        if self._connection is not None:
            return self._connection

        driver = self._driver()
        self._initialize_thick_client(driver)
        self._connection = driver.connect(**self._connect_kwargs(driver))
        # Bound every round-trip on the established connection so a dead socket
        # mid-query aborts quickly instead of hanging on the OS default, while a
        # legitimate long-running query gets its own independent budget.
        self._connection.call_timeout = self.query_timeout_seconds * 1000
        self._install_output_type_handler(self._connection, driver)
        self._apply_default_session_settings(self._connection)
        if self.startup_sql:
            apply_startup(self._connection, self.startup_sql)
        return self._connection

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self.connect().cursor()
        cursor.arraysize = self.FETCH_ARRAYSIZE
        try:
            cursor.execute(sql, dict(params or {}))
        except Exception as error:
            _attach_sql(error, sql)
            raise
        columns = [column[0] for column in cursor.description or []]
        return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]

    def read_only_fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch rows under a ``READ ONLY`` transaction.

        Issues ``SET TRANSACTION READ ONLY`` on the session before the query so the
        database itself rejects any write a side-effecting function might attempt
        (ORA-01456), then rolls back to end the transaction — the committing
        ``execute`` path is never used.
        """
        connection = self.connect()
        cursor = connection.cursor()
        cursor.arraysize = self.FETCH_ARRAYSIZE
        cursor.execute("SET TRANSACTION READ ONLY")
        try:
            cursor.execute(sql, dict(params or {}))
            columns = [column[0] for column in cursor.description or []]
            return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]
        except Exception as error:
            _attach_sql(error, sql)
            raise
        finally:
            connection.rollback()

    def execute(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        connection = self.connect()
        cursor = connection.cursor()
        try:
            cursor.execute(sql, dict(params or {}))
        except Exception as error:
            _attach_sql(error, sql)
            raise
        connection.commit()

    def sqlcl_request(self, request: str, root: Path) -> str:
        root.mkdir(parents=True, exist_ok=True)
        # SQLcl understands STARTUP.sql natively, so it is injected verbatim
        # after the default session settings and before the request.
        startup = f"{self.startup_sql.rstrip()}\n" if self.startup_sql else ""
        payload = (
            f"{self._sqlcl_connect()}\n"
            f"{DDL_LOCK_TIMEOUT_STATEMENT};\n"
            f"{startup}"
            f"{request.rstrip()}\n"
            "exit;\n"
        )
        return run_sqlcl_script(payload, root, self.project_root)

    def _apply_default_session_settings(self, connection: Any) -> None:
        connection.cursor().execute(DDL_LOCK_TIMEOUT_STATEMENT)

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
        kwargs: dict[str, Any] = {
            "user": self.connection.username,
            "password": coerce_password(self.connection.password),
            "dsn": self._dsn(driver),
            # Fail a dead/unreachable host inside the connect phase (also applies
            # when reconnecting to a different schema), with no driver-level
            # connect retries to stretch that budget.
            "tcp_connect_timeout": self.connect_timeout_seconds,
            "retry_count": 0,
        }
        if self.connection.wallet_path:
            wallet_path = _ensure_wallet_folder(Path(self.connection.wallet_path).expanduser())
            kwargs["config_dir"] = str(wallet_path)
            kwargs["wallet_location"] = str(wallet_path)
        if self.connection.wallet_password:
            kwargs["wallet_password"] = coerce_password(self.connection.wallet_password)
        return kwargs

    def _dsn(self, driver: Any) -> str:
        if not self.connection.hostname:
            return self.connection.service or self.connection.sid or ""

        return driver.makedsn(
            self.connection.hostname,
            self.connection.port or DEFAULT_PORT,
            service_name=self.connection.service,
            sid=self.connection.sid,
        )

    def _sqlcl_connect(self) -> str:
        username = self.connection.username
        password = coerce_password(self.connection.password)
        service = self.connection.service or self.connection.sid or ""
        if self.connection.wallet_path:
            wallet_path = _ensure_wallet_folder(Path(self.connection.wallet_path).expanduser())
            wallet_zip = (
                wallet_path.with_suffix(".zip")
                if wallet_path.suffix != ".zip"
                else wallet_path
            )
            return f'connect -cloudconfig "{wallet_zip}" {username}/"{password}"@{service}'
        if self.connection.hostname:
            dsn = f"{self.connection.hostname}:{self.connection.port or DEFAULT_PORT}/{service}"
        else:
            dsn = service
        return f'connect {username}/"{password}"@{dsn}'


def _ensure_wallet_folder(wallet_path: Path) -> Path:
    if wallet_path.suffix.lower() == ".zip":
        zip_path = wallet_path
        wallet_folder = wallet_path.with_suffix("")
    else:
        zip_path = wallet_path.with_suffix(".zip")
        wallet_folder = wallet_path
    if _wallet_needs_extract(wallet_folder) and zip_path.is_file():
        _extract_wallet_zip(zip_path, wallet_folder)
    return wallet_folder


def _wallet_needs_extract(wallet_folder: Path) -> bool:
    return not (wallet_folder / "tnsnames.ora").is_file()


def _extract_wallet_zip(zip_path: Path, wallet_folder: Path) -> None:
    wallet_folder.mkdir(parents=True, exist_ok=True)
    with ZipFile(zip_path) as archive:
        target = wallet_folder.resolve()
        for member in archive.infolist():
            member_path = (wallet_folder / member.filename).resolve()
            if target != member_path and target not in member_path.parents:
                raise RuntimeError(f"Unsafe wallet zip entry: {member.filename}")
        archive.extractall(wallet_folder)
