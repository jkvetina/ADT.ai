"""Version probes run at every connected command's startup (connection block)."""

from __future__ import annotations

APEX_VERSION_QUERY = """
SELECT
    a.version_no AS version
FROM apex_release a
""".strip()

DATABASE_VERSION_QUERY = """
SELECT
    p.version_full || ' | ' ||
    REGEXP_REPLACE(SYS_CONTEXT('USERENV', 'DB_NAME'), '^[^_]+_', '') AS version
FROM product_component_version p
""".strip()

DATABASE_VERSION_OLD_QUERY = """
SELECT p.version
FROM product_component_version p
WHERE p.product LIKE 'Oracle Database%'
""".strip()
