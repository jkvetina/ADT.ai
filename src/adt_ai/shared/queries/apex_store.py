"""SQL for the APEX cache store (`shared/apex_store.py`).

Four tables, one subject: what ADT.ai knows about a project's APEX applications
between runs. Every write is an upsert, which is what lets the legacy conversion
re-run after a half-finished pass, and what lets the checksum arrive on its own
later pass without erasing the row the export listing already wrote.
"""

from __future__ import annotations

APEX_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    app_id       INTEGER PRIMARY KEY,
    owner        TEXT,
    workspace    TEXT,
    workspace_id TEXT,
    app_group    TEXT,
    app_alias    TEXT,
    app_name     TEXT,
    pages        INTEGER,
    updated_at   TEXT,
    checksum     TEXT
);
CREATE TABLE IF NOT EXISTS developers (
    workspace TEXT NOT NULL,
    user_name TEXT NOT NULL,
    user_mail TEXT,
    PRIMARY KEY (workspace, user_name)
);
CREATE TABLE IF NOT EXISTS timers (
    app_id  INTEGER NOT NULL,
    action  TEXT NOT NULL,
    seconds REAL,
    PRIMARY KEY (app_id, action)
);
CREATE TABLE IF NOT EXISTS watermarks (
    environment TEXT NOT NULL,
    app_id      TEXT NOT NULL,
    format      TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    PRIMARY KEY (environment, app_id, format)
);
CREATE TABLE IF NOT EXISTS _meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

APEX_META_UPSERT = (
    "INSERT INTO _meta (key, value) VALUES ('schema_version', ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
)

APEX_APPLICATIONS_QUERY = "SELECT * FROM applications ORDER BY app_id"

APEX_APPLICATION_QUERY = "SELECT * FROM applications WHERE app_id = ?"

#: Built per call because the column list is the store's own
#: ``APPLICATION_FIELDS`` tuple: spelling it twice is how the two drift.
def apex_application_upsert(fields: tuple[str, ...]) -> str:
    assignments = ", ".join(
        f"{field} = COALESCE(excluded.{field}, applications.{field})" for field in fields
    )
    columns = ", ".join(("app_id", *fields))
    placeholders = ", ".join("?" for _ in range(len(fields) + 1))
    return (
        f"INSERT INTO applications ({columns}) VALUES ({placeholders}) "
        f"ON CONFLICT(app_id) DO UPDATE SET {assignments}"
    )


APEX_CHECKSUM_UPSERT = (
    "INSERT INTO applications (app_id, checksum) VALUES (?, ?) "
    "ON CONFLICT(app_id) DO UPDATE SET checksum = excluded.checksum"
)

APEX_DEVELOPERS_QUERY = (
    "SELECT workspace, user_name, user_mail FROM developers ORDER BY workspace, user_name"
)

APEX_DEVELOPER_UPSERT = (
    "INSERT INTO developers (workspace, user_name, user_mail) VALUES (?, ?, ?) "
    "ON CONFLICT(workspace, user_name) DO UPDATE SET user_mail = excluded.user_mail"
)

APEX_TIMERS_QUERY = "SELECT app_id, action, seconds FROM timers ORDER BY app_id, action"

APEX_TIMER_UPSERT = (
    "INSERT INTO timers (app_id, action, seconds) VALUES (?, ?, ?) "
    "ON CONFLICT(app_id, action) DO UPDATE SET seconds = excluded.seconds"
)

APEX_WATERMARK_QUERY = (
    "SELECT exported_at FROM watermarks "
    "WHERE environment = ? AND app_id = ? AND format = ?"
)

APEX_WATERMARKS_QUERY = (
    "SELECT environment, app_id, format, exported_at FROM watermarks "
    "ORDER BY environment, app_id, format"
)

APEX_WATERMARK_UPSERT = (
    "INSERT INTO watermarks (environment, app_id, format, exported_at) VALUES (?, ?, ?, ?) "
    "ON CONFLICT(environment, app_id, format) DO UPDATE SET exported_at = excluded.exported_at"
)

__all__ = [name for name in globals() if not name.startswith("_")]
