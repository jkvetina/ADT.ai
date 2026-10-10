"""What each tree's last clean codescan held, and the ratchet over it (ADT #1026).

A project rarely starts with no findings, so failing on every one would fail
every run until somebody fixed a backlog nobody scheduled. The baseline turns
the scan into a ratchet instead: a tree's last clean run is what it is allowed
to hold, a finding beyond it is new and fails, and a finding that disappears
drops out so the next run is held to less.

**A finding is its tree, file, rule, message and target, never its line.** Lines
move under every edit above them, so a baseline keyed on them would report
every finding below a change as new. The identity is counted as a multiset, so
two identical findings in one file are two, and a third is new.

**Only a clean run is recorded.** The caller decides what clean means (see
`codescan.verdict`); this module stores and reads. Placement follows `ut.db`:
one SQLite store per command under `config/internal/`, through `open_store`,
twenty runs kept per tree.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared.codescan_settings import CODESCAN_STORE
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.sqlite_store import open_store
from adt_ai.validate import queries
from adt_ai.validate.codescan_report import Finding

#: Version 1 is the first the store carries.
SCHEMA_VERSION = "1"

#: Runs kept per tree, the bound `ut.db` keeps for the same reason: a count is
#: what a reader can predict, where an age window keeps hundreds in a busy week.
RETAINED_RUNS = 20

Key = tuple[str, str, str, str]


@dataclass(frozen=True)
class Comparison:
    known        : int
    new          : int
    fixed        : int
    #: The findings beyond the baseline, in file order, component findings first.
    new_findings : tuple[Finding, ...]
    #: True when the tree had no baseline, so everything it holds is known.
    first_run    : bool


def store_path(root: Path | str) -> Path:
    """Where this project keeps its codescan baselines."""
    return internal_path(root, CODESCAN_STORE)


def baseline(root: Path | str, label: str) -> Counter[Key] | None:
    """The tree's newest recorded run, or ``None`` when it has none.

    Never creates the store, and an unreadable one is no baseline rather than a
    failed command: the first run after it records afresh.
    """
    path = store_path(root)
    if not path.is_file():
        return None
    try:
        with contextlib.closing(_connect(path)) as connection:
            row = connection.execute(queries.LATEST_RUN_QUERY, (label,)).fetchone()
            if row is None:
                return None
            return Counter({
                (str(file), str(rule), str(message), str(target)): int(count)
                for file, rule, message, target, count in connection.execute(
                    queries.RUN_FINDINGS_QUERY, (row[0],)
                )
            })
    except sqlite3.Error:
        return None


def record(root: Path | str, label: str, findings: Iterable[Finding]) -> bool:
    """Store ``findings`` as the tree's new baseline; ``False`` when it could not be."""
    counts = Counter(finding.key for finding in findings)
    try:
        with contextlib.closing(_connect(store_path(root))) as connection, connection:
            cursor = connection.execute(queries.INSERT_RUN_STATEMENT, (label,))
            run_id = cursor.lastrowid
            connection.executemany(
                queries.INSERT_FINDING_STATEMENT,
                [(run_id, *key, count) for key, count in sorted(counts.items())],
            )
            _prune(connection, label)
    except (sqlite3.Error, OSError):
        return False
    return True


def compare(previous: Counter[Key] | None, findings: Iterable[Finding]) -> Comparison:
    """This run against the baseline: known, new and fixed, as multisets.

    Which of two identical findings is the new one cannot be told without a
    line number, so the later ones in file order are listed as new.
    """
    ordered = sorted(findings, key=finding_order)
    if previous is None:
        return Comparison(len(ordered), 0, 0, (), first_run=True)
    current = Counter(finding.key for finding in ordered)
    allowance = Counter(previous)
    kept: list[Finding] = []
    new: list[Finding] = []
    for finding in ordered:
        if allowance[finding.key] > 0:
            allowance[finding.key] -= 1
            kept.append(finding)
        else:
            new.append(finding)
    fixed = sum((previous - current).values())
    return Comparison(len(kept), len(new), fixed, tuple(new), first_run=False)


def finding_order(finding: Finding) -> tuple[str, int, int, str]:
    """File order, a component finding (no line) ahead of the positioned ones."""
    return (finding.file, finding.line or 0, finding.column or 0, finding.rule)


def _connect(path: Path) -> sqlite3.Connection:
    return open_store(
        path,
        schema      = queries.STORE_SCHEMA_SCRIPT,
        version     = SCHEMA_VERSION,
        row_factory = None,
    )


def _prune(connection: sqlite3.Connection, label: str) -> None:
    """Drop all but the newest runs of one tree, never another tree's."""
    doomed = [
        row[0] for row in connection.execute(queries.EXPIRED_RUNS_QUERY, (label, RETAINED_RUNS))
    ]
    if not doomed:
        return
    marks = ",".join("?" for _ in doomed)
    # Explicit rather than trusting ON DELETE CASCADE, which is a per-connection
    # pragma a future reader of this file might not set.
    connection.execute(queries.DELETE_FINDINGS_STATEMENT.format(marks=marks), doomed)
    connection.execute(queries.DELETE_RUNS_STATEMENT.format(marks=marks), doomed)


__all__ = [
    "RETAINED_RUNS",
    "SCHEMA_VERSION",
    "Comparison",
    "baseline",
    "compare",
    "finding_order",
    "record",
    "store_path",
]
