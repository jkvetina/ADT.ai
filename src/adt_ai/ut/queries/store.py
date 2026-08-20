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

#: How a run says what it measured, added after the store shipped (`#436`).
#:
#: `-name` narrows a run to the suites it names, so a filtered run measures a
#: handful of packages and a full one measures them all. Comparing the second
#: against the first reports every package the filter excluded as having no
#: previous figure, which is exactly what Jan's store did on 2026-08-20: four
#: single-package runs sat between two 42-package ones. The selection keys the
#: history, the same key `ut_timers.yaml` already stores its seconds under.
RUN_COLUMNS_PRAGMA = "PRAGMA table_info(runs)"

#: Nullable on purpose, and the NULL is load-bearing. A row written before this
#: column existed cannot say what it measured, and :func:`run_history` declines
#: to compare against one rather than guess: reading them all as full runs puts
#: a single-package run straight back into the baseline position, which is the
#: defect, and reading them all as filtered throws real history away.
ADD_VARIANT_STATEMENT = "ALTER TABLE runs ADD COLUMN variant TEXT"

#: Every run recorded for one schema and one selection, newest first.
#:
#: A list rather than the newest row alone, because the baseline is the newest
#: run whose figures actually DIFFER from the current ones. Coverage moves only
#: when a suite is deployed, so the run a reader is looking at is usually
#: identical to the one before it, and "the previous run" made the table empty
#: on every one of those (`#436`).
RUNS_QUERY = (
    "SELECT run_id, recorded_at FROM runs "
    "WHERE schema_name = ? AND variant = ? ORDER BY run_id DESC"
)

#: What that run measured. Unmeasured packages are filtered in SQL, so no caller
#: can mistake a stored NULL for a zero.
RUN_PERCENTS_QUERY = (
    "SELECT package, percent FROM package_coverage "
    "WHERE run_id = ? AND percent IS NOT NULL"
)

RUN_COUNT_QUERY = "SELECT COUNT(*) FROM runs WHERE schema_name = ?"

INSERT_RUN_STATEMENT = (
    "INSERT INTO runs (schema_name, recorded_at, variant) VALUES (?, ?, ?)"
)

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
