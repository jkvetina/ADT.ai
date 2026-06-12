"""Catalog reads and typed discovery results for the recompile module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adt_ai.db import QueryGateway
from adt_ai.recompile import queries


@dataclass(frozen=True)
class RecompileObject:
    object_type: str
    object_name: str


@dataclass(frozen=True)
class ObjectOverview:
    object_type: str
    total: int
    invalid: int
    missing_plscope_identifiers: int = 0
    missing_plscope_statements: int = 0


@dataclass(frozen=True)
class ObjectError:
    object_type: str
    object_name: str
    errors: int
    error: str | None


class RecompileDiscovery:
    OVERVIEW_QUERY = queries.OVERVIEW_QUERY
    OBJECTS_TO_RECOMPILE_QUERY = queries.OBJECTS_TO_RECOMPILE_QUERY
    ERRORS_SUMMARY_QUERY = queries.ERRORS_SUMMARY_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway

    def _scope_binds(
        self,
        *,
        object_name: str,
        object_type: str,
        prefix: str,
        ignore: str,
    ) -> dict[str, Any]:
        return {
            "object_name"    : object_name,
            "object_type"    : object_type,
            "objects_prefix" : prefix,
            "objects_ignore" : ignore,
        }

    def overview(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[ObjectOverview]:
        rows = self.gateway.fetch_all(
            self.OVERVIEW_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            ObjectOverview(
                str(row["OBJECT_TYPE"]),
                int(row["TOTAL"] or 0),
                int(row["INVALID"] or 0),
                int(row.get("MISSING_PLSCOPE_IDENTIFIERS") or 0),
                int(row.get("MISSING_PLSCOPE_STATEMENTS") or 0),
            )
            for row in rows
        ]

    def objects_to_recompile(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
        force: bool = False,
    ) -> list[RecompileObject]:
        binds = self._scope_binds(
            object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
        )
        binds["force"] = "Y" if force else ""
        rows = self.gateway.fetch_all(self.OBJECTS_TO_RECOMPILE_QUERY, binds)
        return [RecompileObject(str(row["OBJECT_TYPE"]), str(row["OBJECT_NAME"])) for row in rows]

    def errors_summary(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[ObjectError]:
        rows = self.gateway.fetch_all(
            self.ERRORS_SUMMARY_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            ObjectError(
                str(row["OBJECT_TYPE"]),
                str(row["OBJECT_NAME"]),
                int(row["ERRORS"] or 0),
                (str(row["ERROR"]) if row.get("ERROR") is not None else None),
            )
            for row in rows
        ]
