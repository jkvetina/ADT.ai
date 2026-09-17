"""SQL for the per-branch commit store (`shared/commit_store.py`).

SQLite, not Oracle: this is the only SQL home in the package that talks to a
local file rather than a gateway, and it is here for the same reason as the
rest, one SQL home per module.

The schema is the numbering contract written down. `number INTEGER PRIMARY KEY`
says a number belongs to one commit, `UNIQUE (id)` says a commit carries one
number, and together they make a reused number unwritable rather than merely
tested for. That matters because the defect this store replaces was exactly a
numbering one: positional numbering let a merge renumber commits that were
already cached.

Version 2 (ADT #642) is the convention every store follows: the version table
is `_meta`, the author date is `authored_at`, and `commit_files` declares the
foreign key it always kept by hand, with a cascade. Version 3 (ADT #851)
drops `commits.patch`. Version 4 (ADT #873) drops `branch` from both tables:
one file holds one branch and `_meta.branch_name` says which, so the column
repeated one value on every row and widened every key and index for nothing.
"""

from __future__ import annotations

from adt_ai.shared.queries.sqlite_store import META_TABLE_DDL

COMMITS_DDL = """
CREATE TABLE IF NOT EXISTS commits (
    number      INTEGER PRIMARY KEY,
    id          TEXT    NOT NULL UNIQUE,
    summary     TEXT,
    author      TEXT,
    authored_at TEXT
);
"""

# WITHOUT ROWID: the key is the row, so a file row is stored once in key order
# instead of twice (table plus key index). The path index then carries the
# number too, which is what the status fallback in `search_repo` reads.
COMMIT_FILES_DDL = """
CREATE TABLE IF NOT EXISTS commit_files (
    number INTEGER NOT NULL REFERENCES commits (number) ON DELETE CASCADE,
    path   TEXT    NOT NULL,
    hash   TEXT,
    status TEXT,
    PRIMARY KEY (number, path)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_commit_files_path ON commit_files (path);
"""

COMMIT_STORE_SCHEMA = META_TABLE_DDL + COMMITS_DDL + COMMIT_FILES_DDL

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
CREATE TABLE commit_files (
    branch TEXT    NOT NULL,
    number INTEGER NOT NULL,
    path   TEXT    NOT NULL,
    hash   TEXT,
    status TEXT,
    PRIMARY KEY (branch, number, path),
    FOREIGN KEY (branch, number) REFERENCES commits (branch, number) ON DELETE CASCADE
);
INSERT INTO commit_files (branch, number, path, hash, status)
SELECT f.branch, f.number, f.path, f.hash, f.status
  FROM commit_files_v1 f
 WHERE EXISTS (SELECT 1 FROM commits c WHERE c.branch = f.branch AND c.number = f.number);
DROP TABLE commit_files_v1;
CREATE INDEX ix_commit_files_path ON commit_files (branch, path);
COMMIT;
"""

# Version 2 to 3 (ADT #851). `patch` held the folder a commit touched, decided
# once when `rebuild` wrote the row, under a hardcoded `patch/`, and nothing ever
# read it. `patch` now reads the marker off `commit_files` with the project's own
# `patch_root`, so the column goes rather than hold an answer to a question asked
# with the wrong root. Every number and file row is untouched.
COMMIT_STORE_LIFT_2 = """
BEGIN;
ALTER TABLE commits DROP COLUMN patch;
COMMIT;
"""

# Version 3 to 4 (ADT #873), one transaction. The branch owner is recorded in
# `_meta` first, for a file that predates the record, and the caller has
# already refused a file holding two branches. Both tables are then rebuilt on
# their new keys, every number, id, file row and status carried across.
COMMIT_STORE_LIFT_3 = """
BEGIN;
INSERT INTO _meta (key, value)
SELECT 'branch_name', (SELECT MIN(branch) FROM commits)
 WHERE EXISTS (SELECT 1 FROM commits)
   AND NOT EXISTS (SELECT 1 FROM _meta WHERE key = 'branch_name');
