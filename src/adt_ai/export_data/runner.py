from __future__ import annotations

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from adt_ai.export_data import queries
from adt_ai.export_data.groups import GroupRules, group_for, resolve_data_group_rules
from adt_ai.export_data.inventory import DataColumn, DataDiscovery, DataTable
from adt_ai.export_data.lob_update_scripts import include_update_scripts
from adt_ai.export_data.sidecars import (  # noqa: F401  (re-exported for existing importers)
    SIDE_CAR_DATA_TYPES,
    _is_sidecar_column,
    _prune_sidecar_folder,
    _read_lob_value,
    _sidecar_extension,
    _sidecar_name_part,
    _sidecar_payload,
    _sidecar_row_key,
    _write_sidecar_values,
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
from adt_ai.shared.safe_paths import (
    simple_component,
    simple_oracle_identifier,
    simple_relative_path,
    under_root,
)
from adt_ai.shared.sql_like import split_patterns


@dataclass(frozen=True)
class ExportDataRequest:
    root         : Path
    schemas      : list[str]
    config       : dict[str, Any]
    schema_export: dict[str, dict[str, Any]] | None = None
    names        : list[str] | None = None
    reporter     : ExportDataReporter | None = None
    #: Seed rules (`config/groups.yaml`), merged with whatever `data/` already
    #: has learned about itself before each schema's export (ADT #520), same
    #: split as `export_db`'s own `_resolve_group_rules`.
    group_rules  : GroupRules | None = None


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
        group_rules_by_schema: dict[str, GroupRules] = {}
        for schema in request.schemas:
            gateway = self.gateway_factory(schema)
            discovery = DataDiscovery(gateway)
            schema_export = (request.schema_export or {}).get(schema, {})
            group_rules_by_schema[schema] = resolve_data_group_rules(
                _data_folder(request.root, request.config, schema), request.group_rules
            )
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
            path, row_count = self._write_table(
                request, discovery, table, group_rules_by_schema[table.schema]
            )
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
        group_rules: GroupRules | None = None,
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
        path = _data_path(request.root, request.config, table.name, table.schema, group_rules)
        path.parent.mkdir(parents=True, exist_ok=True)
        row_count = 0
        update_scripts: list[str] = []
        written_sidecars: set[Path] = set()
        # The CSV is built in memory and handed to the shared writer rather than
        # streamed straight at the file, so a table whose rows have not moved
        # keeps its mtime (`#593`). The rows are already a materialized list by
        # this point, so nothing is held that was not held before. `newline=""`
        # semantics are preserved by writing the buffer's bytes verbatim: a line
        # break inside a quoted value is data, and normalizing it would rewrite
        # what the column holds.
        buffer = io.StringIO(newline="")
        writer = csv.writer(
            buffer,
            delimiter      = str(request.config.get("csv_delimiter") or ";"),
            lineterminator = text_files.configured_newline(),
            quoting        = csv.QUOTE_NONNUMERIC,
        )
        writer.writerow(csv_columns)
        sidecar_key_columns = _key_columns(table, query_columns)
        sql_table_name = _sql_table_name(table.schema, table.name, request.config)
        # Folded filename -> the raw key that took it, for this table's export.
        claimed_row_keys: dict[str, str] = {}
        for row_number, row in enumerate(rows, start=1):
            writer.writerow([_csv_cell(row_value(row, column)) for column in csv_columns])
            update_scripts.extend(_write_sidecar_values(
                path             = path,
                table_name       = table.name,
                row              = row,
                row_number       = row_number,
                key_columns      = sidecar_key_columns,
                sidecar_columns  = sidecar_columns,
                written          = written_sidecars,
                sql_table_name   = sql_table_name,
                claimed_row_keys = claimed_row_keys,
            ))
            row_count += 1
        text_files.write_bytes(path, buffer.getvalue().encode("utf-8"))
        # A `where` predicate narrows what was SELECTed, so "not written this
        # run" stops meaning "gone from the table" and pruning would delete the
        # sidecars of rows that are still there (`#670`). The run cannot tell
        # the two apart, so under a filter it deletes nothing.
        if sidecar_columns and not where_filter:
            _prune_sidecar_folder(path.with_suffix(""), written_sidecars)
        primary_columns = _key_columns(table, csv_columns)
        if primary_columns:
            merge_sql = _merge_sql_from_csv(
                path            = path,
                table_name      = table.name,
                primary_columns = primary_columns,
                config          = request.config,
                where_filter    = where_filter,
                sql_table_name  = sql_table_name,
                null_safe_key   = not _has_primary_key(table, csv_columns),
                column_types    = {
                    column.name.upper(): column.data_type
                    for column in columns
                },
                identity_columns = _always_identity_columns(columns),
            )
            if merge_sql:
                text_files.write_text(
                    path.with_suffix(".sql"),
                    merge_sql + include_update_scripts(update_scripts),
                )
        return path, row_count


class _ExactNumber(Decimal):
    """A NUMBER the CSV writer prints in full rather than in exponent notation.

    `csv.QUOTE_NONNUMERIC` leaves a Decimal unquoted, which is what keeps a
    number a number in the file, and prints it with `str()`, which switches to
    `1E+3` once the exponent leaves a narrow window. The CSV is read back by
    the MERGE builder and by people, so the plain form is pinned here (`#670`).
    """

    def __str__(self) -> str:
        return format(self, "f")


def _csv_cell(value: Any) -> Any:
    """One CSV cell, for the two value types `csv.writer` renders wrongly (`#670`).

    A RAW arrives as `bytes`, whose `str()` is Python's `b'\\x01\\xffA'` repr, so
    it is hex-encoded the way `HEXTORAW` reads it back. A NUMBER arrives as a
    `Decimal` (see `shared/db.fetch_all`) and keeps every digit it was stored
    with, unquoted.
    """
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex().upper()
    if isinstance(value, Decimal):
        return _ExactNumber(value)
    return value


def _always_identity_columns(columns: list[DataColumn]) -> set[str]:
    """The lower-cased columns Oracle refuses to let DML write (`#670`)."""
    return {
        column.name.lower()
        for column in columns
        if column.identity.strip().upper() == "ALWAYS"
    }


def _sql_table_name(schema: str, table_name: str, config: dict[str, Any]) -> str:
    """How generated DML names this table: bare, or `owner.table` under `keep_owner`.

    One reader for both the MERGE/DELETE and the per-row LOB UPDATE, so the two
    files a table produces cannot end up naming it differently.
    """
    if schema and is_enabled(config.get("keep_owner", False)):
        return f"{schema}.{table_name}"
    return table_name


def _data_path(
    root: Path,
    config: dict[str, Any],
    table_name: str,
    schema: str = "",
    group_rules: GroupRules | None = None,
) -> Path:
    simple_oracle_identifier(table_name, role="table name")
    folder = _data_folder(root, config, schema)
    group = group_for("DATA", table_name, group_rules)
    if group:
        folder = folder / simple_component(group.upper(), role="group name")
    return under_root(root, folder / f"{table_name.lower()}.csv", role="data export path")


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
    if schema:
        simple_oracle_identifier(schema, role="schema name")
    rendered = render_path_template(template, schema=schema or "", object_type=folder)
    if object_type_token(template):
        relative = simple_relative_path(rendered, role="path_objects")
    else:
        relative = simple_relative_path(rendered, role="path_objects") / simple_relative_path(
            folder, role="data folder"
        )
    return under_root(root, root / relative, role="data folder")


def _data_extension(config: dict[str, Any]) -> str:
    return _data_layout(config)[1]




def _existing_data_names(root: Path, config: dict[str, Any], schema: str = "") -> list[str]:
    data_folder = _data_folder(root, config, schema)
    extension = _data_extension(config)
    if not data_folder.exists():
        return []
    names: list[str] = []
    # Flat, then exactly one level down: a table `-groups -force` (or a hand
    # arrangement) already moved into `data/<GROUP>/` is still a previously
    # exported table, and a bare re-export with no `-name` has to find it the
    # same way `export_db`'s own discovery retries one level for a grouped
    # object (ADT #498, #520).
    candidates = sorted(data_folder.glob(f"*{extension}")) + sorted(
        data_folder.glob(f"*/*{extension}")
    )
    for file_path in candidates:
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


def _has_primary_key(table: DataTable, columns: list[str]) -> bool:
    """Whether `_key_columns` answered with a primary key rather than the UQ fallback.

    A UNIQUE constraint permits NULLs and a primary key does not, which is what
    decides whether the MERGE join has to be NULL-safe (`#670`).
    """
    return any(
        column.pk is not None and column.name in columns
        for column in table.columns
    )


def _merge_sql_from_csv(
    path: Path,
    table_name: str,
    primary_columns: list[str],
    config: dict[str, Any],
    where_filter: str,
    sql_table_name: str | None = None,
    null_safe_key: bool = False,
    column_types: dict[str, str] | None = None,
    identity_columns: set[str] | None = None,
) -> str:
    """`column_types` types the CSV cells; `identity_columns` are the unwritable ones.

    Both arrive from the table's own inventory (`#670`). Without them every cell
    is rendered as a text literal and no column is excluded, which is the shape
    a caller with no inventory in hand gets.
    """
    columns, batches = _csv_select_batches(path, config, column_types)
    if not columns:
        return ""
    # The MERGE/DELETE target carries the owner under `keep_owner`; every config
    # lookup below still keys off the unqualified `table_name`.
    table = (sql_table_name or table_name).lower()
    lower_columns = [column.lower() for column in columns]
    lower_primary = [column.lower() for column in primary_columns]
    # An ALWAYS identity column is exported and may be the key, but Oracle
    # refuses an INSERT that names it (ORA-32795) and an UPDATE that sets it.
    identity = identity_columns or set()
    update_columns = [
        column
        for column in lower_columns
        if column not in lower_primary and column not in identity
    ]
    insert_columns = [column for column in lower_columns if column not in identity]
    merge_config = _merge_config(config, table_name)
    skip_delete = "" if is_enabled(merge_config.get("delete"), default=False) else "--"
    skip_insert = (
        ""
        if is_enabled(merge_config.get("insert"), default=True) and insert_columns
        else "--"
    )
    skip_update = (
        ""
        if is_enabled(merge_config.get("update"), default=True) and update_columns
        else "--"
    )
    primary_join = "\n    " + "\n    AND ".join(
        _join_predicate(column, null_safe_key)
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
                insert_columns = insert_columns,
            )
        )
    return "".join(statements)


