from __future__ import annotations

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
    MIN(CASE WHEN u.constraint_name IS NOT NULL THEN c.position END) AS uq
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
) -> str:
    safe_qualified_identifier(table, role="table name")
    safe_identifiers(columns, role="column name")
    all_columns = "t." + ",\n        t.".join(columns)
    all_values = "s." + ",\n        s.".join(columns)
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


def row_select(row: dict[str, Any], columns: list[str]) -> str:
    safe_identifiers(columns, role="column name")
    values = [
        f"{sql_value(row[column])} AS {column}"
        for column in columns
    ]
    return f"SELECT {', '.join(values)} FROM DUAL"


def sql_value(value: Any) -> str:
    if value is None or value == "":
        return "NULL"
    if isinstance(value, int | float):
        return str(value)
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"
