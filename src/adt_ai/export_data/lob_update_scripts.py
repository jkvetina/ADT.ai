"""The per-row script that puts one LOB value back: base64 in the file, decoded by Oracle.

**Why `UTL_ENCODE` and not `apex_web_service.clobbase642blob`.** APEX ships a
one-call base64-to-BLOB converter, and it was the obvious candidate. It was not
picked because it makes a data reload depend on APEX being installed in the
target database, and `export_data` exports plain tables: a schema with no APEX,
or one whose APEX is a different release, still has to take these scripts. The
decode here uses only `UTL_ENCODE`, `UTL_RAW`, `UTL_I18N` and `DBMS_LOB`, which
every Oracle database carries. The price is the chunk loop below, since a RAW
holds 32767 bytes and a payload does not fit one. Keep it that way unless ADT
decides to require APEX on every target (`#811`).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.export_data import queries
from adt_ai.export_data.inventory import DataColumn
from adt_ai.shared import text_files
from adt_ai.shared.row_values import row_value
from adt_ai.shared.sql_identifiers import safe_identifier, safe_qualified_identifier

_BASE64_CHUNK_SIZE = 30000
#: The bytes one chunk decodes to, which is what has to fit `v_raw RAW(32767)`.
_DECODED_CHUNK_BYTES = _BASE64_CHUNK_SIZE // 4 * 3
_TEXT_DATA_TYPES = {"CLOB", "JSON", "XMLTYPE"}


def write_lob_update_script(
    folder: Path,
    table_name: str,
    row_key: str,
    row: dict[str, Any],
    key_columns: list[str],
    column: DataColumn,
    payload: str | bytes,
    column_types: Mapping[str, str] | None = None,
) -> str | None:
    if not key_columns:
        return None
    script_path = folder / f"{row_key}.{column.name.lower()}.sql"
    text_files.write_text(
        script_path,
        lob_update_sql(
            table_name   = table_name,
            column       = column,
            payload      = payload,
            row          = row,
            key_columns  = key_columns,
            column_types = column_types,
        ),
    )
    return f"{folder.name}/{script_path.name}"


def lob_update_sql(
    table_name: str,
    column: DataColumn,
    payload: str | bytes,
    row: dict[str, Any],
    key_columns: list[str],
    column_types: Mapping[str, str] | None = None,
) -> str:
    """`column_types` maps an upper-case column name to its Oracle data type.

    It types the key values the WHERE finds the row by, through the same
    renderer the MERGE loads that row with (`#923`). Untyped, a RAW(16)
    `SYS_GUID()` key was compared with Python's `b'...'` repr (ORA-01465) and a
    DATE key with a bare string only one NLS date format parses (ORA-01861), so
    every LOB script of such a table failed and its LOBs never reloaded.
    """
    data_type = column.data_type.upper()
    where = _where_clause(row, key_columns, column_types or {})
    if data_type == "BLOB":
        return _blob_update_sql(table_name, column.name, payload, where)
    return _text_update_sql(table_name, column.name, data_type, payload, where)


def include_update_scripts(paths: list[str]) -> str:
    """The MERGE's calls to its per-row LOB scripts, relative to the MERGE itself.

    `@@` rather than `@"./..."` (`#811`): a plain `@` resolves against SQLcl's
    working directory, so the MERGE only found its LOB scripts when it was run
    from its own folder, and a patch deploying it from a snapshot looked for them
    beside the patch instead.
    """
    if not paths:
        return ""
    lines: list[str] = []
    for path in paths:
        lines.extend(["--", f"PROMPT {path}", f'@@"{path}";'])
    return "\n".join(lines) + "\n"


def _blob_update_sql(
    table_name: str,
    column_name: str,
    payload: str | bytes,
    where: str,
) -> str:
    table = _sql_table_name(table_name)
    column = _sql_name(column_name)
    writes = "\n".join(
        f"    {_raw_decode_line(chunk)}\n"
        "    DBMS_LOB.WRITEAPPEND(v_blob, UTL_RAW.LENGTH(v_raw), v_raw);"
        for chunk in _base64_chunks(payload)
    )
    return queries.BLOB_UPDATE_BLOCK.format(writes=writes, table=table, column=column, where=where)


def _text_update_sql(
    table_name: str,
    column_name: str,
    data_type: str,
    payload: str | bytes,
    where: str,
) -> str:
    table = _sql_table_name(table_name)
    column = _sql_name(column_name)
    value = _text_assignment(column, data_type)
    writes = "\n".join(
        f"    {_raw_decode_line(chunk)}\n"
        "    v_text := UTL_I18N.RAW_TO_CHAR(v_raw, 'AL32UTF8');\n"
        "    DBMS_LOB.WRITEAPPEND(v_clob, LENGTH(v_text), v_text);"
        for chunk in _text_base64_chunks(payload)
    )
    return queries.CLOB_UPDATE_BLOCK.format(writes=writes, table=table, value=value, where=where)


def _text_assignment(column: str, data_type: str) -> str:
    if data_type == "XMLTYPE":
        return f"{column} = XMLTYPE(v_clob)"
    if data_type not in _TEXT_DATA_TYPES:
        return f"{column} = v_clob"
    return f"{column} = v_clob"


def _raw_decode_line(chunk: str) -> str:
    return f"v_raw := UTL_ENCODE.BASE64_DECODE(UTL_RAW.CAST_TO_RAW('{chunk}'));"


def _base64_chunks(payload: str | bytes) -> list[str]:
    payload_bytes = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    encoded = base64.b64encode(payload_bytes).decode("ascii")
    return [
        encoded[index:index + _BASE64_CHUNK_SIZE]
        for index in range(0, len(encoded), _BASE64_CHUNK_SIZE)
    ] or [""]


def _text_base64_chunks(payload: str | bytes) -> list[str]:
    """Base64 chunks that each decode to whole UTF-8 characters (`#811`).

    The text script decodes every chunk on its own with `UTL_I18N.RAW_TO_CHAR`,
    so a cut through a multi-byte character handed Oracle two halves that are
    each invalid, and the reload stored replacement glyphs where the letter was.
    The cut moves back off any continuation byte instead, which keeps every
    chunk within the same RAW limit and never splits a character.
    """
    data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    chunks: list[str] = []
    start = 0
    while start < len(data):
        end = min(start + _DECODED_CHUNK_BYTES, len(data))
        while start < end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        chunks.append(base64.b64encode(data[start:end]).decode("ascii"))
        start = end
    return chunks or [""]


def _where_clause(
    row: dict[str, Any],
    key_columns: list[str],
    column_types: Mapping[str, str],
) -> str:
    return " AND ".join(
        _where_condition(column, row_value(row, column), column_types.get(column.upper(), ""))
        for column in key_columns
    )


def _where_condition(column_name: str, value: Any, data_type: str) -> str:
    column = _sql_name(column_name)
    if value is None:
        return f"{column} IS NULL"
    return f"{column} = {queries.sql_value(value, data_type)}"


def _sql_name(name: str) -> str:
    safe_identifier(name, role="identifier")
    return name.lower()


def _sql_table_name(name: str) -> str:
    """The UPDATE target, which carries its owner when `keep_owner` is set.

    Separate from `_sql_name` on purpose: a column is never owner-qualified, so
    the looser guard applies to the table alone.
    """
    safe_qualified_identifier(name, role="table name")
    return name.lower()
