"""SQL for the per-branch commit store (`shared/commit_store.py`).

SQLite, not Oracle: this is the only SQL home in the package that talks to a
local file rather than a gateway, and it is here for the same reason as the
rest, one SQL home per module.

The schema is the numbering contract written down. `PRIMARY KEY (branch,
number)` says a number belongs to one commit, `UNIQUE (branch, id)` says a
commit carries one number, and together they make a hole or a reused number
unwritable rather than merely tested for. That matters because the defect this
store replaces was exactly a numbering one: positional numbering let a merge
renumber commits that were already cached.

Version 2 (ADT #642) is the convention every store follows: the version table
is `_meta`, the author date is `authored_at`, and `commit_files` declares the
foreign key it always kept by hand, with a cascade.
"""

from __future__ import annotations

from adt_ai.shared.queries.sqlite_store import META_TABLE_DDL

COMMIT_FILES_DDL = """
CREATE TABLE IF NOT EXISTS commit_files (
    branch TEXT    NOT NULL,
    number INTEGER NOT NULL,
    path   TEXT    NOT NULL,
    hash   TEXT,
    status TEXT,
    PRIMARY KEY (branch, number, path),
    FOREIGN KEY (branch, number) REFERENCES commits (branch, number) ON DELETE CASCADE
);
"""

COMMIT_STORE_SCHEMA = META_TABLE_DDL + """
CREATE TABLE IF NOT EXISTS commits (
    branch      TEXT    NOT NULL,
    number      INTEGER NOT NULL,
    id          TEXT    NOT NULL,
    summary     TEXT,
    author      TEXT,
    authored_at TEXT,
    patch       TEXT,
    PRIMARY KEY (branch, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_commits_branch_id ON commits (branch, id);
""" + COMMIT_FILES_DDL + """
CREATE INDEX IF NOT EXISTS ix_commit_files_path ON commit_files (branch, path);
"""

# Version 1 to 2, one transaction. `meta` becomes `_meta`, `date` becomes
# `authored_at` with git's `T` read as the space every other stamp uses, and
# `commit_files` is rebuilt around its foreign key: a file row whose commit is
# gone had nothing to describe and is left behind.
COMMIT_STORE_LIFT_1 = """
BEGIN;
ALTER TABLE meta RENAME TO _meta;
DROP INDEX IF EXISTS ix_commits_branch_id;
DROP INDEX IF EXISTS ix_files_path;
ALTER TABLE commits RENAME COLUMN date TO authored_at;
UPDATE commits
   SET authored_at = substr(authored_at, 1, 10) || ' ' || substr(authored_at, 12)
 WHERE substr(authored_at, 11, 1) = 'T';
ALTER TABLE commit_files RENAME TO commit_files_v1;
""" + COMMIT_FILES_DDL + """
INSERT INTO commit_files (branch, number, path, hash, status)
SELECT f.branch, f.number, f.path, f.hash, f.status
  FROM commit_files_v1 f
 WHERE EXISTS (SELECT 1 FROM commits c WHERE c.branch = f.branch AND c.number = f.number);
DROP TABLE commit_files_v1;
COMMIT;
"""

META_BRANCH_QUERY = "SELECT value FROM _meta WHERE key = 'branch_name'"

META_BRANCH_INSERT = "INSERT INTO _meta (key, value) VALUES ('branch_name', ?)"

COMMIT_BRANCHES_QUERY = "SELECT DISTINCT branch FROM commits ORDER BY branch"

COMMIT_FLOOR_QUERY = """
SELECT MIN(number) FROM commits WHERE branch = ?
""".strip()

COMMIT_CEILING_QUERY = """
SELECT MAX(number) FROM commits WHERE branch = ?
""".strip()

COMMIT_NUMBERS_QUERY = """
SELECT id, number FROM commits WHERE branch = ?
""".strip()

# Newest first, bounded. This is the query `patch` runs, and the reason the
# store is a database: it answers "the newest forty" without materialising the
# branch, where a text cache has to parse all of it first.
COMMIT_RECENT_QUERY = """
SELECT branch, number, id, summary, author, authored_at, patch
FROM commits
WHERE branch = ?
ORDER BY number DESC
LIMIT ?
""".strip()

COMMIT_RECORDS_QUERY = """
SELECT branch, number, id, summary, author, authored_at, patch
FROM commits
WHERE branch = ?
ORDER BY number
""".strip()

COMMIT_BY_PATH_QUERY = """
SELECT number FROM commit_files
WHERE branch = ? AND path = ?
ORDER BY number DESC
""".strip()

# The IN list is built from a number of placeholders known at call time, so the
# statement carries a single `{placeholders}` slot rather than being assembled
# out of fragments at the call site.
COMMIT_FILES_FOR_NUMBERS_TEMPLATE = """
SELECT number, path, hash, status FROM commit_files
WHERE branch = ? AND number IN ({placeholders})
""".strip()

COMMIT_INSERT = """
INSERT INTO commits (branch, number, id, summary, author, authored_at, patch)
VALUES (?, ?, ?, ?, ?, ?, ?)
""".strip()

COMMIT_FILE_INSERT = """
INSERT OR REPLACE INTO commit_files (branch, number, path, hash, status)
VALUES (?, ?, ?, ?, ?)
""".strip()

# Floor, ceiling and count in one read: a contiguous branch spans exactly
# (high - low + 1) slots, so any other count is a gap.
COMMIT_SPAN_QUERY = """
SELECT MIN(number), MAX(number), COUNT(*) FROM commits WHERE branch = ?
""".strip()

# Dropping a branch is for ONE case: history was rewritten under it, so the
# numbers point at commits that no longer exist. Anything else that reached for
# these would be renumbering, which is the defect the store was built to end.
# The file rows go with the commits through the cascade.
COMMIT_DELETE_BRANCH = """
DELETE FROM commits WHERE branch = ?
""".strip()
