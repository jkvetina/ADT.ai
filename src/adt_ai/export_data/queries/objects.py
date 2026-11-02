from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from adt_ai.export_data.intervals import ds_interval_text, is_ym_interval, ym_interval_text
from adt_ai.shared.sql_identifiers import (
    column_type,
    safe_qualified_identifier,
    sql_name,
    sql_names,
    sql_qualified_name,
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
    -- ADT #1096: a table created quoted in lower case is still found by the
    -- unquoted name a user types
    ON UPPER(o.object_name) LIKE n.object_like ESCAPE '\\'
LEFT JOIN objects_ignore g
    ON o.object_name LIKE g.object_like ESCAPE '\\'
WHERE o.object_type = 'TABLE'
    AND g.object_like IS NULL
    -- ADT #1100: a nested table's storage table is listed as a TABLE, but
    -- COLUMNS_QUERY finds no column in it, so its SELECT had none at all
    -- (ORA-00936); its rows belong to the parent row and DML cannot name it
    AND o.object_name NOT IN (SELECT n.table_name FROM user_nested_tables n)
ORDER BY o.object_name
""".strip()

# The schema's tables a SELECT is filtered on (`#1098`): an enabled policy that
# covers SELECT. One on INSERT or UPDATE alone narrows nothing this export reads.
POLICY_TABLES_QUERY = """
SELECT DISTINCT object_name
FROM   user_policies
WHERE  enable = 'YES'
AND    sel    = 'YES'
ORDER  BY object_name
""".strip()

# Whether this session reads past every policy (`#1098`). A bypass the app's own
# policy function grants, such as a context a `STARTUP.sql` sets, is invisible
# here: it is a decision inside the function, never a privilege.
EXEMPT_QUERY = """
SELECT COUNT(*) AS exempt
FROM   session_privs
WHERE  privilege = 'EXEMPT ACCESS POLICY'
""".strip()

COLUMNS_QUERY = """
SELECT
    t.column_name,
    t.data_type,
    t.column_id,
    t.nullable,
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
    -- ADT #982: a UNIQUE key over a nullable column lets several rows share
    -- NULL, so a constraint whose columns are all NOT NULL is preferred; a
    -- nullable one comes back only when it is all there is, and the export
    -- refuses it as a replay key
    SELECT MIN(k.constraint_name) KEEP (DENSE_RANK FIRST ORDER BY k.nullable_columns)
        AS constraint_name
    FROM (
        SELECT
            u.constraint_name,
            COUNT(CASE WHEN tc.nullable = 'Y' THEN 1 END) AS nullable_columns
        FROM user_constraints u
        JOIN user_cons_columns uc
            ON uc.table_name        = u.table_name
            AND uc.constraint_name  = u.constraint_name
        JOIN user_tab_cols tc
            ON tc.table_name        = uc.table_name
            AND tc.column_name      = uc.column_name
        WHERE u.table_name          = :table_name
            AND u.constraint_type   = 'U'
        GROUP BY u.constraint_name
    ) k
) u
    ON u.constraint_name    = c.constraint_name
LEFT JOIN user_tab_identity_cols i
    ON i.table_name         = t.table_name
    AND i.column_name       = t.column_name
-- ADT #1111: the exact dictionary name, so `FOO` and a table created quoted as
-- "Foo" are never read as one another; a caller holding a file name resolves it
-- through TABLES_QUERY first
WHERE t.table_name          = :table_name
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
    t.column_id,
    t.nullable
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
    """The SELECT of `data_select` in `order_by` order.

    `order_by` is a comma-separated list of names, or `ROWID`.
    """
    ordering = ", ".join(
        part.strip() if part.strip().upper() == "ROWID"
        else sql_name(part.strip(), role="order by column")
        for part in order_by.split(",")
        if part.strip()
    )
    select = data_select(table_name, columns, where_filter, column_types)
    return f"{select}\nORDER BY {ordering}"


def data_select(
    table_name: str,
    columns: list[str],
    where_filter: str,
    column_types: Mapping[str, str] | None = None,
) -> str:
    """The SELECT and FROM of a table's rows, with no ordering, so a caller
    that orders them differently (`diff -data -limit`, `#1111`) appends its own
    rather than cutting one off this text.

    `column_types` names the columns Oracle has to render for us (`#695`).

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
    table = sql_name(table_name, role="table name")
    sql_names(columns, role="column name")
    spatial = {
        column for column in columns if column_type(column_types, column) in SPATIAL_TYPES
    }
    selected = ", ".join(
        _wkt_projection(column) if column in spatial
        else _zoned_projection(column) if _ZONED.match(column_type(column_types, column))
        else _date_projection(column) if column_type(column_types, column) == "DATE"
        else sql_name(column)
        for column in columns
    )
    alias = f" {SOURCE_ALIAS}" if spatial else ""
    return f"SELECT {selected}\nFROM {table}{alias}{where_filter}"


def count_query(table_name: str) -> str:
    """Every row of the table, which a `where` filter's export is measured against (`#1099`)."""
    return f"SELECT COUNT(*) AS row_count\nFROM {sql_name(table_name, role='table name')}"


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
    name = sql_name(column)
    reference = f"{SOURCE_ALIAS}.{name}"
    return (
        f"CASE WHEN {reference} IS NULL THEN NULL ELSE "
        f"'SRID=' || NVL(TO_CHAR({reference}.SDO_SRID), 'NULL') || ';' || "
        f"SDO_UTIL.TO_WKTGEOMETRY({reference}) END AS {name}"
    )


