from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from adt_ai.shared.sql_identifiers import (
    safe_identifier,
    safe_identifiers,
    safe_qualified_identifier,
)

TABLES_QUERY = """
WITH objects_prefix AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 1) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_prefix), ',')) t
),
objects_names AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :object_name), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
)
SELECT
    o.object_name
FROM user_objects o
JOIN objects_prefix p
    ON o.object_name LIKE p.object_like ESCAPE '\\'
JOIN objects_names n
    ON o.object_name LIKE n.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON o.object_name LIKE g.object_like ESCAPE '\\'
WHERE o.object_type = 'TABLE'
    AND g.object_like IS NULL
ORDER BY o.object_name
""".strip()

COLUMNS_QUERY = """
SELECT
    t.column_name,
    t.data_type,
    t.column_id,
    MIN(CASE WHEN n.constraint_name IS NOT NULL THEN c.position END) AS pk,
    MIN(CASE WHEN u.constraint_name IS NOT NULL THEN c.position END) AS uq,
    -- ADT #670: an INSERT that names a GENERATED ALWAYS identity column raises
    -- ORA-32795 and MERGE has no OVERRIDING SYSTEM VALUE to excuse it, so the
    -- generated statement has to know which columns those are
    MIN(i.generation_type) AS identity_generation
FROM user_tab_cols t
LEFT JOIN user_cons_columns c
    ON c.table_name         = t.table_name
    AND c.column_name       = t.column_name
LEFT JOIN user_constraints n
    ON n.table_name         = c.table_name
    AND n.constraint_name   = c.constraint_name
    AND n.constraint_type   = 'P'
LEFT JOIN (
    SELECT MIN(u.constraint_name) AS constraint_name
    FROM user_constraints u
    WHERE u.table_name          = UPPER(:table_name)
        AND u.constraint_type   = 'U'
) u
    ON u.constraint_name    = c.constraint_name
LEFT JOIN user_tab_identity_cols i
    ON i.table_name         = t.table_name
    AND i.column_name       = t.column_name
WHERE t.table_name          = UPPER(:table_name)
    AND t.column_id         > 0     -- ignore hidden columns, whose column_id is NULL
    AND t.virtual_column    = 'NO'  -- a virtual column HAS a column_id, so the line
                                    -- above never excluded one; its value is derived,
                                    -- and naming it in DML raises ORA-54013
GROUP BY
    t.column_name,
    t.data_type,
    t.column_id
ORDER BY
    t.column_id
""".strip()


def data_query(
    table_name: str,
    columns: list[str],
    where_filter: str,
    order_by: str,
) -> str:
    safe_identifier(table_name, role="table name")
    safe_identifiers(columns, role="column name")
    safe_identifiers(
        [
            part.strip()
            for part in order_by.split(",")
            if part.strip() and part.strip().upper() != "ROWID"
        ],
        role="order by column",
    )
    return f"SELECT {', '.join(columns)}\nFROM {table_name}{where_filter}\nORDER BY {order_by}"


def update_assignments(columns: list[str], skip_update: str) -> str:
    if not columns:
        return ""
    safe_identifiers(columns, role="column name")
    width = max(len(f"t.{column}") for column in columns)
    return f",\n{skip_update}        ".join(
        f"{f't.{column}':<{width}} = s.{column}"
        for column in columns
    )


def merge_statement(
    table: str,
    columns: list[str],
    csv_selects: list[str],
    primary_join: str,
    updates: str,
    skip_delete: str,
    skip_insert: str,
    skip_update: str,
    where_filter: str,
    insert_columns: list[str] | None = None,
) -> str:
    """`insert_columns` narrows the INSERT list; `columns` still drives the SELECT.

    A GENERATED ALWAYS identity column may not be named in an INSERT (ORA-32795,
    `#670`), so it is exported and joined on but never inserted. An empty list
    falls back to every column, which only happens when the INSERT is commented
    out anyway.
    """
    safe_qualified_identifier(table, role="table name")
    safe_identifiers(columns, role="column name")
    inserted = insert_columns or columns
    all_columns = "t." + ",\n        t.".join(inserted)
    all_values = "s." + ",\n        s.".join(inserted)
    csv_content = " UNION ALL\n    ".join(csv_selects)
    return f"""BEGIN
    DBMS_OUTPUT.PUT_LINE('--');
    DBMS_OUTPUT.PUT_LINE('-- MERGE ' || UPPER('{table}'));
    DBMS_OUTPUT.PUT_LINE('--');
END;
/
--
{skip_delete}DELETE FROM {table}{where_filter};
--
MERGE INTO {table} t
USING (
    {csv_content}
) s
ON ({primary_join})
{skip_update}WHEN MATCHED THEN
{skip_update}    UPDATE SET
{skip_update}        {updates}
{skip_insert}WHEN NOT MATCHED THEN
{skip_insert}    INSERT (
{skip_insert}        {all_columns}
{skip_insert}    )
{skip_insert}    VALUES (
{skip_insert}        {all_values}
{skip_insert}    );
--
COMMIT;
"""


