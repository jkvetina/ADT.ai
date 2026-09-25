"""What ``connection -test`` counts once it is connected (ADT #948).

The filters are explained on :mod:`adt_ai.connection.probe`, the reader.
"""

from __future__ import annotations

OBJECT_COUNTS_QUERY = """
SELECT
    o.object_type,
    COUNT(*) AS total,
    SUM(CASE WHEN o.status != 'VALID' THEN 1 ELSE 0 END) AS invalid
FROM user_objects o
WHERE o.generated = 'N'
    AND o.secondary = 'N'
    AND o.object_name NOT LIKE 'BIN$%'
    AND o.object_type NOT LIKE '%PARTITION'
    AND o.object_type NOT LIKE 'LOB%'
GROUP BY o.object_type
ORDER BY o.object_type
"""

# The cheapest statement that proves a session opened: `-test` without
# `-schema` only asks whether each schema connects (`#949`).
CONNECT_PROBE_QUERY = "SELECT 1 AS connected FROM dual"

__all__ = ["CONNECT_PROBE_QUERY", "OBJECT_COUNTS_QUERY"]
