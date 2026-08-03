"""Shared Oracle session settings and error classifiers."""

from __future__ import annotations

DDL_LOCK_TIMEOUT_SECONDS = 10


def ddl_lock_timeout_statement(seconds: int = DDL_LOCK_TIMEOUT_SECONDS) -> str:
    return f"ALTER SESSION SET DDL_LOCK_TIMEOUT = {seconds}"


DDL_LOCK_TIMEOUT_STATEMENT = ddl_lock_timeout_statement()


def is_ddl_lock_timeout(error: BaseException) -> bool:
    return "ORA-04021" in str(error)
