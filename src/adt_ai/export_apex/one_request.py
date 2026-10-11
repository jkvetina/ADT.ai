"""`export_apex` over a transport whose every request is its own session (ADT #1071).

The export opens an APEX session and a collection in one statement, fills the
collection in the next, and reads it back in a third. A driver session or a
held-open SQLcl process keeps the three together. An `ords:` connection cannot:
each REST-enabled SQL request is a database session of its own, so the
collection is gone before it is read. What it CAN do is send several
statements as one request (`OrdsGateway.fetch_all_after`), which is one session.

So for such a gateway the two statements that write the collection are held
back and sent ahead of the read that consumes them. The start statement is
kept for every later read of the same application, because each read is a new
session that needs the workspace and the collection again; a fill statement is
sent once, with the read that follows it. Everything else passes straight
through, which is every statement that does not name the collection.

A gateway that holds no session and cannot batch either is refused, as before
(`shared/session_scope`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adt_ai.shared.session_scope import batches_in_one_request, require_database_session

COLLECTION = "ADT_APEX_EXPORT"

Statement = tuple[str, Mapping[str, Any] | None]


def collection_gateway(gateway: Any, feature: str, start_sql: str) -> Any:
    """``gateway`` itself when it holds a session, else one that batches."""
    if isinstance(gateway, OneRequestCollection):
        return gateway
    if not batches_in_one_request(gateway):
        require_database_session(gateway, feature)
        return gateway
    return OneRequestCollection(gateway, start_sql)


class OneRequestCollection:
    """A gateway whose collection statements travel with the read of it."""

    holds_a_session = False

    def __init__(self, gateway: Any, start_sql: str) -> None:
        self.wrapped = gateway
        self._start_sql = start_sql
        self._start: Statement | None = None
        self._fill: Statement | None = None

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        if sql == self._start_sql:
            # A new application: its start replaces the last one's, and any
            # fill nobody read belongs to the application before it.
            self._start, self._fill = (sql, params), None
            return
        if COLLECTION in sql:
            self._fill = (sql, params)
            return
        self.wrapped.execute(sql, params)

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        exact_numbers: bool = False,
    ) -> list[dict[str, Any]]:
        if COLLECTION not in sql:
            if not exact_numbers:
                return list(self.wrapped.fetch_all(sql, params))
            return list(self.wrapped.fetch_all(sql, params, exact_numbers=True))
        setup = [statement for statement in (self._start, self._fill) if statement]
        self._fill = None
        return list(
            self.wrapped.fetch_all_after(setup, sql, params, exact_numbers=exact_numbers)
        )

    def __getattr__(self, name: str) -> object:
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return getattr(self.wrapped, name)


__all__ = ["COLLECTION", "OneRequestCollection", "collection_gateway"]