def row_select(
    row: dict[str, Any],
    columns: list[str],
    column_types: Mapping[str, str] | None = None,
) -> str:
    """`column_types` maps an upper-case column name to its Oracle data type.

    The MERGE path reads its values back out of the CSV, where every cell is
    text, so the destination column's type is the only thing that can say
    whether a cell is a number, a RAW or a date (`#670`). Omitting it renders
    each value by its Python type instead, which is what a caller holding the
    driver's own row (`lob_update_scripts`) wants.
    """
    safe_identifiers(columns, role="column name")
    types = column_types or {}
    values = [
        f"{sql_value(row[column], types.get(column.upper(), ''))} AS {column}"
        for column in columns
    ]
    return f"SELECT {', '.join(values)} FROM DUAL"


#: `HEXTORAW` takes an even number of hex digits and nothing else.
_HEX_DIGITS = frozenset("0123456789ABCDEFabcdef")
_RAW_TYPES = frozenset({"RAW", "LONG RAW"})
#: Every Oracle type whose CSV text is a number literal Oracle can read as it is.
_NUMBER_TYPES = frozenset({
    "BINARY_DOUBLE",
    "BINARY_FLOAT",
    "DEC",
    "DECIMAL",
    "FLOAT",
    "INT",
    "INTEGER",
    "NUMBER",
    "NUMERIC",
    "SMALLINT",
})
_NUMBER_TEXT = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
#: The mask both sides of the export agree on. `row_value` writes a datetime with
#: `str()`, which is this shape, so the literal and the mask cannot drift.
_DATE_MASK = "YYYY-MM-DD HH24:MI:SS"


def sql_value(value: Any, data_type: str = "") -> str:
    """The SQL literal for one exported value.

    With `data_type`, the value is CSV text and the column's Oracle type decides
    the literal: a RAW is hex that `HEXTORAW` decodes, a NUMBER stays unquoted
    with every digit it was exported with, and a DATE or TIMESTAMP carries its
    own conversion so it does not depend on the session's NLS formats, which
    default to `DD-MON-RR` and raise ORA-01861 against an ISO string (`#670`).

    Without it the value is rendered by its Python type, for a caller that holds
    the driver's own row rather than a cell read back off disk.
    """
    if value is None or value == "":
        return "NULL"
    if data_type:
        return _typed_literal(str(value), data_type.strip().upper())
    if isinstance(value, int | float | Decimal):
        return str(value)
    return _quoted(str(value))


def _typed_literal(text: str, data_type: str) -> str:
    if data_type in _RAW_TYPES and _is_hex(text):
        return f"HEXTORAW('{text}')"
    if data_type in _NUMBER_TYPES and _NUMBER_TEXT.match(text):
        return text
    if data_type == "DATE" or data_type.startswith("TIMESTAMP"):
        return _temporal_literal(text, data_type)
    return _quoted(text)


def _temporal_literal(text: str, data_type: str) -> str:
    """A DATE/TIMESTAMP literal with the conversion that reads it back.

    The offset on the parsed value, not the column type, decides between
    `TO_TIMESTAMP_TZ` and `TO_TIMESTAMP`: the driver hands a TIMESTAMP WITH
    LOCAL TIME ZONE back in the session zone with no offset attached, and Oracle
    normalizes a plain TIMESTAMP into that column on assignment.
    """
    moment = _parsed_moment(text)
    if moment is None:
        return _quoted(text)
    if data_type == "DATE":
        return f"TO_DATE('{moment:%Y-%m-%d %H:%M:%S}', '{_DATE_MASK}')"
    stamp = f"{moment:%Y-%m-%d %H:%M:%S.%f}"
    offset = moment.utcoffset()
    if offset is None:
        return f"TO_TIMESTAMP('{stamp}', '{_DATE_MASK}.FF6')"
    return f"TO_TIMESTAMP_TZ('{stamp} {_utc_offset(offset)}', '{_DATE_MASK}.FF6 TZH:TZM')"


def _parsed_moment(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _utc_offset(offset: timedelta) -> str:
    minutes = int(offset.total_seconds()) // 60
    sign = "-" if minutes < 0 else "+"
    minutes = abs(minutes)
    return f"{sign}{minutes // 60:02d}:{minutes % 60:02d}"


def _is_hex(text: str) -> bool:
    return len(text) % 2 == 0 and set(text) <= _HEX_DIGITS


def _quoted(text: str) -> str:
    escaped = text.replace("'", "''")
    return f"'{escaped}'"
