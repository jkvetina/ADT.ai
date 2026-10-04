"""SQL for the `DEPSCAN$<n>#<n>` helper procedures (ADT #888).

The name pattern, the predicate that keeps a helper out of a `user_objects`
read, the read that finds the strays, and the drop. `shared/scan_helpers.py`
carries the calls around them.
"""

from __future__ import annotations

#: Oracle `REGEXP_LIKE` pattern for a helper's name, exactly as the SQL reads it.
SCAN_HELPER_NAME_PATTERN = r"^DEPSCAN\$[[:digit:]]+#[[:digit:]]+$"


def not_a_scan_helper(column: str) -> str:
    """The SQL predicate that keeps a helper out of a `user_objects` read."""
    return f"NOT REGEXP_LIKE({column}, '{SCAN_HELPER_NAME_PATTERN}')"


#: A helper younger than this may belong to a scan another session is running
#: right now (ADT #991). The name carries a per-scan id that maps to no session,
#: so age is the only line a cleanup can draw: APEX drops a finished scan's own
#: helpers itself, and what outlives a scan by this long belongs to none.
STALE_AFTER_MINUTES = 30

_STALE = f"AND created < SYSDATE - INTERVAL '{STALE_AFTER_MINUTES}' MINUTE"

#: The stale helpers on the schema, read before anything is dropped so a schema
#: carrying none sees no DDL at all.
SCAN_HELPERS_QUERY = f"""
SELECT object_name
FROM user_objects
WHERE object_type = 'PROCEDURE'
AND REGEXP_LIKE(object_name, '{SCAN_HELPER_NAME_PATTERN}')
{_STALE}
""".strip()

#: Drops every stale helper on the schema and leaves a running scan's alone.
#: Idempotent: it loops over whatever matches now, so a schema with none is a
#: no-op and a second run after a first is another.
DROP_SCAN_HELPERS_STATEMENT = f"""
BEGIN
    FOR r IN (
        SELECT object_name
        FROM user_objects
        WHERE object_type = 'PROCEDURE'
        AND REGEXP_LIKE(object_name, '{SCAN_HELPER_NAME_PATTERN}')
        {_STALE}
    ) LOOP
        EXECUTE IMMEDIATE 'DROP PROCEDURE "' || REPLACE(r.object_name, '"', '""') || '"';
    END LOOP;
END;
""".strip()


__all__ = [
    "DROP_SCAN_HELPERS_STATEMENT",
    "SCAN_HELPERS_QUERY",
    "SCAN_HELPER_NAME_PATTERN",
    "STALE_AFTER_MINUTES",
    "not_a_scan_helper",
]