#: `TIMESTAMP(6) WITH TIME ZONE`, and never its `WITH LOCAL TIME ZONE` sibling,
#: which the driver already returns in the session zone (`_temporal_literal`).
_ZONED = re.compile(r"^TIMESTAMP(\(\d\))? WITH TIME ZONE$")


def _zoned_projection(column: str) -> str:
    """One SELECT item: a TIMESTAMP WITH TIME ZONE as ISO text with its offset (`#1004`).

    The driver hands the type back as a naive datetime, so the offset is gone
    before Python sees the row and a replay would stamp the target session's own
    zone on the moment. Oracle writes the text instead, and `_temporal_literal`
    reads the offset back into `TO_TIMESTAMP_TZ`.
    """
    name = sql_name(column)
    return f"TO_CHAR({name}, '{_DATE_MASK}.FF6TZH:TZM') AS {name}"


def _date_projection(column: str) -> str:
    """One SELECT item: a DATE as the text `str()` of a datetime would give (ADT #1100).

    Oracle's DATE reaches back to -4712 and Python's datetime stops at year 1,
    so the driver raised on the first BC row and took the table with it. A BC
    year carries its sign through `SYYYY`; every other date is `YYYY` alone,
    because `SYYYY` pads a positive year with a space the CSV has never held.
    """
    name = sql_name(column)
    return (
        f"CASE WHEN {name} < DATE '0001-01-01' "
        f"THEN TO_CHAR({name}, 'S{_DATE_MASK}') "
        f"ELSE TO_CHAR({name}, '{_DATE_MASK}') END AS {name}"
    )


