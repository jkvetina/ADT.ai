"""SQL for the local run-history store (`#251`).

SQLite, not Oracle, and it lives here for the same reason every other statement
in this package does: `tests/contracts/test_sql_home.py` binds the whole repo to
one home per module, so a reader looking for what a command writes has one place
to look and a statement cannot drift into a runner. ``shared/queries/apex_store``
and ``shared/queries/commit_store`` are the same shape for their stores.

The store keeps one row per run per schema plus one row per package that run
measured; the schema is created on first write and is idempotent, so a root that
already has the file is never migrated, only opened.
"""

from __future__ import annotations

#: Both tables plus the lookup index, run as one script on every write.
#:
#: ``percent`` is nullable on purpose: ``blocks_total = 0`` is Oracle collecting
#: nothing rather than a package scoring zero, and a NULL is the only value that
#: cannot be mistaken for a measurement later.
STORE_SCHEMA_SCRIPT = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    schema_name TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS package_coverage (
    run_id         INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    package        TEXT NOT NULL,
    lines          INTEGER NOT NULL DEFAULT 0,
    blocks_total   INTEGER NOT NULL DEFAULT 0,
    blocks_covered INTEGER NOT NULL DEFAULT 0,
    percent        REAL,
    PRIMARY KEY (run_id, package)
);
CREATE INDEX IF NOT EXISTS runs_by_schema ON runs (schema_name, run_id);
"""

#: The newest run recorded for one schema.
LATEST_RUN_QUERY = (
    "SELECT run_id FROM runs WHERE schema_name = ? ORDER BY run_id DESC LIMIT 1"
)

#: What that run measured. Unmeasured packages are filtered in SQL, so no caller
#: can mistake a stored NULL for a zero.
RUN_PERCENTS_QUERY = (
    "SELECT package, percent FROM package_coverage "
    "WHERE run_id = ? AND percent IS NOT NULL"
)

RUN_COUNT_QUERY = "SELECT COUNT(*) FROM runs WHERE schema_name = ?"

INSERT_RUN_STATEMENT = "INSERT INTO runs (schema_name, recorded_at) VALUES (?, ?)"

INSERT_PACKAGE_STATEMENT = (
    "INSERT INTO package_coverage "
    "(run_id, package, lines, blocks_total, blocks_covered, percent) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

#: Every run for this schema older than the newest ``?`` of them. ``LIMIT -1``
#: is SQLite's "no limit", which is what makes a bare ``OFFSET`` legal.
EXPIRED_RUNS_QUERY = (
    "SELECT run_id FROM runs WHERE schema_name = ? ORDER BY run_id DESC LIMIT -1 OFFSET ?"
)

#: Both halves of the purge. The ``IN`` list is built from ids this module just
#: selected, never from user input, and the caller parameterises it.
DELETE_PACKAGES_STATEMENT = "DELETE FROM package_coverage WHERE run_id IN ({marks})"
DELETE_RUNS_STATEMENT = "DELETE FROM runs WHERE run_id IN ({marks})"

FOREIGN_KEYS_PRAGMA = "PRAGMA foreign_keys = ON"

__all__ = [name for name in globals() if not name.startswith("_")]
