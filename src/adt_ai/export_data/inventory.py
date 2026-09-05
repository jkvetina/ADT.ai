from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from adt_ai.export_data import queries
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.row_values import row_value


@dataclass(frozen=True)
class DataColumn:
    name     : str
    data_type: str
    pk       : int | None = None
    uq       : int | None = None
    #: `ALWAYS` or `BY DEFAULT` for an identity column, empty for every other
    #: column. An ALWAYS one may be exported and joined on but never named in an
    #: INSERT or an UPDATE SET, which raises ORA-32795 / ORA-54015 (`#670`).
    identity : str = ""


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
        return [
            DataTable(
                schema  = schema,
                name    = table_name,
                columns = self.columns(table_name),
            )
            for table_name in self.table_names(
                names  = names,
                prefix = prefix,
                ignore = ignore,
            )
        ]

    def table_names(
        self,
        names: list[str] | None = None,
        prefix: str | None = None,
        ignore: list[str] | None = None,
    ) -> list[str]:
        if names is not None and not names:
            return []
        rows = self.gateway.fetch_all(
            self.TABLES_QUERY,
            {
                "object_name"    : ",".join(names or ["%"]).upper(),
                "objects_prefix" : prefix or "%",
                "objects_ignore" : ",".join(ignore or []),
            },
        )
        return [_row_value(row, "OBJECT_NAME") for row in rows]

    def columns(self, table_name: str) -> list[DataColumn]:
        rows = self.gateway.fetch_all(self.COLUMNS_QUERY, {"table_name": table_name})
        return [
            DataColumn(
                name      = _row_value(row, "COLUMN_NAME"),
                data_type = _row_value(row, "DATA_TYPE"),
                pk        = _optional_int(row, "PK"),
                uq        = _optional_int(row, "UQ"),
                identity  = _row_value(row, "IDENTITY_GENERATION"),
            )
            for row in rows
        ]

    def rows(
        self,
        table_name: str,
        columns: list[str],
        where_filter: str,
        order_by: str,
        column_types: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        # `exact_numbers` (`#670`): the export writes the digits it read into a
        # CSV, so a NUMBER(p,s) arriving as the driver's default float loses
        # precision no later step can recover. Only the row fetch asks for it;
        # the dictionary queries above want ordinary ints.
        #
        # `column_types` (`#695`): a spatial column has no text form outside the
        # database, so the SELECT asks Oracle to render it rather than the driver
        # to hand it over.
        return self.gateway.fetch_all(
            self.data_query(table_name, columns, where_filter, order_by, column_types),
            exact_numbers = True,
        )

    @staticmethod
    def data_query(
        table_name: str,
        columns: list[str],
        where_filter: str,
        order_by: str,
        column_types: Mapping[str, str] | None = None,
    ) -> str:
        return queries.data_query(table_name, columns, where_filter, order_by, column_types)


def _row_value(row: dict[str, Any], key: str) -> str:
    return str(row_value(row, key) or "")


def _optional_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value is None:
        value = row.get(key.lower())
    return int(value) if value is not None else None
