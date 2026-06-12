from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adt_ai.db import QueryGateway
from adt_ai.export_data import queries


@dataclass(frozen=True)
class DataColumn:
    name     : str
    data_type: str
    pk       : int | None = None
    uq       : int | None = None


@dataclass(frozen=True)
class DataTable:
    schema : str
    name   : str
    columns: list[DataColumn]


class DataDiscovery:
    TABLES_QUERY = queries.TABLES_QUERY
    COLUMNS_QUERY = queries.COLUMNS_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway

    def tables(
        self,
        schema: str,
        names: list[str] | None = None,
        prefix: str | None = None,
        ignore: list[str] | None = None,
    ) -> list[DataTable]:
        rows = self.gateway.fetch_all(
            self.TABLES_QUERY,
            {
                "object_name"    : ",".join(names or ["%"]).upper(),
                "objects_prefix" : prefix or "%",
                "objects_ignore" : ",".join(ignore or []),
            },
        )
        return [
            DataTable(
                schema  = schema,
                name    = _row_value(row, "OBJECT_NAME"),
                columns = self.columns(_row_value(row, "OBJECT_NAME")),
            )
            for row in rows
        ]

    def columns(self, table_name: str) -> list[DataColumn]:
        rows = self.gateway.fetch_all(self.COLUMNS_QUERY, {"table_name": table_name})
        return [
            DataColumn(
                name      = _row_value(row, "COLUMN_NAME"),
                data_type = _row_value(row, "DATA_TYPE"),
                pk        = _optional_int(row, "PK"),
                uq        = _optional_int(row, "UQ"),
            )
            for row in rows
        ]

    def rows(
        self,
        table_name: str,
        columns: list[str],
        where_filter: str,
        order_by: str,
    ) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(
            self.data_query(table_name, columns, where_filter, order_by),
        )

    @staticmethod
    def data_query(
        table_name: str,
        columns: list[str],
        where_filter: str,
        order_by: str,
    ) -> str:
        return queries.data_query(table_name, columns, where_filter, order_by)


def _row_value(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or row.get(key.lower()) or "")


def _optional_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        value = row.get(key.lower())
    return int(value) if value is not None else None
