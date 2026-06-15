from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from adt_ai.db import QueryGateway
from adt_ai.export_data import queries
from adt_ai.export_data.inventory import DataDiscovery, DataTable


@dataclass(frozen=True)
class ExportDataRequest:
    root         : Path
    schemas      : list[str]
    config       : dict[str, Any]
    schema_export: dict[str, dict[str, Any]] | None = None
    names        : list[str] | None = None
    reporter     : ExportDataReporter | None = None


GatewayFactory = Callable[[str], QueryGateway]


class ExportDataReporter(Protocol):
    def start_export(self, total: int) -> None:
        ...

    def export_table(self, table: DataTable) -> None:
        ...

    def finish_table(self, table: DataTable, row_count: int) -> None:
        ...

    def finish_export(self) -> None:
        ...


class ExportDataRunner:
    def __init__(self, gateway_factory: GatewayFactory) -> None:
        self.gateway_factory = gateway_factory

    def run(self, request: ExportDataRequest) -> list[Path]:
        written: list[Path] = []
        export_items: list[tuple[DataDiscovery, DataTable]] = []
        for schema in request.schemas:
            gateway = self.gateway_factory(schema)
            discovery = DataDiscovery(gateway)
            schema_export = (request.schema_export or {}).get(schema, {})
            # default the name list per schema – data folders may be schema-scoped
            names = request.names or _existing_data_names(request.root, request.config, schema)
            for table in discovery.tables(
                schema = schema,
                names  = names,
                prefix = schema_export.get("prefix"),
                ignore = _split_patterns(schema_export.get("ignore")),
            ):
                export_items.append((discovery, table))
        if request.reporter:
            request.reporter.start_export(len(export_items))
        for discovery, table in export_items:
            if request.reporter:
                request.reporter.export_table(table)
            path, row_count = self._write_table(request, discovery, table)
            written.append(path)
            if request.reporter:
                request.reporter.finish_table(table, row_count)
        if request.reporter:
            request.reporter.finish_export()
        return written

    def _write_table(
        self,
        request: ExportDataRequest,
        discovery: DataDiscovery,
        table: DataTable,
    ) -> tuple[Path, int]:
        columns = [
            column.name
            for column in table.columns
            if column.name not in _ignored_columns(request.config)
        ]
        order_by = ", ".join(_key_columns(table, columns)) or "ROWID"
        where_filter = _where_filter(request.config, table.name, columns)
        rows = discovery.rows(table.name, columns, where_filter, order_by)
        path = _data_path(request.root, request.config, table.name, table.schema)
        path.parent.mkdir(parents=True, exist_ok=True)
        row_count = 0
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(
                handle,
                delimiter      = str(request.config.get("csv_delimiter") or ";"),
                lineterminator = "\n",
                quoting        = csv.QUOTE_NONNUMERIC,
            )
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_row_value(row, column) for column in columns])
                row_count += 1
        primary_columns = _key_columns(table, columns)
        if primary_columns:
            merge_sql = _merge_sql_from_csv(
                path            = path,
                table_name      = table.name,
                primary_columns = primary_columns,
                config          = request.config,
                where_filter    = where_filter,
            )
            if merge_sql:
                path.with_suffix(".sql").write_text(merge_sql, encoding="utf-8")
        return path, row_count


def _data_path(root: Path, config: dict[str, Any], table_name: str, schema: str = "") -> Path:
    return _data_folder(root, config, schema) / f"{table_name.lower()}.csv"


def _data_folder(root: Path, config: dict[str, Any], schema: str = "") -> Path:
    # Mirrors export_db.files.ObjectFileResolver._folder_for so data lands beside
    # its database objects. Placeholders in ``path_objects``:
    #   <schema>       schema / owner name (lowercased)
    #   <object_type>  the DATA layout folder (e.g. data/); auto-appended if absent
    layout = (config.get("object_types") or {}).get("DATA", ["data", ".sql"])
    folder = str(layout[0]) if isinstance(layout, list | tuple) and layout else "data"
    folder = folder.strip("/")
    template = str(config.get("path_objects") or "database/<schema>/<object_type>")
    rendered = template.replace("<schema>", (schema or "").lower())
    if "<object_type>" in rendered:
        rendered = rendered.replace("<object_type>", folder)
        return root / Path(rendered.strip("/"))
    return root / Path(rendered.strip("/")) / folder


def _data_extension(config: dict[str, Any]) -> str:
    layout = (config.get("object_types") or {}).get("DATA", ["data", ".sql"])
    return str(layout[1]) if isinstance(layout, list | tuple) and len(layout) > 1 else ".sql"


def _existing_data_names(root: Path, config: dict[str, Any], schema: str = "") -> list[str]:
    data_folder = _data_folder(root, config, schema)
    extension = _data_extension(config)
    if not data_folder.exists():
        return []
    return [
        file_path.name[: -len(extension)].upper()
        for file_path in sorted(data_folder.glob(f"*{extension}"))
        if file_path.is_file()
    ]


def _ignored_columns(config: dict[str, Any]) -> set[str]:
    return {
        str(column)
        for column in config.get("ignored_columns", [])
    }


