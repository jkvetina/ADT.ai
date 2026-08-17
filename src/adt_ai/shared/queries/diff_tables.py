"""SQL for the SQLcl DIFF leftover sweep (ADT #356).

Lives in a `queries` package because that is where every SQL constant lives
(`tests/contracts/test_sql_home.py`), and shared rather than per-module because
three commands run this same sweep: `diff` at the producer, `export_db` and
`patch -deploy` as the later backstops. One copy, so they cannot drift about
which tables these are.
"""

from __future__ import annotations

# Old ADT's own test, `t.table_name LIKE '%$1' OR t.table_name LIKE '%$2'`
# (`ADT--OLD/lib/queries_patch.py:283`).
#
# `user_tables` rather than `all_tables`: the leftovers belong to the connected
# schema, and a sweep has no business dropping another schema's objects even
# where the privileges would allow it.
DIFF_TABLES_QUERY = (
    "SELECT table_name FROM user_tables "
    "WHERE table_name LIKE '%$1' OR table_name LIKE '%$2' "
    "ORDER BY table_name"
)

# `PURGE` because the recycle bin is the other half of the mess: a plain drop
# leaves `BIN$...` objects that `export_db` then has to filter separately.
DROP_DIFF_TABLE_STATEMENT = "DROP TABLE {table_name} PURGE"
