from __future__ import annotations

import csv
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from adt_ai.export_data import queries
from adt_ai.export_data.inventory import DataColumn, DataDiscovery, DataTable
from adt_ai.export_data.lob_update_scripts import (
    include_update_scripts,
    write_lob_update_script,
)
from adt_ai.shared import text_files
from adt_ai.shared.config import (
    DEFAULT_PATH_OBJECTS,
    is_enabled,
    reject_unresolved_placeholders,
)
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.object_files import object_layouts
from adt_ai.shared.path_template import object_type_token, render_path_template
from adt_ai.shared.row_values import row_value
from adt_ai.shared.sql_like import split_patterns

_SIDE_CAR_DATA_TYPES = {
    "BLOB"   : "bin",
    "CLOB"   : "txt",
    "JSON"   : "json",
    "XMLTYPE": "xml",
}


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
            for table_name in discovery.table_names(
                names  = names,
                prefix = schema_export.get("prefix"),
                ignore = _split_patterns(schema_export.get("ignore")),
            ):
                export_items.append(
                    (discovery, DataTable(schema=schema, name=table_name, columns=[]))
                )
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
        table = (
            table
            if table.columns
            else DataTable(
                schema  = table.schema,
                name    = table.name,
                columns = discovery.columns(table.name),
            )
        )
        columns = [
            column
            for column in table.columns
            if column.name not in _ignored_columns(request.config)
        ]
        csv_columns = [
            column.name
            for column in columns
            if not _is_sidecar_column(column)
        ]
        sidecar_columns = [
            column
            for column in columns
            if _is_sidecar_column(column)
        ]
        query_columns = [column.name for column in columns]
        order_by = ", ".join(_key_columns(table, query_columns)) or "ROWID"
        where_filter = _where_filter(request.config, table.name, csv_columns)
        rows = discovery.rows(table.name, query_columns, where_filter, order_by)
        path = _data_path(request.root, request.config, table.name, table.schema)
        path.parent.mkdir(parents=True, exist_ok=True)
        if sidecar_columns:
            _clear_sidecar_folder(path.with_suffix(""))
        row_count = 0
        update_scripts: list[str] = []
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(
                handle,
                delimiter      = str(request.config.get("csv_delimiter") or ";"),
                lineterminator = text_files.configured_newline(),
                quoting        = csv.QUOTE_NONNUMERIC,
            )
            writer.writerow(csv_columns)
            sidecar_key_columns = _key_columns(table, query_columns)
            for row_number, row in enumerate(rows, start=1):
                writer.writerow([row_value(row, column) for column in csv_columns])
                update_scripts.extend(_write_sidecar_values(
                    path            = path,
                    table_name      = table.name,
                    row             = row,
                    row_number      = row_number,
                    key_columns     = sidecar_key_columns,
                    sidecar_columns = sidecar_columns,
                ))
                row_count += 1
        primary_columns = _key_columns(table, csv_columns)
        if primary_columns:
            merge_sql = _merge_sql_from_csv(
                path            = path,
                table_name      = table.name,
                primary_columns = primary_columns,
                config          = request.config,
                where_filter    = where_filter,
            )
            if merge_sql:
                text_files.write_text(
                    path.with_suffix(".sql"),
                    merge_sql + include_update_scripts(update_scripts),
                )
        return path, row_count


def _is_sidecar_column(column: DataColumn) -> bool:
    return _sidecar_extension(column) is not None


def _sidecar_extension(column: DataColumn) -> str | None:
    return _SIDE_CAR_DATA_TYPES.get(column.data_type.upper())


def _clear_sidecar_folder(folder: Path) -> None:
    if not folder.exists():
        return
    sidecar_suffixes = {
        f".{extension}"
        for extension in _SIDE_CAR_DATA_TYPES.values()
    }
    sidecar_suffixes.add(".sql")
    for file_path in folder.iterdir():
        if file_path.suffix not in sidecar_suffixes:
            continue
        if file_path.is_file():
            file_path.unlink()


def _write_sidecar_values(
    path: Path,
    table_name: str,
    row: dict[str, Any],
    row_number: int,
    key_columns: list[str],
    sidecar_columns: list[DataColumn],
) -> list[str]:
    folder_created = False
    row_key = _sidecar_row_key(row, key_columns, row_number)
    update_scripts: list[str] = []
    for column in sidecar_columns:
        value = row_value(row, column.name)
        payload = _sidecar_payload(value, column)
        if payload is None:
            continue
        folder = path.with_suffix("")
        if not folder_created:
            folder.mkdir(parents=True, exist_ok=True)
            folder_created = True
        extension = _sidecar_extension(column)
        if extension is None:
            continue
        file_path = folder / f"{row_key}.{column.name.lower()}.{extension}"
        if isinstance(payload, bytes):
            file_path.write_bytes(payload)
        else:
            # Raw LOB payload, byte-faithful: newline="" blocks the platform
            # translation so the sidecar mirrors the stored value exactly,
            # independent of the file_crlf setting.
            with file_path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(payload)
        update_script = write_lob_update_script(
            folder      = folder,
            table_name  = table_name,
            row_key     = row_key,
            row         = row,
            key_columns = key_columns,
            column      = column,
            payload     = payload,
        )
        if update_script:
            update_scripts.append(update_script)
    return update_scripts


