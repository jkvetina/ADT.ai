"""Deep Data Security reads: a schema's Data Grants and the Data Roles they name (`#1063`).

Neither type has a `user_objects` row and `DBMS_METADATA` refuses both with
`ORA-31600`, so `data_security.py` rebuilds each statement from these rows.
Measured on the live 26ai fixture (Oracle AI Database 23.26.3.0.0, SANDBOX,
2026-10-09).
"""

from __future__ import annotations

# One row per privilege, column and grantee. `ALL COLUMNS EXCEPT` comes back
# expanded, one row per granted column per excepted column, which is why the
# excepted column is read beside the granted one. Both column ids are joined in
# so the rebuilt column lists keep the table's own order whatever order the view
# returns rows in. The two BOOLEAN and TIMESTAMP WITH TIME ZONE columns are
# rendered here, so the renderer sees the text it writes.
DATA_GRANTS_QUERY = """
SELECT g.grant_name,
       g.privilege,
       g.column_name,
       g.granted_with_all_columns_except AS except_column,
       g.object_owner,
       g.object_name,
       g.predicate,
       g.grantee,
       g.grantee_type,
       CASE WHEN g.cross_table_data_grant THEN 'Y' ELSE 'N' END AS cross_table,
       TO_CHAR(g.start_time, 'YYYY-MM-DD HH24:MI:SS.FF6 TZR') AS start_time,
       TO_CHAR(g.end_time, 'YYYY-MM-DD HH24:MI:SS.FF6 TZR') AS end_time,
       c.column_id,
       e.column_id AS except_column_id
FROM   user_data_grants g
LEFT JOIN all_tab_columns c
    ON  c.owner       = g.object_owner
    AND c.table_name  = g.object_name
    AND c.column_name = g.column_name
LEFT JOIN all_tab_columns e
    ON  e.owner       = g.object_owner
    AND e.table_name  = g.object_name
    AND e.column_name = g.granted_with_all_columns_except
ORDER  BY g.grant_name
""".strip()

# No `user_` or `all_` view describes a Data Role: the mapping to an IAM group and
# the default state live in `dba_data_roles` alone. A schema without access to it
# answers `ORA-00942`, and the export then writes a stub per role name instead.
DATA_ROLES_QUERY = """
SELECT data_role,
       mapped_to,
       CASE WHEN enabled_by_default THEN 'Y' ELSE 'N' END AS enabled
FROM   dba_data_roles
ORDER  BY data_role
""".strip()
