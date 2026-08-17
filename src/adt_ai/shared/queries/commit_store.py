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
"""

from __future__ import annotations

COMMIT_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commits(
    branch  TEXT    NOT NULL,
    number  INTEGER NOT NULL,
    id      TEXT    NOT NULL,
    summary TEXT,
    author  TEXT,
    date    TEXT,
    patch   TEXT,
    PRIMARY KEY (branch, number)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_commits_branch_id ON commits(branch, id);
CREATE TABLE IF NOT EXISTS commit_files(
    branch TEXT    NOT NULL,
    number INTEGER NOT NULL,
    path   TEXT    NOT NULL,
    hash   TEXT,
    status TEXT,
    PRIMARY KEY (branch, number, path)
);
CREATE INDEX IF NOT EXISTS ix_files_path ON commit_files(branch, path);
"""

META_VERSION_INSERT = """
INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)
""".strip()

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
SELECT branch, number, id, summary, author, date, patch
FROM commits
WHERE branch = ?
ORDER BY number DESC
LIMIT ?
""".strip()

COMMIT_RECORDS_QUERY = """
SELECT branch, number, id, summary, author, date, patch
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
INSERT INTO commits(branch, number, id, summary, author, date, patch)
VALUES(?, ?, ?, ?, ?, ?, ?)
""".strip()

COMMIT_FILE_INSERT = """
INSERT OR REPLACE INTO commit_files(branch, number, path, hash, status)
VALUES(?, ?, ?, ?, ?)
""".strip()

# Floor, ceiling and count in one read: a contiguous branch spans exactly
# (high - low + 1) slots, so any other count is a gap.
COMMIT_SPAN_QUERY = """
SELECT MIN(number), MAX(number), COUNT(*) FROM commits WHERE branch = ?
""".strip()

# Dropping a branch is for ONE case: history was rewritten under it, so the
# numbers point at commits that no longer exist. Anything else that reached for
# these would be renumbering, which is the defect the store was built to end.
COMMIT_DELETE_BRANCH = """
DELETE FROM commits WHERE branch = ?
""".strip()

COMMIT_FILES_DELETE_BRANCH = """
DELETE FROM commit_files WHERE branch = ?
""".strip()
