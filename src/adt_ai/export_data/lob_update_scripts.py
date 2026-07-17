from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from adt_ai.export_data import queries
from adt_ai.export_data.inventory import DataColumn
from adt_ai.shared import text_files
from adt_ai.shared.row_values import row_value
from adt_ai.shared.sql_identifiers import safe_identifier

_BASE64_CHUNK_SIZE = 30000
_TEXT_DATA_TYPES = {"CLOB", "JSON", "XMLTYPE"}


def write_lob_update_script(
    folder: Path,
    table_name: str,
    row_key: str,
    row: dict[str, Any],
    key_columns: list[str],
    column: DataColumn,
    payload: str | bytes,
) -> str | None:
    if not key_columns:
        return None
    script_path = folder / f"{row_key}.{column.name.lower()}.sql"
    text_files.write_text(
        script_path,
        lob_update_sql(
            table_name  = table_name,
            column      = column,
            payload     = payload,
            row         = row,
            key_columns = key_columns,
        ),
    )
    return f"{folder.name}/{script_path.name}"


def lob_update_sql(
    table_name: str,
    column: DataColumn,
    payload: str | bytes,
    row: dict[str, Any],
    key_columns: list[str],
) -> str:
    data_type = column.data_type.upper()
    if data_type == "BLOB":
        return _blob_update_sql(table_name, column.name, payload, row, key_columns)
    return _text_update_sql(table_name, column.name, data_type, payload, row, key_columns)


def include_update_scripts(paths: list[str]) -> str:
    if not paths:
        return ""
    lines: list[str] = []
    for path in paths:
        lines.extend(["--", f"PROMPT {path}", f'@"./{path}";'])
    return "\n".join(lines) + "\n"


def _blob_update_sql(
    table_name: str,
    column_name: str,
    payload: str | bytes,
    row: dict[str, Any],
    key_columns: list[str],
) -> str:
    table = _sql_name(table_name)
    column = _sql_name(column_name)
    where = _where_clause(row, key_columns)
    writes = "\n".join(
        f"    {_raw_decode_line(chunk)}\n"
        "    DBMS_LOB.WRITEAPPEND(v_blob, UTL_RAW.LENGTH(v_raw), v_raw);"
        for chunk in _base64_chunks(payload)
    )
    return f"""DECLARE
    v_blob  BLOB;
    v_raw   RAW(32767);
BEGIN
    DBMS_LOB.CREATETEMPORARY(v_blob, TRUE);
{writes}
    --
    UPDATE {table}
    SET {column} = v_blob
    WHERE {where};
    --
    DBMS_LOB.FREETEMPORARY(v_blob);
END;
/
"""


def _text_update_sql(
    table_name: str,
    column_name: str,
    data_type: str,
    payload: str | bytes,
    row: dict[str, Any],
    key_columns: list[str],
) -> str:
    table = _sql_name(table_name)
    column = _sql_name(column_name)
    value = _text_assignment(column, data_type)
    where = _where_clause(row, key_columns)
    writes = "\n".join(
        f"    {_raw_decode_line(chunk)}\n"
        "    v_text := UTL_I18N.RAW_TO_CHAR(v_raw, 'AL32UTF8');\n"
        "    DBMS_LOB.WRITEAPPEND(v_clob, LENGTH(v_text), v_text);"
        for chunk in _base64_chunks(payload)
    )
    return f"""DECLARE
    v_clob  CLOB;
    v_raw   RAW(32767);
    v_text  VARCHAR2(32767);
BEGIN
    DBMS_LOB.CREATETEMPORARY(v_clob, TRUE);
{writes}
    --
    UPDATE {table}
    SET {value}
    WHERE {where};
    --
    DBMS_LOB.FREETEMPORARY(v_clob);
END;
/
"""


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


def _where_clause(row: dict[str, Any], key_columns: list[str]) -> str:
    return " AND ".join(
        _where_condition(column, row_value(row, column))
        for column in key_columns
    )


def _where_condition(column_name: str, value: Any) -> str:
    column = _sql_name(column_name)
    if value is None:
        return f"{column} IS NULL"
    return f"{column} = {queries.sql_value(value)}"


def _sql_name(name: str) -> str:
    safe_identifier(name, role="identifier")
    return name.lower()
