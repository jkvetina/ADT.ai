"""The dictionary reads of `search TERM -data` (ADT #879, #920)."""

from __future__ import annotations

#: The objects of the login schema a search reads, `-name` and `ignore:`
#: applied to the name the schema knows them by. A synonym stands for its
#: target in another schema, read owner-qualified, so a target reached only
#: through a database link is left out. Left out as well: a materialized view's
#: container table, which the materialized view row already stands for; a
#: global temporary table, holding only the writing session's rows, whose
#: assertion auxiliary kind refuses every read (`ORA-08709`, the first live run
#: on SANDBOX); and an external table, whose rows are a file.
OBJECTS_QUERY = """
WITH objects_names AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :object_name), ',')) t
),
objects_ignore AS (
    SELECT /*+ MATERIALIZE CARDINALITY(t 10) */
        t.column_value AS object_like
    FROM TABLE(APEX_STRING.SPLIT(TRIM(BOTH ',' FROM :objects_ignore), ',')) t
),
objects AS (
    SELECT
        o.object_type,
        o.object_name,
        USER            AS target_owner,
        o.object_name   AS target_name,
        o.object_type   AS target_type
    FROM user_objects o
    WHERE o.object_type IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
    UNION ALL
    SELECT
        'SYNONYM',
        s.synonym_name,
        s.table_owner,
        s.table_name,
        MIN(t.object_type)  -- a materialized view is listed as a TABLE too
    FROM user_synonyms s
    JOIN all_objects t
        ON t.owner          = s.table_owner
        AND t.object_name   = s.table_name
        AND t.object_type   IN ('TABLE', 'VIEW', 'MATERIALIZED VIEW')
    WHERE s.db_link IS NULL
    GROUP BY
        s.synonym_name,
        s.table_owner,
        s.table_name
)
SELECT
    o.object_type,
    o.object_name,
    o.target_owner,
    o.target_name,
    o.target_type
FROM objects o
WHERE EXISTS (
        SELECT 1
        FROM objects_names n
        WHERE o.object_name LIKE n.object_like ESCAPE '\\'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM objects_ignore g
        WHERE o.object_name LIKE g.object_like ESCAPE '\\'
    )
    AND NOT (
        o.target_type = 'TABLE'
        AND EXISTS (
            SELECT 1
            FROM all_mviews m
            WHERE m.owner           = o.target_owner
                AND m.mview_name    = o.target_name
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM all_tables t
        WHERE t.owner           = o.target_owner
            AND t.table_name    = o.target_name
            AND (t.temporary = 'Y' OR t.external = 'YES')
    )
ORDER BY
    DECODE(o.object_type, 'TABLE', 1, 'VIEW', 2, 'MATERIALIZED VIEW', 3, 4),
    o.object_name
""".strip()

#: The columns of one object, by owner, so a synonym's target in another schema
#: reads the same way as a table of the login schema. `ALL_TAB_COLS` lists a
#: view's columns as well as a table's.
COLUMNS_QUERY = """
SELECT
    t.column_name,
    t.data_type,
    t.column_id,
    MIN(CASE WHEN n.constraint_name IS NOT NULL THEN c.position END) AS pk
FROM all_tab_cols t
LEFT JOIN all_cons_columns c
    ON c.owner              = t.owner
    AND c.table_name        = t.table_name
    AND c.column_name       = t.column_name
LEFT JOIN all_constraints n
    ON n.owner              = c.owner
    AND n.constraint_name   = c.constraint_name
    AND n.constraint_type   = 'P'
WHERE t.owner               = :owner
    AND t.table_name        = :table_name
    AND t.column_id         > 0
    AND t.hidden_column     = 'NO'
GROUP BY
    t.column_name,
    t.data_type,
    t.column_id
ORDER BY
    t.column_id
""".strip()

__all__ = ["COLUMNS_QUERY", "OBJECTS_QUERY"]
