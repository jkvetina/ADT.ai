"""The in-memory `QueryGateway`, split out of `shared/db.py` by card `#670`.

That file took the 24 KB context guard when the export's exact-number fetch
landed on it, and the seam was already there to be cut: everything else in
`db.py` opens a real Oracle session, resolves a wallet, or shells out to SQLcl,
and this one class talks to a dictionary. It answers the same five methods for
callers that must not touch a database, which is why it ships as source rather
than as a test helper: `tests/`, the demo fixtures and the doctor's dry runs all
hand it to production code paths.

`from adt_ai.shared.db import FakeGateway` still works and is the spelling
everything uses; `db.py` re-exports it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


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
        #: The queries that asked for exact NUMBER values (`#670`), so a test can
        #: pin WHICH fetch opts in rather than only that the keyword exists.
        self.exact_number_queries: list[str] = []
        self.closed = False

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, Any] | None = None,
        exact_numbers: bool = False,
    ) -> list[dict[str, Any]]:
        self.queries.append((sql, dict(params or {})))
        if exact_numbers:
            self.exact_number_queries.append(sql)
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
