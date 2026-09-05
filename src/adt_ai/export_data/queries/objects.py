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
    AND t.column_id         > 0
    AND t.virtual_column    = 'NO'  -- a virtual column HAS a column_id, so the line
                                    -- above never excluded one; its value is derived,
                                    -- and naming it in DML raises ORA-54013
    AND t.hidden_column     = 'NO'  -- ADT #695: an object column's attributes get
                                    -- their own SYS_NC00021$ rows, and they SHARE the
                                    -- object's column_id rather than carrying none, so
                                    -- the test above never excluded them either. They
                                    -- duplicate the column they belong to, DML cannot
                                    -- name them, and one of a geometry's is itself an
                                    -- object that exported as a memory address
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
    column_types: Mapping[str, str] | None = None,
) -> str:
    """`column_types` names the columns Oracle has to render for us (`#695`).

    A spatial column arrives over the wire as a driver object with no text form,
    and Python cannot make one: the coordinates, the SRID and the element
    structure are all inside a type only the database understands. So the
    conversion is asked of the database instead, in the SELECT itself, and what
    comes back is text like `SRID=4326;POINT (-73.87261 40.77725)`.

    Reaching `SDO_SRID` means naming an attribute of an object column, and
    Oracle requires a table alias for that, so the FROM grows one -- but only
    for a table that actually has such a column, so every other query is the
    plain shape it always was.
    """
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
    types = {
        str(name).upper(): str(data_type).strip().upper()
        for name, data_type in (column_types or {}).items()
    }
    spatial = {column for column in columns if types.get(column.upper()) in SPATIAL_TYPES}
    if not spatial:
        return f"SELECT {', '.join(columns)}\nFROM {table_name}{where_filter}\nORDER BY {order_by}"
    selected = ", ".join(
        _wkt_projection(column) if column in spatial else column
        for column in columns
    )
    return (
        f"SELECT {selected}\n"
        f"FROM {table_name} {SOURCE_ALIAS}{where_filter}\n"
        f"ORDER BY {order_by}"
    )


#: Every Oracle type this export renders through WKT rather than reading raw.
#: `user_tab_cols` spells it unqualified; the owner-qualified form is what the
#: driver puts in a `DbObject` repr, so both are accepted.
SPATIAL_TYPES = frozenset({"SDO_GEOMETRY", "MDSYS.SDO_GEOMETRY"})
#: The alias the spatial projection needs to reach an object attribute. Long
#: enough that it cannot be mistaken for one of the table's own columns.
SOURCE_ALIAS = "adt_src"


def _wkt_projection(column: str) -> str:
    """One SELECT item: the column's geometry as SRID-prefixed WKT, or NULL.

    The CASE is load-bearing. `SDO_UTIL.TO_WKTGEOMETRY(NULL)` is NULL, but the
    concatenation around it is not, so without the guard an empty geometry would
    export as the string `SRID=NULL;` and reload as a geometry that is not there.
    """
    reference = f"{SOURCE_ALIAS}.{column}"
    return (
        f"CASE WHEN {reference} IS NULL THEN NULL ELSE "
        f"'SRID=' || NVL(TO_CHAR({reference}.SDO_SRID), 'NULL') || ';' || "
        f"SDO_UTIL.TO_WKTGEOMETRY({reference}) END AS {column}"
    )


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
#: How a spatial cell carries its coordinate system, written by `_wkt_projection`
#: and read back by `_spatial_literal`. WKT alone has no room for an SRID, and a
#: geometry reloaded into the wrong one is silently in the wrong place on Earth.
_SRID_PREFIX = re.compile(r"^SRID=(\d+|NULL);", re.IGNORECASE)
#: An Oracle SQL text literal stops at 4000 bytes (32767 under EXTENDED, which
#: not every database is), and a polygon's WKT runs well past that. WKT is ASCII,
#: so a character is a byte and this bound needs no encoding allowance.
_WKT_CHUNK = 3000
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
    if data_type in SPATIAL_TYPES:
        return _spatial_literal(text)
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


def _spatial_literal(text: str) -> str:
    """The geometry constructor that reads one exported spatial cell back (`#695`).

    The cell is `SRID=<n|NULL>;<wkt>`, and Oracle's own two-argument constructor
    takes exactly those halves. A cell with no prefix is read as bare WKT with no
    coordinate system, which is what a hand-written row carries and what
    `SDO_UTIL.FROM_WKTGEOMETRY` would have produced anyway.

    A quoted string is never the answer here: it cannot be assigned to an
    SDO_GEOMETRY column at all, so a fallback to one would put a MERGE on disk
    that no database will run.
    """
    match = _SRID_PREFIX.match(text)
    if match:
        return f"SDO_GEOMETRY({_wkt_literal(text[match.end():])}, {match.group(1).upper()})"
    return f"SDO_GEOMETRY({_wkt_literal(text)}, NULL)"


def _wkt_literal(wkt: str) -> str:
    if len(wkt) <= _WKT_CHUNK:
        return _quoted(wkt)
    return " || ".join(
        f"TO_CLOB({_quoted(wkt[index:index + _WKT_CHUNK])})"
        for index in range(0, len(wkt), _WKT_CHUNK)
    )


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
