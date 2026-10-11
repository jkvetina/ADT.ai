"""The gateway for an `ords:` connection: REST-enabled SQL over HTTPS (ADT #1071).

Some sites close the listener port and publish only ORDS. A connection whose file
carries `ords: https://host/ords/<schema alias>` (Jan, 2026-10-11, picked that
shape) reaches the database through ORDS's REST-enabled SQL endpoint,
`<url>/_/sql`, and never opens a python-oracledb session. Four things about that
endpoint decide how this class is built, each measured on 2026-10-11 against
ORDS 26.1 on the local container rather than assumed:

* **A request is a session.** Every POST runs in a database session of its own and
  is committed at its end; two requests share nothing. So the per-session setup a
  driver connection gets once (the DDL lock timeout, `STARTUP.sql`) goes in front
  of every request, and a flow that needs two statements in one session sends
  them as ONE request through `fetch_all_after`.
* **Binds are real.** The request carries typed binds beside the script, so the
  statement is posted untouched. SQLcl's own REST connector does not manage this:
  `var` plus `exec` plus a bound `select` answers `ORA-17283: No resultset
  available`, which is why queries come here directly instead of through SQLcl.
* **A failed statement does not stop the script.** `WHENEVER SQLERROR` is a
  restricted command on this endpoint (`SP2-0738`), so every item of the answer
  is checked and the first error raises, wherever it sits.
* **A result set is paged at 10000 rows** (`hasMore`, `offset`), and is read to
  its end by asking again from the next offset.

`sqlcl_request` is inherited: `patch -deploy`, `apex import` and the REST export
are SQLcl scripts, and SQLcl connects over the same URL (`sqlcl_connect`).
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from adt_ai.shared.connection_errors import ConnectFailedError
from adt_ai.shared.connections import Connection
from adt_ai.shared.db import OracleGateway, _attach_sql
from adt_ai.shared.oracle_session import DDL_LOCK_TIMEOUT_STATEMENT
from adt_ai.shared.sqlcl_gateway import _terminated

Statement = tuple[str, Mapping[str, Any] | None]

_DATE_SHAPES = ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")
_BINARY_TYPES = frozenset({"RAW", "LONG RAW", "BLOB"})


class OrdsRequestError(RuntimeError):
    """A statement sent through ORDS failed, or could not be sent as asked."""


class OrdsGateway(OracleGateway):
    """`QueryGateway` served by ORDS REST-enabled SQL.

    It extends `OracleGateway` for the reason `SqlclGateway` does: `sqlcl_request`
    is already written once there, and `tests/contracts/test_gateway_startup_wiring.py`
    allows exactly one `OracleGateway(...)` construction in the tree.
    """

    # Two requests never reach one session, see the module docstring. Read by
    # `shared/session_scope`; a flow that needs one asks `fetch_all_after`.
    holds_a_session = False

    def __init__(
        self,
        connection: Connection,
        driver: Any | None = None,
        project_root: Path | None = None,
        startup_sql: str | None = None,
        config: Mapping[str, Any] | None = None,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            connection,
            driver       = driver,
            project_root = project_root,
            startup_sql  = startup_sql,
            config       = config,
        )
        self._opener = opener or urlopen

    def connect(self) -> Any:
        raise OrdsRequestError(
            "This connection reaches the database through its ords URL, so it has "
            "no listener for a python-oracledb session. Something asked the gateway "
            "for a driver connection directly instead of going through fetch_all, "
            "read_only_fetch_all, execute or fetch_clob."
        )

    # -- QueryGateway ------------------------------------------------------

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        exact_numbers: bool = False,
    ) -> list[dict[str, Any]]:
        return self.fetch_all_after((), sql, params, exact_numbers=exact_numbers)

    def fetch_all_after(
        self,
        setup: Sequence[Statement],
        sql: str,
        params: Mapping[str, Any] | None = None,
        *,
        exact_numbers: bool = False,
    ) -> list[dict[str, Any]]:
        """Run ``setup`` and then the query ``sql`` in one request, one session.

        The answer is the query's rows. A later page re-sends the whole request,
        setup included, because the session that held the setup ended with the
        first one.
        """
        statements = [*setup, (sql, params)]
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            items = self._request(statements, sql, offset=offset, exact_numbers=exact_numbers)
            result = items[-1].get("resultSet") or {}
            rows.extend(_rows(result))
            if not result.get("hasMore"):
                return rows
            offset += int(result.get("count") or 0)

    def read_only_fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """The fetch under a ``READ ONLY`` transaction, all three in one request."""
        items = self._request(
            [("set transaction read only", None), (sql, params), ("rollback", None)],
            sql,
        )
        return _rows(items[1].get("resultSet") or {})

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        # ORDS commits a request that ends without an error, which is the
        # `commit()` the driver gateway issues after its statement.
        self._request([(sql, params)], sql)

    def fetch_clob(self, sql: str, params: Mapping[str, Any] | None = None) -> str:
        """Run a block whose OUT bind ``:result`` is a CLOB, as the driver does."""
        items = self._request([(sql, params)], sql, out_clob="result")
        for bind in items[-1].get("binds") or []:
            if bind.get("name") == "result":
                return str(bind.get("result") or "")
        return ""

    def run_script(self, text: str) -> str:
        """Post a whole SQL*Plus script as one request; return what it printed.

        For a script ADT did not write, an APEX export run back as a revert
        (ADT #1071): it is sent as it stands rather than split here, and ORDS
        reads it the SQL*Plus way. Any statement that failed raises, wherever it
        sits, since ORDS carries on past it.
        """
        answer = self._post(
            {"statementText": "\n".join([*self._prelude(), text.rstrip()])},
            exact_numbers = False,
        )
        items = list(answer.get("items") or [])
        _raise_first_error(items, text)
        return "\n".join(
            str(line).strip("\n") for item in items for line in item.get("response") or []
        )

    # `sqlcl_request` and `close` are inherited unchanged.

    # -- internals ---------------------------------------------------------

    def _request(
        self,
        statements: Sequence[Statement],
        sql: str,
        *,
        offset: int = 0,
        exact_numbers: bool = False,
        out_clob: str | None = None,
    ) -> list[dict[str, Any]]:
        """POST ``statements`` behind the session prelude; return their items."""
        body: dict[str, Any] = {
            "statementText": "\n".join(
                [*self._prelude(), *(_terminated(text) for text, _ in statements)]
            ),
            "binds": _binds(statements, out_clob),
        }
        if offset:
            body["offset"] = offset
        answer = self._post(body, exact_numbers=exact_numbers)
        items = list(answer.get("items") or [])
        _raise_first_error(items, sql)
        if len(items) < len(statements):
            error = OrdsRequestError(
                f"ORDS answered {len(items)} statement(s) for {len(statements)} sent."
            )
            _attach_sql(error, sql)
            raise error
        return items[len(items) - len(statements) :]

    def _prelude(self) -> list[str]:
        # Substitution off first, as on every SQLcl path (ADT #311): the script
        # is read the SQL*Plus way, so a `&` in a statement, which an APEX
        # import block carries in its URLs, otherwise swallows the rest of it.
        prelude = ["set define off", f"{DDL_LOCK_TIMEOUT_STATEMENT};"]
        if self.startup_sql:
            prelude.append(self.startup_sql.rstrip())
        return prelude

    def _post(self, body: Mapping[str, Any], *, exact_numbers: bool) -> dict[str, Any]:
        url = f"{self.connection.ords}/_/sql"
        request = Request(
            url,
            data    = json.dumps(body).encode("utf-8"),
            headers = {
                "Content-Type": "application/json",
                "Authorization": _basic_authorization(self.connection),
            },
            method  = "POST",
        )
        try:
            with self._opener(request, timeout=self.query_timeout_seconds) as response:
                raw = response.read()
        except HTTPError as error:
            message, steps = _http_failure(url, error.code)
            raise ConnectFailedError(message, steps=steps) from None
        except (URLError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise ConnectFailedError(
                f"ORDS DID NOT ANSWER\n\n{url}\n{reason}",
                steps=(
                    "1) check the ords URL in the connection file",
                    "2) check that ORDS is up and reachable from this machine",
                    "3) rerun",
                ),
            ) from None
        parse_float = Decimal if exact_numbers else float
        answer = json.loads(raw.decode("utf-8"), parse_float=parse_float)
        return answer if isinstance(answer, dict) else {}


def _raise_first_error(items: Sequence[Mapping[str, Any]], sql: str) -> None:
    for item in items:
        if item.get("errorCode") or item.get("errorMessage"):
            # A failed SQL statement carries `errorMessage`; a failed block
            # carries only `errorDetails`, the ORA stack as SQL*Plus prints it.
            error = OrdsRequestError(
                str(
                    item.get("errorMessage")
                    or item.get("errorDetails")
                    or f"ORA-{int(item['errorCode']):05d}"
                ).strip()
            )
            _attach_sql(error, sql)
            raise error


def _basic_authorization(connection: Connection) -> str:
    # The fifth approved `Secret.reveal()` site (ADT #1071): REST-enabled SQL
    # authenticates the database user with HTTP basic authentication, and the
    # header is the one place it can go. `shared/ords_url` refuses plain http off
    # loopback, so it never crosses a network in the clear.
    credential = f"{connection.username}:{connection.password.reveal() or ''}"
    return "Basic " + base64.b64encode(credential.encode("utf-8")).decode("ascii")


def _http_failure(url: str, status: int) -> tuple[str, tuple[str, ...]]:
    """The screen text and the steps for an HTTP refusal, by status."""
    if status == 401:
        return (
            f"ORDS REFUSED THE CREDENTIAL (HTTP 401)\n\n{url}",
            (
                "1) check user and pwd for this schema",
                "2) check that the schema is REST-enabled",
                "3) rerun",
            ),
        )
    if status == 404:
        return (
            f"ORDS HAS NO REST-ENABLED SQL HERE (HTTP 404)\n\n{url}",
            (
                "1) check the schema alias at the end of the ords URL",
                "2) set restEnabledSql.active to true on the ORDS pool",
                "3) rerun",
            ),
        )
    return (
        f"ORDS ANSWERED HTTP {status}\n\n{url}",
        ("1) read the ORDS log for this request", "2) rerun"),
    )


def _binds(statements: Sequence[Statement], out_clob: str | None) -> list[dict[str, Any]]:
    merged: dict[str, Any] = {}
    for _, params in statements:
        for name, value in (params or {}).items():
            if name in merged and merged[name] != value:
                raise OrdsRequestError(
                    f"bind {name!r} has two values in one request; one request "
                    "carries one set of binds."
                )
            merged[name] = value
    binds = [
        {"name": name, "data_type": _bind_type(name, value), "value": value}
        for name, value in merged.items()
    ]
    if out_clob is not None:
        binds.append({"name": out_clob, "data_type": "CLOB", "mode": "out"})
    return binds


def _bind_type(name: str, value: Any) -> str:
    if value is None or isinstance(value, str):
        return "VARCHAR2"
    if isinstance(value, bool):
        raise OrdsRequestError(
            f"bind {name!r} is a bool, which Oracle SQL has no type for. "
            "Bind the value the query actually compares against."
        )
    if isinstance(value, (int, float, Decimal)):
        return "NUMBER"
    raise OrdsRequestError(
        f"bind {name!r} is a {type(value).__name__}, which an ords connection "
        "cannot bind. Every ADT query binds a string or a number."
    )


def _rows(result: Mapping[str, Any]) -> list[dict[str, Any]]:
    columns = [
        (
            str(column.get("columnName")),
            str(column.get("jsonColumnName") or column.get("columnName")),
            str(column.get("columnTypeName") or ""),
        )
        for column in result.get("metadata") or []
    ]
    return [
        {name: _coerce(item.get(key), kind) for name, key, kind in columns}
        for item in result.get("items") or []
    ]


def _coerce(value: Any, column_type: str) -> Any:
    """Give a REST value the type python-oracledb would have returned."""
    if value is None:
        return None
    kind = column_type.upper()
    if kind == "DATE" or kind.startswith("TIMESTAMP"):
        return _datetime(str(value))
    if kind in _BINARY_TYPES:
        try:
            return base64.b64decode(str(value), validate=True)
        except (binascii.Error, ValueError):
            return value
    return value


def _datetime(text: str) -> Any:
    # ORDS renders the value with a trailing `Z`; python-oracledb hands back a
    # naive datetime, and the two gateways have to agree.
    plain = text.rstrip("Z")
    for shape in _DATE_SHAPES:
        try:
            return datetime.strptime(plain, shape)
        except ValueError:
            continue
    return text


__all__ = ["OrdsGateway", "OrdsRequestError"]
