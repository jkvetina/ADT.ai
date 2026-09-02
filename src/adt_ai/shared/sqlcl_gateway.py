"""The gateway that never opens a python-oracledb session (ADT #396).

Jan, 2026-08-18: *"we can do a switch in config sqlcl_only (default false) and for
all queries we would use sqlcl, might be a bit slower, but for some customers
might be acceptable tradeof"*. SQLcl's own connection store already holds the
credential, so a run that never opens a driver session never needs the password
inside the Python process at all.

It is a second gateway behind the same four-method interface, built on
:class:`~adt_ai.shared.sqlcl_session.SqlclSession` so the session-scoped flows
(`export_apex`'s collection, `ut -coverage`'s profiler) keep working. Three
decisions are worth knowing before changing anything here, each measured on
2026-08-19 against `APPS@FREEPDB1` rather than assumed:

* **Rows come back as `SET SQLFORMAT JSON`**, which carries a `type` per column.
  That is what makes the rows type-faithful rather than "everything is a string":
  a `DATE` becomes a `datetime`, a `RAW` becomes `bytes`, and a `NUMBER` stays a
  number. Without the column types this gateway could not be a drop-in, because
  `patch/staleness.py` does arithmetic on a `LAST_DDL_TIME`.
* **LOBs need no encoding.** JSON renders a BLOB as full hex and a CLOB as full
  text inline: a 30000 byte BLOB came back as 60000 hex characters and a 40000
  character CLOB came back whole. The card's base64 design was written before that
  was measured and is not needed. Hex costs 100% inflation against base64's 33%,
  which is the only thing base64 would have bought.
* **Binds are real binds.** ADT binds only strings and integers (surveyed across
  all 69 `fetch_all` call sites), so each one is declared with `VAR` and assigned
  with `EXEC`, and the statement itself is handed to SQLcl untouched. Rewriting
  `:name` into a literal inside the SQL would have meant scanning it for string
  and comment spans, which is a scanner this module has no business owning.

`sqlcl_request` deliberately does NOT go through the session. Its callers
(`patch -deploy`, `diff`, `validate`, the REST export) write
`WHENEVER SQLERROR EXIT FAILURE` and expect a process that can end; routing them
here would let one of their scripts exit the gateway out from under the command.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.shared.connections import Connection
from adt_ai.shared.db import OracleGateway, _attach_sql
from adt_ai.shared.sqlcl_script_session import open_session
from adt_ai.shared.sqlcl_session import SqlclSessionError, error_in

# A statement that opens a PL/SQL block is terminated with `/` on its own line,
# everything else with `;`. Leading comments and blank lines are skipped, because
# every generated block in the tree carries them.
_BLOCK_START = re.compile(r"^\s*(declare|begin)\b", re.IGNORECASE)
_LEADING_NOISE = re.compile(r"\A(\s|--[^\n]*\n|/\*.*?\*/)*", re.DOTALL)

_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
)

_JSON_START_MARKER = "<<<ADT-SQLCL-JSON-START>>>"
_JSON_END_MARKER = "<<<ADT-SQLCL-JSON-END>>>"
_JSON_PAYLOAD_START = re.compile(r'^\{"results"\s*:', re.MULTILINE)

# VARCHAR2 reaches 32767 in PL/SQL, which is what a `VAR` declaration gets.
_VARCHAR_SIZE = 32767


class SqlclGateway(OracleGateway):
    """`QueryGateway` served entirely by SQLcl.

    It extends `OracleGateway` rather than standing beside it for one reason:
    `sqlcl_request` and its named-connection registration dance are already
    written, once, and `tests/contracts/test_gateway_startup_wiring.py` allows
    exactly one `OracleGateway(...)` construction in the tree. Building a private
    one here to borrow that method would have been a second place session setup
    could drift, which is the whole thing that contract exists to prevent.

    Inheriting brings `connect()` with it, and that method is the one thing this
    gateway must never do, so it is overridden to refuse by name. The guarantee
    the switch sells is that no database password enters a driver call; leaving a
    working `connect()` on the class would have left that resting on nobody
    calling it.
    """

    def __init__(
        self,
        connection: Connection,
        driver: Any | None = None,
        project_root: Path | None = None,
        startup_sql: str | None = None,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            connection,
            driver       = driver,
            project_root = project_root,
            startup_sql  = startup_sql,
            config       = config,
        )
        # The transport is a platform choice, not a gateway one (ADT #449):
        # POSIX drives one held-open SQLcl process, Windows runs one script per
        # request because SQLcl draws no prompt inside a Windows console. The
        # gateway only cares that both answer `run`, and passes on
        # `holds_a_session` so a session-scoped flow can ask before it relies on
        # one.
        self.session = open_session(
            connection,
            project_root = project_root,
            startup_sql  = startup_sql,
            config       = dict(config or {}),
        )

    @property
    def holds_a_session(self) -> bool:
        """Whether two calls through this gateway reach one database session.

        Forwarded from the transport rather than stored, so it can never fall out
        of step with the thing that actually decides it. `shared/session_scope`
        is the only reader, and `OracleGateway` needs no such property: a driver
        connection always holds a session, which is the default that helper takes.
        """
        return bool(getattr(self.session, "holds_a_session", True))

    def connect(self) -> Any:
        raise SqlclSessionError(
            "sqlcl_only is on, so this run never opens a python-oracledb session. "
            "Something asked the gateway for a driver connection directly instead "
            "of going through fetch_all, read_only_fetch_all or execute."
        )

    # -- QueryGateway ------------------------------------------------------

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return self._select(sql, params, prologue="", epilogue="")

    def read_only_fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """The same fetch under a read-only transaction (discovery safety layer 2).

        The two statements have to reach one session or the guard guards nothing,
        which is the property this gateway exists to preserve.
        """
        return self._select(
            sql,
            params,
            prologue = "set transaction read only;",
            epilogue = "rollback;",
        )

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        body = "\n".join(
            part
            for part in (
                _binds(params),
                _terminated(sql),
                "commit;",
            )
            if part
        )
        output = self._run(body, sql)
        _raise_on_error(output, sql)

    # `sqlcl_request` is inherited unchanged and deliberately keeps its own
    # process, see the module docstring.

    def close(self) -> None:
        self.session.close()

    # -- internals ---------------------------------------------------------

    def _select(
        self,
        sql: str,
        params: Mapping[str, Any] | None,
        *,
        prologue: str,
        epilogue: str,
    ) -> list[dict[str, Any]]:
        body = "\n".join(
            part
            for part in (
                prologue,
                "set sqlformat json",
                _binds(params),
                f"prompt {_JSON_START_MARKER}",
                _terminated(sql),
                f"prompt {_JSON_END_MARKER}",
                "set sqlformat default",
                epilogue,
            )
            if part
        )
        output = self._run(body, sql)
        _raise_on_error(output, sql)
        return _rows(output, sql)

    def _run(self, body: str, sql: str) -> str:
        try:
            return self.session.run(body)
        except SqlclSessionError as error:
            _attach_sql(error, sql)
            raise


def _terminated(sql: str) -> str:
    """Terminate a statement the way SQLcl needs it.

    A PL/SQL block keeps its own `END;` and gets `/` on the next line. Stripping
    that semicolon first, which is right for a SELECT, leaves `END` with no
    terminator and SQLcl runs nothing at all: measured as
    `dbms_session.set_identifier` silently not taking effect, with no error
    anywhere, which is the worst shape a transport bug can have.
    """
    statement = sql.rstrip()
    head = _LEADING_NOISE.sub("", statement)
    if _BLOCK_START.match(head):
        if not statement.endswith(";"):
            statement += ";"
        return f"{statement}\n/"
    return f"{statement.rstrip(';').rstrip()};"


def _binds(params: Mapping[str, Any] | None) -> str:
    if not params:
        return ""
    lines: list[str] = []
    for name, value in params.items():
        lines.append(f"var {name} {_bind_type(name, value)}")
        lines.append(f"exec :{name} := {_literal(name, value)}")
    return "\n".join(lines)


def _bind_type(name: str, value: Any) -> str:
    if value is None or isinstance(value, str):
        return f"varchar2({_VARCHAR_SIZE})"
    if isinstance(value, bool):
        raise SqlclSessionError(
            f"bind {name!r} is a bool, which Oracle SQL has no type for. "
            "Bind the value the query actually compares against."
        )
    if isinstance(value, (int, float)):
        return "number"
    raise SqlclSessionError(
        f"bind {name!r} is a {type(value).__name__}, which sqlcl_only cannot bind. "
        "Every ADT query binds a string or a number; a new shape needs a decision "
        "about how it crosses the text boundary."
    )


def _literal(name: str, value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    _bind_type(name, value)
    return repr(value)


def _raise_on_error(output: str, sql: str) -> None:
    message = error_in(output)
    if message is None:
        return
    error = SqlclSessionError(message)
    _attach_sql(error, sql)
    raise error


def _rows(output: str, sql: str) -> list[dict[str, Any]]:
    payload = _json_payload(output)
    if payload is None:
        error = SqlclSessionError(
            "SQLcl returned no result set for a query. Output was:\n"
            + output.strip()[:2000]
        )
        _attach_sql(error, sql)
        raise error
    rows: list[dict[str, Any]] = []
    for result in payload.get("results", []):
        columns = result.get("columns", [])
        names = [column["name"] for column in columns]
        types = {column["name"]: column.get("type", "") for column in columns}
        for item in result.get("items", []):
            lowered = {key.lower(): value for key, value in item.items()}
            # A NULL column is ABSENT from the item rather than present as null,
            # so the row is built from the declared columns and not from the keys
            # the item happens to carry.
            rows.append(
                {
                    name: _coerce(lowered.get(name.lower()), types[name])
                    for name in names
                }
            )
    return rows


def _json_payload(output: str) -> dict[str, Any] | None:
    framed = _between_markers(output, _JSON_START_MARKER, _JSON_END_MARKER)
    if framed is None:
        return None
    decoder = json.JSONDecoder()
    for match in _JSON_PAYLOAD_START.finditer(framed):
        start = match.start()
        try:
            payload, end = decoder.raw_decode(framed[start:])
        except ValueError:
            continue
        absolute_end = start + end
        if absolute_end < len(framed) and framed[absolute_end] not in "\r\n":
            continue
        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            return payload
    return None


def _between_markers(output: str, start_marker: str, end_marker: str) -> str | None:
    start = re.search(rf"(?m)^{re.escape(start_marker)}\r?$", output)
    if start is None:
        return None
    end = output.find(end_marker, start.end())
    if end < 0:
        return None
    return output[start.end() : end].lstrip("\r\n")


def _coerce(value: Any, column_type: str) -> Any:
    """Give a JSON value the type python-oracledb would have returned."""
    if value is None:
        return None
    kind = column_type.upper()
    if kind == "DATE":
        return _datetime(value, (_DATE_FORMAT,))
    if kind.startswith("TIMESTAMP"):
        return _datetime(value, _TIMESTAMP_FORMATS)
    if kind in {"RAW", "LONG RAW", "BLOB"}:
        try:
            return bytes.fromhex(str(value))
        except ValueError:
            return value
    return value


def _datetime(value: Any, formats: tuple[str, ...]) -> Any:
    text = str(value).strip()
    # A timestamp with a zone renders the region after the fraction; the zone is
    # dropped rather than parsed, because python-oracledb hands back a naive
    # datetime for these columns and the two gateways have to agree.
    head = text.split(" ")
    if len(head) > 2:
        text = " ".join(head[:2])
    for shape in formats:
        try:
            return datetime.strptime(text, shape)
        except ValueError:
            continue
    return value


__all__ = ["SqlclGateway"]
