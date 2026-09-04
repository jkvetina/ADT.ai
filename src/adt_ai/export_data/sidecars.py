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

import hashlib
import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
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
    claimed_row_keys: dict[str, str],
    sql_table_name: str | None = None,
) -> list[str]:
    """`sql_table_name` is how the generated UPDATE names the table.

    It carries the owner under `keep_owner` and is otherwise `table_name`; the
    unqualified `table_name` stays the one that names files and config lookups.

    `claimed_row_keys` is the export's filename-collision ledger, shared across
    the rows of one table; see `_sidecar_row_key`.
    """
    folder_created = False
    row_key = _sidecar_row_key(row, key_columns, row_number, claimed_row_keys)
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
        return json.dumps(_json_ready(value), ensure_ascii=False, indent=2) + "\n"
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).decode("utf-8")
    return str(value)


def _json_ready(value: Any) -> Any:
    """Oracle's JSON scalars, rendered as JSON ones or not at all (`#674`).

    Oracle's JSON type stores more than JSON's own six types, and
    python-oracledb hands each one back as the Python object it really is, so
    `json.dumps` meets values it has no encoder for and raises. Every one of
    these crashed a whole `export_data` run against a real APEX schema, one
    after another, with no file written and no table named:

    * a **number** arrives as `Decimal`, because ADT asks for `fetch_decimals`,
      and 166 of the gallery schema's 384 tables carry a JSON or timestamp
      column. An integer goes out as `int`, exact at any size. A fraction has to
      become a `float`, the only unquoted number `json.dumps` writes, and that
      is lossy past a double's digits, so it is **verified rather than
      assumed**: a value that does not survive the round trip raises instead of
      writing a number that is quietly not the one in the database. Same stance
      as `_ExactNumber` in the runner, which keeps a NUMBER's digits in the CSV
      rather than letting `str()` shorten them;
    * **binary** arrives as `bytes` and is hex-encoded, the convention this
      module's CSV half already uses for RAW so `HEXTORAW` reads it back;
    * a **date, timestamp or interval** is written in ISO 8601, which is what
      Oracle's own JSON reader accepts back.

    Anything else raises and names its type. A silent `str()` fallback here
    would turn an unknown scalar into a quoted string that reloads as different
    data, which is the one outcome worse than the crash this replaces.
    """
    if isinstance(value, bool):
        # Before the Decimal test: a bool is an int in Python and JSON has its
        # own literal for it.
        return value
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        rendered = float(value)
        if Decimal(repr(rendered)) != value:
            raise ValueError(
                f"JSON number {value!r} cannot be written without losing digits"
            )
        return rendered
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex().upper()
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_ready(item) for item in value]
    if value is None or isinstance(value, str | int | float):
        return value
    raise ValueError(
        f"a JSON column holds a {type(value).__name__} this export cannot render"
    )


def _read_lob_value(value: Any) -> Any:
    read = getattr(value, "read", None)
    if callable(read):
        return read()
    return value


def _sidecar_row_key(
    row: dict[str, Any],
    key_columns: list[str],
    row_number: int,
    claimed: dict[str, str],
) -> str:
    """The filename stem for this row's sidecars, unique within the export.

    Every character a filename cannot carry folds to `_`, so distinct keys can
    fold to one name: `2024/001` and `2024_001` both become `2024_001`, the
    second row overwrote the first's sidecar, and both still got a `@` line in
    the MERGE script pointing at the survivor (ADT #654).

    `claimed` maps a folded name to the raw key that took it. The first row to
    fold to a name keeps it, so a repository whose keys never collide sees no
    churn; a later row folding to the same name is disambiguated by a short
    digest of its own raw key, which is stable across runs. Rows arrive ordered
    by the key columns (`runner.py` builds the ORDER BY), so "first" is the same
    row every time.

    The ledger is keyed case-folded (`#670`) because a filename collision is
    decided by the filesystem, not by Python: `ABC` and `abc` are two dict keys
    and one file on macOS and Windows, so without the fold the second row
    silently overwrote the first's sidecar and both rows still got a `@` line
    pointing at the survivor. Folding ALWAYS, rather than only on a
    case-insensitive host, keeps one export identical on every platform.
    """
    if not key_columns:
        return f"row_{row_number:06d}"
    raw = "__".join(str(row_value(row, column)) for column in key_columns)
    # Every part is non-empty (`_sidecar_name_part` falls back to "null"/"value"),
    # so a non-empty key column list always folds to a usable stem.
    folded = "__".join(
        _sidecar_name_part(row_value(row, column))
        for column in key_columns
    )
    owner = claimed.setdefault(folded.casefold(), raw)
    if owner == raw:
        return folded
    return f"{folded}__{_raw_key_digest(raw)}"


def _raw_key_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]


def _sidecar_name_part(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return "null"
    safe = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in text
    ).strip("._")
    return safe or "value"