def _where_filter(config: dict[str, Any], table_name: str, columns: list[str]) -> str:
    where_columns: dict[str, Any] = {}
    tables_global = config.get("tables_global", {})
    if isinstance(tables_global, dict) and isinstance(tables_global.get("where"), dict):
        _merge_where(where_columns, tables_global["where"])
    table_config = _table_config(config, table_name)
    if isinstance(table_config, dict) and isinstance(table_config.get("where"), dict):
        _merge_where(where_columns, table_config["where"])

    present_columns = {column.upper() for column in columns}
    filtered = [
        (str(column_name), condition)
        for column_name, condition in where_columns.items()
        if str(column_name).upper() in present_columns
    ]
    if not filtered:
        return ""
    lines = ["", "WHERE 1 = 1"]
    lines.extend(
        f"    AND {column_name} {condition}"
        for column_name, condition in filtered
    )
    return "\n".join(lines)


def _merge_where(target: dict[str, Any], source: dict[str, Any]) -> None:
    for column_name, condition in source.items():
        column_key = str(column_name).upper()
        for existing in list(target):
            if existing.upper() == column_key:
                target.pop(existing)
        target[str(column_name)] = condition


def _key_columns(table: DataTable, columns: list[str]) -> list[str]:
    primary = sorted(
        (column.pk, column.name)
        for column in table.columns
        if column.pk is not None and column.name in columns
    )
    if primary:
        return [name for _, name in primary]
    unique = sorted(
        (column.uq, column.name)
        for column in table.columns
        if column.uq is not None and column.name in columns
    )
    return [name for _, name in unique]


def _row_value(row: dict[str, Any], column: str) -> Any:
    return row.get(column) if column in row else row.get(column.lower())


def _merge_sql_from_csv(
    path: Path,
    table_name: str,
    primary_columns: list[str],
    config: dict[str, Any],
    where_filter: str,
) -> str:
    columns, batches = _csv_select_batches(path, config)
    if not columns:
        return ""
    table = table_name.lower()
    lower_columns = [column.lower() for column in columns]
    lower_primary = [column.lower() for column in primary_columns]
    update_columns = [column for column in lower_columns if column not in lower_primary]
    merge_config = _merge_config(config, table_name)
    skip_delete = "" if _is_enabled(merge_config.get("delete"), default=False) else "--"
    skip_insert = "" if _is_enabled(merge_config.get("insert"), default=True) else "--"
    skip_update = "" if _is_enabled(merge_config.get("update"), default=True) and update_columns else "--"
    primary_join = "\n    " + "\n    AND ".join(
        f"t.{column} = s.{column}"
        for column in lower_primary
    ) + "\n"
    updates = queries.update_assignments(update_columns, skip_update)
    statements = []
    for batch in batches:
        statements.append(
            queries.merge_statement(
                table          = table,
                columns        = lower_columns,
                csv_selects    = batch,
                primary_join   = primary_join,
                updates        = updates,
                skip_delete    = skip_delete,
                skip_insert    = skip_insert,
                skip_update    = skip_update,
                where_filter   = _commented_where_filter(where_filter, skip_delete),
            )
        )
    return "".join(statements)


def _csv_select_batches(path: Path, config: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    columns: list[str] = []
    batches: list[list[str]] = []
    delimiter = str(config.get("csv_delimiter") or ";")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(
            handle,
            delimiter      = delimiter,
            lineterminator = "\n",
            quoting        = csv.QUOTE_NONNUMERIC,
        )
        for index, row in enumerate(reader):
            if not columns:
                columns = list(row)
            batch_index = index // 10000
            if batch_index == len(batches):
                batches.append([])
            batches[batch_index].append(queries.row_select(row, columns))
    return columns, batches


def _merge_config(config: dict[str, Any], table_name: str) -> dict[str, Any]:
    config_merged: dict[str, Any] = {}
    tables_global = config.get("tables_global", {})
    if isinstance(tables_global, dict) and isinstance(tables_global.get("merge"), dict):
        config_merged.update(tables_global["merge"])
    table_config = _table_config(config, table_name)
    if isinstance(table_config.get("merge"), dict):
        config_merged.update(table_config["merge"])
    return config_merged


def _table_config(config: dict[str, Any], table_name: str) -> dict[str, Any]:
    tables = config.get("tables", {})
    if not isinstance(tables, dict):
        return {}
    table_key = table_name.upper()
    for key, value in tables.items():
        if str(key).upper() == table_key and isinstance(value, dict):
            return value
    return {}


def _is_enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in {"1", "TRUE", "Y", "YES", "ON"}


def _commented_where_filter(where_filter: str, skip_delete: str) -> str:
    if not where_filter or not skip_delete:
        return where_filter
    return ("\n" + skip_delete).join(where_filter.splitlines())


def _split_patterns(value: object) -> list[str] | None:
    if not value:
        return None
    if isinstance(value, list | tuple):
        return [
            part.strip()
            for item in value
            for part in str(item).split(",")
            if part.strip()
        ]
    return [part.strip() for part in str(value).split(",") if part.strip()]