def _join_predicate(column: str, null_safe: bool) -> str:
    """One `ON` comparison, NULL-safe when the key came from the UNIQUE fallback.

    A UNIQUE constraint permits NULLs, and `t.c = s.c` is never TRUE for one, so
    every NULL-keyed row failed to match and was inserted again on each replay
    (`#670`). A primary key cannot be NULL, so it keeps the plain equality and
    the index path that comes with it.
    """
    if not null_safe:
        return f"t.{column} = s.{column}"
    return f"(t.{column} = s.{column} OR (t.{column} IS NULL AND s.{column} IS NULL))"


def _csv_select_batches(
    path: Path,
    config: dict[str, Any],
    column_types: dict[str, str] | None = None,
) -> tuple[list[str], list[list[str]]]:
    """Read the CSV back as text and let the destination column type shape each literal.

    This read used to pass `csv.QUOTE_NONNUMERIC`, which casts every unquoted
    field through `float()` (`#670`): 9007199254740993 came back as
    9007199254740992.0 and an exported `1` was replayed as `1.0`. The column's
    Oracle type is already known here, so it decides numeric-versus-text rather
    than the parser guessing from the quoting.
    """
    columns: list[str] = []
    batches: list[list[str]] = []
    delimiter = str(config.get("csv_delimiter") or ";")
    batch_size = int(config.get("merge_batch_size") or 10000)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(
            handle,
            delimiter      = delimiter,
            lineterminator = "\n",
        )
        for index, row in enumerate(reader):
            if not columns:
                columns = list(row)
            batch_index = index // batch_size
            if batch_index == len(batches):
                batches.append([])
            batches[batch_index].append(queries.row_select(row, columns, column_types))
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
