"""Shared Oracle session settings and error classifiers."""

from __future__ import annotations

from typing import Any, Protocol


class ExecutableGateway(Protocol):
    def execute(self, sql: str, params: Any | None = None) -> None:
        ...

DDL_LOCK_TIMEOUT_SECONDS = 10


def ddl_lock_timeout_statement(seconds: int = DDL_LOCK_TIMEOUT_SECONDS) -> str:
    return f"ALTER SESSION SET DDL_LOCK_TIMEOUT = {seconds}"


DDL_LOCK_TIMEOUT_STATEMENT = ddl_lock_timeout_statement()


def set_ddl_lock_timeout(
    gateway: ExecutableGateway,
    seconds: int = DDL_LOCK_TIMEOUT_SECONDS,
) -> None:
    gateway.execute(ddl_lock_timeout_statement(seconds))


def is_ddl_lock_timeout(error: BaseException) -> bool:
    return "ORA-04021" in str(error)