def _sidecar_payload(value: Any, column: DataColumn) -> str | bytes | None:
    value = _read_lob_value(value)
    if value is None or value == "":
        return None
    if isinstance(value, bytes | bytearray | memoryview) and len(value) == 0:
        return None

    data_type = column.data_type.upper()
    if data_type == "BLOB":
        if isinstance(value, bytes | bytearray | memoryview):
            return bytes(value)
        return str(value).encode("utf-8")
    if data_type == "JSON" and not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("utf-8")
    return str(value)


def _read_lob_value(value: Any) -> Any:
    read = getattr(value, "read", None)
    if callable(read):
        return read()
    return value


def _sidecar_row_key(row: dict[str, Any], key_columns: list[str], row_number: int) -> str:
    if not key_columns:
        return f"row_{row_number:06d}"
    key = "__".join(
        _sidecar_name_part(row_value(row, column))
        for column in key_columns
    )
    return key or f"row_{row_number:06d}"


def _sidecar_name_part(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return "null"
    safe = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in text
    ).strip("._")
    return safe or "value"


def _data_path(root: Path, config: dict[str, Any], table_name: str, schema: str = "") -> Path:
    return _data_folder(root, config, schema) / f"{table_name.lower()}.csv"


DEFAULT_DATA_LAYOUT = ("data", ".sql")


def _data_layout(config: dict[str, Any]) -> tuple[str, str]:
    """The configured `object_types.DATA` folder and extension (ADT #474, row C).

    `object_types` accepts two spellings, the two-item list the shipped config
    uses and the `{folder, extension}` mapping, and both canonical readers take
    either. This module had a third reading that accepted only the list, in two
    predicates each carrying its own literal fallback, so a project spelling
    `DATA: {folder: csv_data/, extension: .dat}` had its data written to `data/`
    under `.sql` at exit `0` while `export_db` and `patch` read `csv_data/.dat`
    off the same key. `shared/object_files.object_layouts` is the reader now, and
    a row neither spelling can parse falls back here rather than raising mid
    export, which is the one thing this call site needs that `_parse_layout` does
    not offer.
    """
    return object_layouts(config.get("object_types")).get("DATA", DEFAULT_DATA_LAYOUT)


def _data_folder(root: Path, config: dict[str, Any], schema: str = "") -> Path:
    # Data lands beside its database objects, so both go through the one shared
    # renderer rather than two copies of the same substitution. Placeholders in
    # ``path_objects``:
    #   <schema>       schema / owner name, cased the way the token is spelled
    #   <object_type>  the DATA layout folder (e.g. data/); auto-appended if absent
    folder = _data_layout(config)[0]
    template = reject_unresolved_placeholders(
        str(config.get("path_objects") or DEFAULT_PATH_OBJECTS)
    )
    rendered = render_path_template(template, schema=schema or "", object_type=folder)
    if object_type_token(template):
        return root / Path(rendered.strip("/"))
    return root / Path(rendered.strip("/")) / folder


def _data_extension(config: dict[str, Any]) -> str:
    return _data_layout(config)[1]


def _existing_data_names(root: Path, config: dict[str, Any], schema: str = "") -> list[str]:
    data_folder = _data_folder(root, config, schema)
    extension = _data_extension(config)
    if not data_folder.exists():
        return []
    names: list[str] = []
    for file_path in sorted(data_folder.glob(f"*{extension}")):
        if not file_path.is_file():
            continue
        name = file_path.name
        # Guard the slice: name[:-len("")] would be name[:0] == "", collapsing
        # every recovered name. Only strip a non-empty extension that is present.
        if extension and name.endswith(extension):
            name = name[: -len(extension)]
        names.append(name.upper())
    return names


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
    skip_delete = "" if is_enabled(merge_config.get("delete"), default=False) else "--"
    skip_insert = "" if is_enabled(merge_config.get("insert"), default=True) else "--"
    skip_update = (
        ""
        if is_enabled(merge_config.get("update"), default=True) and update_columns
        else "--"
    )
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
    batch_size = int(config.get("merge_batch_size") or 10000)
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
            batch_index = index // batch_size
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


def _commented_where_filter(where_filter: str, skip_delete: str) -> str:
    if not where_filter or not skip_delete:
        return where_filter
    return ("\n" + skip_delete).join(where_filter.splitlines())


# One splitter with `export_db`, which read the same config key its own way
# (ADT #474). Re-exported under the old private name so no call site moved.
_split_patterns = split_patterns
