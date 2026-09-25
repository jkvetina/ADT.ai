"""SQL for the APEX cache store (`shared/apex_store.py`).

Four tables, one subject: what ADT.ai knows about a project's APEX applications
between runs. Every write is an upsert, which is what lets the legacy conversion
re-run after a half-finished pass, and what lets the checksum arrive on its own
later pass without erasing the row the export listing already wrote.

Version 2 (ADT #642) keys `watermarks` by an INTEGER `app_id` like every other
table, and gives `_meta` the NOT NULL `value` every store's version table has.

Version 3 (ADT #725) records what an APEXlang export was BASED ON beside the
checksum that identifies it: `base_commit`, the commit the repository sat at, and
`mirror_ref`, the ref `-mirror` shares that commit on. A checksum says whether
the target moved; these two say what to rebase onto when it did.

Version 4 (ADT #873) drops `applications.workspace_id`, which every export
fetched and stored and nothing read back.

Version 5 (ADT #925) stamps `checksum_at`, when the checksum was taken, so a
drift refusal can say how old the developer's base is.

Version 6 (ADT #962) stamps `checksum_env`, the environment the export
connected to when it read the checksum. A checksum is only meaningful against
the environment it was taken from: exporting from PLAYGROUND and deploying to
WHATEVER compares WHATEVER's live checksum against PLAYGROUND's recorded one,
which can never match. Recording the environment beside the checksum is what
lets a drift check read the RIGHT live value instead of the target's.
"""

from __future__ import annotations

from adt_ai.shared.queries.sqlite_store import META_TABLE_DDL

WATERMARKS_DDL = """
CREATE TABLE IF NOT EXISTS watermarks (
    environment TEXT    NOT NULL,
    app_id      INTEGER NOT NULL,
    format      TEXT    NOT NULL,
    exported_at TEXT    NOT NULL,
    PRIMARY KEY (environment, app_id, format)
);
"""

APEX_STORE_SCHEMA = META_TABLE_DDL + """
CREATE TABLE IF NOT EXISTS applications (
    app_id       INTEGER PRIMARY KEY,
    owner        TEXT,
    workspace    TEXT,
    app_group    TEXT,
    app_alias    TEXT,
    app_name     TEXT,
    pages        INTEGER,
    updated_at   TEXT,
    checksum     TEXT,
    checksum_at  TEXT,
    base_commit  TEXT,
    mirror_ref   TEXT,
    checksum_env TEXT
);
CREATE TABLE IF NOT EXISTS developers (
    workspace TEXT NOT NULL,
    user_name TEXT NOT NULL,
    user_mail TEXT,
    PRIMARY KEY (workspace, user_name)
);
CREATE TABLE IF NOT EXISTS timers (
    app_id  INTEGER NOT NULL,
    action  TEXT    NOT NULL,
    seconds REAL,
    PRIMARY KEY (app_id, action)
);
""" + WATERMARKS_DDL

# Version 1 to 2, one transaction. A watermark row whose `app_id` was never a
# number described no application and is left behind; a `_meta` row with no
# value answered nothing and goes the same way.
APEX_STORE_LIFT_1 = """
BEGIN;
ALTER TABLE watermarks RENAME TO watermarks_v1;
""" + WATERMARKS_DDL + """
INSERT OR REPLACE INTO watermarks (environment, app_id, format, exported_at)
SELECT environment, CAST(app_id AS INTEGER), format, exported_at
  FROM watermarks_v1
 WHERE app_id <> '' AND app_id NOT GLOB '*[^0-9]*';
DROP TABLE watermarks_v1;
ALTER TABLE _meta RENAME TO _meta_v1;
""" + META_TABLE_DDL + """
INSERT INTO _meta (key, value) SELECT key, value FROM _meta_v1 WHERE value IS NOT NULL;
DROP TABLE _meta_v1;
COMMIT;
"""

# Version 2 to 3, two added columns. `ALTER TABLE ... ADD COLUMN` rather than a
# rename-and-copy: nothing about the existing rows changes, so a rebuild would
# only be a longer way to keep them, and the schema script above cannot add a
# column to a table that already exists.
APEX_STORE_LIFT_2 = """
BEGIN;
ALTER TABLE applications ADD COLUMN base_commit TEXT;
ALTER TABLE applications ADD COLUMN mirror_ref TEXT;
COMMIT;
"""

# Version 4 to 5, one added column, for the reason LIFT_2 gives.
APEX_STORE_LIFT_4 = """
BEGIN;
ALTER TABLE applications ADD COLUMN checksum_at TEXT;
COMMIT;
"""

# Version 5 to 6, one added column, for the reason LIFT_2 gives (ADT #962).
APEX_STORE_LIFT_5 = """
BEGIN;
ALTER TABLE applications ADD COLUMN checksum_env TEXT;
COMMIT;
"""

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


# `checksum_env` travels with the checksum verbatim, like the merge base below
# it: a re-export from a different environment must overwrite it, never merge
# with whatever the previous export recorded (ADT #962).
APEX_CHECKSUM_UPSERT = (
    "INSERT INTO applications (app_id, checksum, checksum_at, checksum_env) VALUES (?, ?, ?, ?) "
    "ON CONFLICT(app_id) DO UPDATE SET "
    "checksum = excluded.checksum, checksum_at = excluded.checksum_at, "
    "checksum_env = excluded.checksum_env"
)

# Written verbatim rather than through the COALESCE upsert above, because both
# values describe THIS export: a re-export from a checkout outside version
# control has no base, and leaving the previous one standing would hand the
# deploy a commit the tree on disk no longer descends from.
APEX_MERGE_BASE_UPSERT = (
    "INSERT INTO applications (app_id, base_commit, mirror_ref) VALUES (?, ?, ?) "
    "ON CONFLICT(app_id) DO UPDATE SET "
    "base_commit = excluded.base_commit, mirror_ref = excluded.mirror_ref"
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

__all__ = [
    "APEX_APPLICATIONS_QUERY",
    "APEX_APPLICATION_QUERY",
    "APEX_CHECKSUM_UPSERT",
    "APEX_DEVELOPERS_QUERY",
    "APEX_DEVELOPER_UPSERT",
    "APEX_MERGE_BASE_UPSERT",
    "APEX_STORE_LIFT_1",
    "APEX_STORE_LIFT_2",
    "APEX_STORE_SCHEMA",
    "APEX_TIMERS_QUERY",
    "APEX_TIMER_UPSERT",
    "APEX_WATERMARKS_QUERY",
    "APEX_WATERMARK_QUERY",
    "APEX_WATERMARK_UPSERT",
    "META_TABLE_DDL",
    "WATERMARKS_DDL",
    "annotations",
    "apex_application_upsert",
]
