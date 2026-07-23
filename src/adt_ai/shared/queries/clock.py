from __future__ import annotations

# The database clock, read once per scope BEFORE that scope's object listing, to
# become the scope's next `-recent` watermark (see shared/recent_state.py).
#
# It must be the DATABASE's clock, not the client's: `last_ddl_time` is written
# by the database, so a client clock running even slightly fast would store a
# watermark in the future and skip real changes on every later run. Formatted
# server-side so the value crosses the gateway as a plain string and never
# depends on the driver's datetime mapping or the session's NLS settings.
DB_NOW_QUERY = """
SELECT TO_CHAR(SYSDATE, 'YYYY-MM-DD HH24:MI:SS') AS db_now
FROM dual
""".strip()