ALTER TABLE commit_files RENAME TO commit_files_v3;
ALTER TABLE commits RENAME TO commits_v3;
DROP INDEX IF EXISTS ix_commit_files_path;
DROP INDEX IF EXISTS ux_commits_branch_id;
""" + COMMITS_DDL + COMMIT_FILES_DDL + """
INSERT INTO commits (number, id, summary, author, authored_at)
SELECT number, id, summary, author, authored_at FROM commits_v3;
INSERT INTO commit_files (number, path, hash, status)
SELECT number, path, hash, status FROM commit_files_v3;
DROP TABLE commit_files_v3;
DROP TABLE commits_v3;
COMMIT;
"""

COMMIT_STORE_VACUUM = "VACUUM"

COMMIT_V3_BRANCHES_QUERY = "SELECT DISTINCT branch FROM commits ORDER BY branch"

META_BRANCH_QUERY = "SELECT value FROM _meta WHERE key = 'branch_name'"

META_BRANCH_INSERT = "INSERT INTO _meta (key, value) VALUES ('branch_name', ?)"

# Floor, ceiling and count in one read: a contiguous branch spans exactly
# (high - low + 1) slots, so any other count is a gap. Both ends are read off
# the primary key, so this costs two index probes and a count.
COMMIT_SPAN_QUERY = "SELECT MIN(number), MAX(number), COUNT(*) FROM commits"

# The IN lists below are built from a number of placeholders known at call
# time, so each statement carries a single `{placeholders}` slot rather than
# being assembled out of fragments at the call site.
COMMIT_NUMBERS_FOR_IDS_TEMPLATE = """
SELECT id, number FROM commits WHERE id IN ({placeholders})
""".strip()

# Newest first, bounded. This is the query `patch` runs, and the reason the
# store is a database: it answers "the newest forty" without materialising the
# branch, where a text cache has to parse all of it first.
COMMIT_RECENT_QUERY = """
SELECT number, id, summary, author, authored_at
FROM commits
ORDER BY number DESC
LIMIT ?
""".strip()

# `calendar`'s read: one month of commits, no file rows. `authored_at` starts
# with the author's own `YYYY-MM`, which is the month the calendar files it in.
COMMIT_MONTH_QUERY = """
SELECT number, id, summary, author, authored_at
FROM commits
WHERE substr(authored_at, 1, 7) = ?
ORDER BY number
""".strip()

# `search_repo`'s page: newest first below a number, narrowed by whatever
# filters the caller could state in SQL. `{conditions}` is filled only from the
# `COMMIT_SEARCH_*` fragments below, joined with AND.
COMMIT_SEARCH_TEMPLATE = """
SELECT number, id, summary, author, authored_at
FROM commits
WHERE number < ?{conditions}
ORDER BY number DESC
LIMIT ?
""".strip()

COMMIT_SEARCH_SINCE = "substr(authored_at, 1, 10) >= ?"

COMMIT_SEARCH_UNTIL = "substr(authored_at, 1, 10) <= ?"

COMMIT_SEARCH_SUMMARY = "instr(lower(summary), ?) > 0"

COMMIT_SEARCH_PATH = (
    "EXISTS (SELECT 1 FROM commit_files f"
    " WHERE f.number = commits.number AND instr(lower(f.path), ?) > 0)"
)

# One author clause is these alternatives joined with OR inside parentheses:
# a LIKE per pattern, an equality per exact address, and one IN list of the
# lower-cased personal addresses `repo_authors` folds into a match.
COMMIT_SEARCH_AUTHOR_LIKE = "author LIKE ? ESCAPE '\\'"

COMMIT_SEARCH_AUTHOR_EXACT = "author = ?"

COMMIT_SEARCH_AUTHOR_ADDRESSES_TEMPLATE = "lower(trim(author)) IN ({placeholders})"

COMMIT_FILES_FOR_NUMBERS_TEMPLATE = """
SELECT number, path, hash, status FROM commit_files
WHERE number IN ({placeholders})
""".strip()

# The status a path had in its newest commit below a number: what an early
# store that wrote no status letters needs to tell an add from a modify.
COMMIT_FILE_PRIOR_STATUS_QUERY = """
SELECT COALESCE(status, 'M') FROM commit_files
WHERE path = ? AND number < ?
ORDER BY number DESC
LIMIT 1
""".strip()

COMMIT_INSERT = """
INSERT INTO commits (number, id, summary, author, authored_at)
VALUES (?, ?, ?, ?, ?)
""".strip()

COMMIT_FILE_INSERT = """
INSERT OR REPLACE INTO commit_files (number, path, hash, status)
VALUES (?, ?, ?, ?)
""".strip()

# Dropping the history is for ONE case: history was rewritten under it, so the
# numbers point at commits that no longer exist. Anything else that reached for
# this would be renumbering, which is the defect the store was built to end.
# The file rows go with the commits through the cascade.
COMMIT_DELETE_ALL = "DELETE FROM commits"
