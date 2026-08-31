"""The LOB half of an `export_data` run: sidecar files and the folder they live in.

Split out of `export_data/runner.py` by card #593, which is the card that took
that file past the 20 000 byte context guard: the sidecar writers had to learn
what the export actually wrote so the folder could be pruned afterwards instead
of cleared before, and the project SOP's remedy for the guard is a bounded
module rather than a debt entry.

It is also the honest boundary. The runner discovers tables, builds the CSV and
the MERGE script; everything here is about one column value too large to sit in
a CSV cell, how it is named, how it lands byte-faithfully and what happens to
the file it left behind last time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adt_ai.export_data.inventory import DataColumn
from adt_ai.export_data.lob_update_scripts import write_lob_update_script
from adt_ai.shared import text_files
from adt_ai.shared.row_values import row_value

SIDE_CAR_DATA_TYPES = {
    "BLOB"   : "bin",
    "CLOB"   : "txt",
    "JSON"   : "json",
    "XMLTYPE": "xml",
}


def _is_sidecar_column(column: DataColumn) -> bool:
    return _sidecar_extension(column) is not None


def _sidecar_extension(column: DataColumn) -> str | None:
    return SIDE_CAR_DATA_TYPES.get(column.data_type.upper())


def _prune_sidecar_folder(folder: Path, written: set[Path]) -> None:
    """Drop the sidecars and LOB scripts this export did not write.

    A row dropped from the source table (or renumbered) must not leave its old
    files behind to be mistaken for still-current exported data. This used to
    clear the folder before the export wrote it back, which deleted every
    unchanged sidecar a moment before rewriting it (`#593`); running it
    afterwards, against what was actually written, reaches the same end state
    with the unchanged ones never touched.

    A file whose extension the sidecar layout does not own is still left alone:
    the folder belongs to the export, its stray neighbours do not.
    """
    if not folder.exists():
        return
    sidecar_suffixes = {
        f".{extension}"
        for extension in SIDE_CAR_DATA_TYPES.values()
    }
    sidecar_suffixes.add(".sql")
    for file_path in folder.iterdir():
        if file_path.suffix not in sidecar_suffixes or file_path in written:
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
    written: set[Path],
    sql_table_name: str | None = None,
) -> list[str]:
    """`sql_table_name` is how the generated UPDATE names the table.

    It carries the owner under `keep_owner` and is otherwise `table_name`; the
    unqualified `table_name` stays the one that names files and config lookups.
    """
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
        # Raw LOB payload, byte-faithful: the bytes go down exactly as stored,
        # so the sidecar mirrors the value independently of `file_crlf`. Through
        # the shared byte writer since `#593`, which adds the unchanged-skip and
        # changes nothing about which bytes land.
        text_files.write_bytes(
            file_path,
            payload if isinstance(payload, bytes) else payload.encode("utf-8"),
        )
        written.add(file_path)
        update_script = write_lob_update_script(
            folder      = folder,
            table_name  = sql_table_name or table_name,
            row_key     = row_key,
            row         = row,
            key_columns = key_columns,
            column      = column,
            payload     = payload,
        )
        if update_script:
            update_scripts.append(update_script)
            # The script's own path, read back off what the writer returned
            # rather than spelled a second time here: the relative form is
            # `<folder name>/<file name>`, so its parent is this folder's.
            written.add(folder.parent / update_script)
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
