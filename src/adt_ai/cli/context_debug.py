from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from adt_ai.cli.constants import QueryGateway
from adt_ai.cli.context_errors import _display
from adt_ai.shared.announce import mark_announced

if TYPE_CHECKING:
    # The context this debug wrapper prints is defined in the module that
    # imports it, so the name is available to the checker only.
    from adt_ai.cli.context import StartupContext


class DebugQueryGateway:
    def __init__(self, wrapped: QueryGateway) -> None:
        self.wrapped = wrapped

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
        exact_numbers: bool = False,
    ) -> list[dict[str, object]]:
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        # Under -debug the statement itself is the announcement, and a better
        # one than any header: it says exactly what the wait is. It ends its own
        # line, so the cursor cannot tell, and it has to say so (`#360`).
        mark_announced()
        # Carried rather than swallowed (`#670`): a `-debug` export_data run has
        # to read the same values as a plain one. Forwarded only when the caller
        # asked, so a gateway that never hears of the keyword -- every partial
        # fake a command's own tests inject -- is called exactly as before.
        if not exact_numbers:
            return self.wrapped.fetch_all(sql, params)
        return self.wrapped.fetch_all(sql, params, exact_numbers=True)

    def read_only_fetch_all(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        """Log and delegate the discovery gateway's read-only query path."""
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        mark_announced()
        return self.wrapped.read_only_fetch_all(sql, params)

    def execute(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
    ) -> None:
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        # Under -debug the statement itself is the announcement, and a better
        # one than any header: it says exactly what the wait is. It ends its own
        # line, so the cursor cannot tell, and it has to say so (`#360`).
        mark_announced()
        self.wrapped.execute(sql, params)

    def sqlcl_request(
        self,
        request: str,
        root: Path,
        timeout_seconds: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        print()
        print("SQLCL REQUEST:")
        print(request)
        print()
        mark_announced()
        return self.wrapped.sqlcl_request(
            request, root, timeout_seconds=timeout_seconds, on_line=on_line
        )

    def close(self) -> None:
        self.wrapped.close()

def _print_startup_debug(context: StartupContext) -> None:
    print()
    print("STARTUP:")
    _print_debug_value("config_dirs", context.config_dirs)
    _print_debug_value("connection_files", context.connection_files)
    print()

def _print_debug_value(key: str, value: object) -> None:
    if value is None or value == "":
        return
    if isinstance(value, list):
        rendered = " | ".join(_display(item) for item in value)
    else:
        rendered = _display(value)
    if not rendered:
        return
    print(f"  {key:<18} {rendered}")

def _debug_sql(sql: str, params: Mapping[str, object]) -> str:
    rendered = sql.strip()
    for key in sorted(params, key=len, reverse=True):
        rendered = rendered.replace(f":{key}", _debug_value(params[key]))
    return rendered

def _debug_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return str(value)
