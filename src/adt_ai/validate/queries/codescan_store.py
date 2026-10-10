"""SQL for the local codescan baseline store, `config/internal/codescan.db` (ADT #1026).

SQLite, not Oracle, and here for the reason every statement in the repo is in a
`queries/` package: `tests/contracts/test_sql_home.py` binds each module to one
home, so a reader looking for what `validate` writes has one place to look.

One row per recorded run per tree, plus one row per distinct finding that run
held with how many times it held it. A finding's identity carries no line or
column: lines move under every edit above them, and a baseline keyed on them
would report every finding below a change as new.
"""

from __future__ import annotations

from adt_ai.shared.queries.sqlite_store import META_TABLE_DDL

#: Both tables plus the lookup index, run as one script on every open.
STORE_SCHEMA_SCRIPT = META_TABLE_DDL + """
CREATE TABLE IF NOT EXISTS runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    label  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    run_id      INTEGER NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    file        TEXT NOT NULL,
    rule        TEXT NOT NULL,
    message     TEXT NOT NULL,
    target      TEXT NOT NULL,
    occurrences INTEGER NOT NULL,
    PRIMARY KEY (run_id, file, rule, message, target)
);
CREATE INDEX IF NOT EXISTS ix_runs_label ON runs (label, run_id);
"""

#: The newest recorded run of one tree, the baseline.
LATEST_RUN_QUERY = "SELECT run_id FROM runs WHERE label = ? ORDER BY run_id DESC LIMIT 1"

#: What that run held, one row per distinct finding.
RUN_FINDINGS_QUERY = (
    "SELECT file, rule, message, target, occurrences FROM findings WHERE run_id = ?"
)

INSERT_RUN_STATEMENT = "INSERT INTO runs (label) VALUES (?)"

INSERT_FINDING_STATEMENT = (
    "INSERT INTO findings (run_id, file, rule, message, target, occurrences) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

#: Every run of one tree older than the newest ``?`` of them. ``LIMIT -1`` is
#: SQLite's "no limit", which is what makes a bare ``OFFSET`` legal.
EXPIRED_RUNS_QUERY = (
    "SELECT run_id FROM runs WHERE label = ? ORDER BY run_id DESC LIMIT -1 OFFSET ?"
)

#: Both halves of the purge. The ``IN`` list is built from ids just selected,
#: never from input, and the caller parameterises it.
DELETE_FINDINGS_STATEMENT = "DELETE FROM findings WHERE run_id IN ({marks})"
DELETE_RUNS_STATEMENT = "DELETE FROM runs WHERE run_id IN ({marks})"

__all__ = [
    "DELETE_FINDINGS_STATEMENT",
    "DELETE_RUNS_STATEMENT",
    "EXPIRED_RUNS_QUERY",
    "INSERT_FINDING_STATEMENT",
    "INSERT_RUN_STATEMENT",
    "LATEST_RUN_QUERY",
    "RUN_FINDINGS_QUERY",
    "STORE_SCHEMA_SCRIPT",
]
