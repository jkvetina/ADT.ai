"""What the previous run measured, so this one can say what moved (`#251`).

Every `ut` run was a console snapshot: nothing recorded what the figure was last
time, so a drop was invisible unless somebody remembered the number. This module
is the memory, and it is what turns the report into a ratchet.

**Placement follows the precedent, it is not a new idea.** One SQLite store per
command under ``config/internal/``, reached through
:func:`~adt_ai.shared.internal_paths.internal_path`, exactly as ``apex.db``,
``dependencies.db`` and ``flow.db`` already are. Sharing ``dependencies.db`` was
considered and rejected for the same reason those three are separate: a store's
lifetime belongs to the command that writes it, and a `ut` retention sweep has
no business deleting rows a `dependencies -refresh` is reading.

**Read before you write.** :func:`previous_percents` answers "what did the run
BEFORE this one measure", so the runner must call it before
:func:`record_run`. Recording first makes a run compare against itself and every
delta is zero, which looks exactly like a stable schema.

**An absent measurement is not a zero.** ``blocks_total == 0`` means Oracle
collected nothing, natively compiled code, a package no test entered, so there is
no percentage to store and none to compare. Those rows are stored (the run did
list the package) but carry a NULL percent, and they can neither gain nor lose a
figure. Same rule the `COVERAGE` cells already follow: blank means unmeasured,
never zero.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from adt_ai.shared.internal_paths import internal_path
from adt_ai.ut import queries
from adt_ai.ut.inventory import PackageCoverage, SuitePackage

#: The store's filename under ``config/internal/``.
STORE_NAME = "ut.db"

#: What this store was called while the command was ``ut3`` (ADT #390).
#:
#: The history IS the feature: a run compares against what the previous one
#: measured, so shipping the rename with no carry-over would reset every
#: project's ratchet on upgrade, and the first run afterwards would report no
#: comparison rather than an error anybody could act on.
LEGACY_STORE_NAME = "ut3.db"

#: How many runs per schema the store keeps.
#:
#: Jan's call, 2026-08-17: retention is by run count rather than by age. A count
#: is the bound a reader can predict, twenty runs is twenty runs whether they
#: happened in a day or a quarter, where an age window silently keeps hundreds
#: during a heavy week and nothing at all after a quiet month, which is precisely
#: when a comparison is most wanted.
DEFAULT_RETAINED_RUNS = 20


@dataclass(frozen=True)
class CoverageChange:
    """One suite whose target package's ratio moved since the previous run.

    ``was`` and ``delta`` are ``None`` for a package the previous run did not
    measure. That is a distinct cell from a move of zero, and the renderer prints
    it as such: a package appearing for the first time has not gained 30 points,
    it has no comparison at all.
    """

    suite   : str
    package : str
    was     : float | None
    now     : float
    delta   : float | None


def store_path(root: Path | str) -> Path:
    """Where this project root keeps its run history.

    Takes over a store left under the old command's name on the way past, once,
    so a project upgrading across ADT #390 keeps the runs it has already
    recorded. A file already sitting under the current name wins and the
    leftover is left where it is: both can exist when an old checkout writes the
    old name again, and overwriting the live store with the stale one would lose
    every run since the upgrade. Deleting it is not this function's call either,
    it is the user's data and nothing here can prove it is superseded.
    """
    path = internal_path(root, STORE_NAME)
    if not path.exists():
        legacy = path.with_name(LEGACY_STORE_NAME)
        if legacy.is_file():
            with contextlib.suppress(OSError):
                legacy.rename(path)
    return path


def previous_percents(root: Path | str, schema: str) -> dict[str, float]:
    """Measured percentages from the latest recorded run, keyed by package name.

    Empty for a root with no store, a schema with no runs, and any store the
    process cannot read: an unreadable history is a missing comparison, never a
    reason to fail the command the user actually asked for.

    Packages the run could not measure are absent rather than zero.
    """
    path = store_path(root)
    if not path.is_file():
        return {}
    try:
        with _connect(path) as connection:
            row = connection.execute(
                queries.LATEST_RUN_QUERY,
                (_key(schema),),
            ).fetchone()
            if row is None:
                return {}
            return {
                str(package): float(percent)
                for package, percent in connection.execute(
                    queries.RUN_PERCENTS_QUERY,
                    (row[0],),
                )
            }
    except sqlite3.Error:
        return {}


def record_run(
    root: Path | str,
    schema: str,
    packages: tuple[PackageCoverage, ...],
    *,
    retain: int = DEFAULT_RETAINED_RUNS,
) -> int | None:
    """Store what this run measured, then prune to the last ``retain`` runs.

    Returns the new run id, or ``None`` when the store could not be written. A
    project root that is read-only still gets its test run, its report and its
    exit code; only the history is skipped.
    """
    path = store_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(path) as connection:
            connection.executescript(queries.STORE_SCHEMA_SCRIPT)
            cursor = connection.execute(
                queries.INSERT_RUN_STATEMENT,
                (_key(schema), datetime.now(UTC).isoformat(timespec="seconds")),
            )
            run_id = int(cursor.lastrowid)
            connection.executemany(
                queries.INSERT_PACKAGE_STATEMENT,
                [
                    (
                        run_id,
                        package.name.upper(),
                        int(package.lines),
                        int(package.blocks_total),
                        int(package.blocks_covered),
                        package.percent,
                    )
                    for package in packages
                ],
            )
            _prune(connection, _key(schema), retain)
            connection.commit()
            return run_id
    except (sqlite3.Error, OSError):
        return None


def run_count(root: Path | str, schema: str) -> int:
    """How many runs the store holds for one schema."""
    path = store_path(root)
    if not path.is_file():
        return 0
    try:
        with _connect(path) as connection:
            row = connection.execute(
                queries.RUN_COUNT_QUERY, (_key(schema),)
            ).fetchone()
            return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def coverage_changes(
    previous: dict[str, float],
    suites: tuple[SuitePackage, ...],
    current: tuple[PackageCoverage, ...],
) -> tuple[CoverageChange, ...]:
    """The suites whose target package's ratio is not what it was last run.

    Only what MOVED. An unchanged suite is omitted, which is the whole reason the
    table earns its place above two summaries that already list everything: a
    reader opening a verbose run wants the diff, not a third copy of the roster.

    A suite ``ut_match`` could not pair contributes nothing, the same honest
    silence its `COVERAGE` cell prints, and so does one whose package this run
    could not measure, because a blank cannot have moved.

    Ordered by the size of the move, largest first, so a drop cannot hide under a
    long tail of rounding. Packages with no previous figure sort last: they carry
    no movement to rank and the reader has nothing to act on beyond their
    presence.
    """
    measured = {package.name.upper(): package for package in current}
    changes: list[CoverageChange] = []

    for suite in suites:
        if not suite.target:
            continue
        package = measured.get(suite.target.upper())
        if package is None or package.percent is None:
            continue
        was = previous.get(suite.target.upper())
        now = package.percent
        if was is None:
            changes.append(
                CoverageChange(
                    suite   = suite.name,
                    package = package.name,
                    was     = None,
                    now     = now,
                    delta   = None,
                )
            )
            continue
        delta = round(now - was, 1)
        if delta == 0:
            continue
        changes.append(
            CoverageChange(
                suite   = suite.name,
                package = package.name,
                was     = was,
                now     = now,
                delta   = delta,
            )
        )

    # `delta is None` sorts last; among the rest the largest absolute move leads,
    # with the suite name breaking ties so the order is stable run to run.
    changes.sort(key=lambda change: (change.delta is None, -abs(change.delta or 0), change.suite))
    return tuple(changes)


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(queries.FOREIGN_KEYS_PRAGMA)
    return connection


def _key(schema: str) -> str:
    """Schemas are Oracle identifiers, so the store keys on one spelling.

    `-schema ICT_OWNER` and `-schema ict_owner` are the same schema, and
    `dependencies.db` already carries the scar of not normalising: it holds both
    casings side by side with two refresh stamps five days apart.
    """
    return (schema or "").upper()


def _prune(connection: sqlite3.Connection, schema: str, retain: int) -> None:
    """Drop all but the newest ``retain`` runs for one schema.

    Per schema, never globally: a project testing two schemas would otherwise
    have each run halve the other's history.
    """
    if retain <= 0:
        return
    doomed = [
        row[0]
        for row in connection.execute(
            queries.EXPIRED_RUNS_QUERY,
            (schema, retain),
        )
    ]
    if not doomed:
        return
    marks = ",".join("?" for _ in doomed)
    # Explicit, rather than trusting ON DELETE CASCADE: the pragma is set per
    # connection and a future reader opening this file without it would leave
    # orphan rows behind.
    connection.execute(queries.DELETE_PACKAGES_STATEMENT.format(marks=marks), doomed)
    connection.execute(queries.DELETE_RUNS_STATEMENT.format(marks=marks), doomed)


with contextlib.suppress(NameError):  # pragma: no cover - module-surface guard
    __all__ = [name for name in globals() if not name.startswith("_")]