def update_assignments(
    columns: list[str],
    skip_update: str,
    values: Mapping[str, str] | None = None,
) -> str:
    """The UPDATE SET lines: each column from the source, then each literal in `values`.

    `values` are the nullable sidecar columns, set to NULL so a NULL replays
    before the per-row LOB scripts write every value that exists (`#1100`).
    """
    literals = dict(values or {})
    assigned = [
        (f"t.{name}", f"s.{name}") for name in sql_names(columns, lower=True)
    ] + [
        (f"t.{sql_name(column, lower=True)}", value) for column, value in literals.items()
    ]
    if not assigned:
        return ""
    width = max(len(target) for target, _ in assigned)
    return f",\n{skip_update}        ".join(
        f"{target:<{width}} = {value}"
        for target, value in assigned
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
    insert_values: Mapping[str, str] | None = None,
) -> str:
    """`insert_columns` narrows the INSERT list; `columns` still drives the SELECT.

    A GENERATED ALWAYS identity column may not be named in an INSERT (ORA-32795,
    `#670`), so it is exported and joined on but never inserted. An empty list
    falls back to every column, which only happens when the INSERT is commented
    out anyway. With both the INSERT and the UPDATE off there is no MERGE at all.

    `insert_values` adds columns the SELECT does not carry, each with the literal
    it is inserted with (`#1094`): a NOT NULL BLOB or CLOB lives in a sidecar, so
    an INSERT built from the CSV left it out and the first row into an empty
    target failed with ORA-01400 before the LOB UPDATE scripts ran. They are
    appended to the INSERT list alone; the UPDATE SET and the join never see them.
    """
    table = sql_qualified_name(table, lower=True)
    sql_names(columns, role="column name")
    literals = dict(insert_values or {})
    sql_names(literals, role="column name")
    header = f"""BEGIN
    DBMS_OUTPUT.PUT_LINE('--');
    DBMS_OUTPUT.PUT_LINE('-- MERGE ' || UPPER('{table}'));
    DBMS_OUTPUT.PUT_LINE('--');
END;
/
--
{skip_delete}DELETE FROM {table}{where_filter};
--
"""
    if skip_insert and skip_update:
        # `#982`: both WHEN clauses off leaves a MERGE Oracle refuses to parse,
        # so the statement is left out whole and the DELETE runs on its own.
        return f"{header}COMMIT;\n"
    inserted = [*(insert_columns or columns), *literals]
    all_columns = "t." + ",\n        t.".join(sql_names(inserted, lower=True))
    all_values = ",\n        ".join(
        literals.get(column, f"s.{sql_name(column, lower=True)}") for column in inserted
    )
    csv_content = " UNION ALL\n    ".join(csv_selects)
    return f"""{header}MERGE INTO {table} t
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


def no_merge_script(table: str, reason: str) -> str:
    """The comment-only file that replaces a MERGE an export could not generate.

    `<table>.sql` is also how a bare `adtai export_data` finds the tables it
    refreshes, so deleting it would drop the table from every later run, while
    keeping the old MERGE would replay rows the CSV no longer holds (`#958`).
    Only comment lines, so running it anywhere does nothing.
    """
    safe_qualified_identifier(table, role="table name")
    return (
        f"-- {table.upper()}: no MERGE was generated, because {reason}.\n"
        "-- This file keeps the table in a bare `adtai export_data` refresh;\n"
        "-- the next export that can generate a MERGE writes it here.\n"
    )


def row_select(
    row: dict[str, Any],
    columns: list[str],
    column_types: Mapping[str, str] | None = None,
) -> str:
    """`column_types` maps a column's exact name to its Oracle data type.

    The MERGE path reads its values back out of the CSV, where every cell is
    text, so the destination column's type is the only thing that can say
    whether a cell is a number, a RAW or a date (`#670`). Omitting it renders
    each value by its Python type instead, which is what a caller holding the
    driver's own row (`lob_update_scripts`) wants.
    """
    names = sql_names(columns, role="column name")
    values = [
        f"{sql_value(row[column], column_type(column_types, column))} AS {name}"
        for column, name in zip(columns, names, strict=True)
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
#: A BC date as `_date_projection` writes it, which no datetime can parse.
_BC_DATE_TEXT = re.compile(r"^-\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
#: Oracle's own spellings of its two interval types, `[+|-]D HH:MI:SS[.FF]` and
#: `[+|-]Y-MM`, which `TO_DSINTERVAL` and `TO_YMINTERVAL` read whatever the
#: session's NLS settings are. `export_data/intervals.py` writes both.
_DS_INTERVAL_TEXT = re.compile(r"^[+-]?\d+ \d{1,2}:\d{2}:\d{2}(\.\d{1,9})?$")
_YM_INTERVAL_TEXT = re.compile(r"^[+-]?\d+-\d{1,2}$")


def sql_value(value: Any, data_type: str = "") -> str:
    """The SQL literal for one exported value.

    With `data_type`, the column's Oracle type decides the literal: a RAW is hex
    that `HEXTORAW` decodes, a NUMBER stays unquoted with every digit it was
    exported with, and a DATE or TIMESTAMP carries its own conversion so it does
    not depend on the session's NLS formats, which default to `DD-MON-RR` and
    raise ORA-01861 against an ISO string (`#670`). The value is either CSV text
    or the driver's own value, which is rendered to that same text first: the
    LOB UPDATE scripts key their WHERE off the driver's row, where a RAW key is
    `bytes` and `str()` of it is Python's `b'...'` repr (`#923`).

    Without it the value is rendered by its Python type, for a caller that holds
    the driver's own row and no inventory to type it with.
    """
    if value is None or value == "":
        return "NULL"
    if data_type:
        return _typed_literal(_exported_text(value), data_type.strip().upper())
    if isinstance(value, int | float | Decimal):
        return str(value)
    return _quoted(str(value))


def _exported_text(value: Any) -> str:
    """One driver value as the text its CSV cell carries, which `_typed_literal` reads."""
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex().upper()
    if isinstance(value, timedelta):
        return ds_interval_text(value)
    if is_ym_interval(value):
        return ym_interval_text(value)
    return str(value)


def _typed_literal(text: str, data_type: str) -> str:
    if data_type in SPATIAL_TYPES:
        return _spatial_literal(text)
    if data_type in _RAW_TYPES and _is_hex(text):
        return f"HEXTORAW('{text}')"
    if data_type in _NUMBER_TYPES and _NUMBER_TEXT.match(text):
        return text
    if data_type == "DATE" or data_type.startswith("TIMESTAMP"):
        return _temporal_literal(text, data_type)
    # `user_tab_cols` spells the type with its precisions, `INTERVAL DAY(2) TO
    # SECOND(6)` and `INTERVAL YEAR(2) TO MONTH`, so the prefix identifies it.
    if data_type.startswith("INTERVAL DAY") and _DS_INTERVAL_TEXT.match(text):
        return f"TO_DSINTERVAL('{text}')"
    if data_type.startswith("INTERVAL YEAR") and _YM_INTERVAL_TEXT.match(text):
        return f"TO_YMINTERVAL('{text}')"
    return _quoted(text)


def _temporal_literal(text: str, data_type: str) -> str:
    """A DATE/TIMESTAMP literal with the conversion that reads it back.

    The offset on the parsed value, not the column type, decides between
    `TO_TIMESTAMP_TZ` and `TO_TIMESTAMP`: the driver hands a TIMESTAMP WITH
    LOCAL TIME ZONE back in the session zone with no offset attached, and Oracle
    normalizes a plain TIMESTAMP into that column on assignment.
    """
    if data_type == "DATE" and _BC_DATE_TEXT.match(text):
        return f"TO_DATE('{text}', 'S{_DATE_MASK}')"
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
