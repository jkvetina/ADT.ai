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

**Read before you write.** :func:`run_history` answers "what did the runs BEFORE
this one measure", so the runner must call it before :func:`record_run`.
Recording first makes a run compare against itself and every delta is zero,
which looks exactly like a stable schema.

**And the baseline is the last run that was DIFFERENT, not the last run.** The
first version compared against whatever was recorded most recently, which reads
as the obvious thing to do and made the table useless: coverage moves when a
suite is deployed, a reader looks at the table some run after that, and every
run in between is identical to the one before it. Measured on Jan's own store,
2026-08-20 (`#436`): of the 20 runs it retained, exactly **two** consecutive
pairs had moved at all, so eighteen renders were a header with nothing under it.
His report was *"this table is always empty, even after you added some tests"*,
and it was.

**A run is comparable only to one that measured the same selection.** `-name`
narrows a run to the suites it names, so its figures describe a handful of
packages; four such single-package runs sat in that same store immediately
before a 42-package one, and comparing across them reported 41 packages as
having no previous figure. :func:`run_history` is keyed by the selection for
that reason, the same key `ut/timers.py` already stores its seconds under.

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
from adt_ai.shared.sqlite_store import Migration, open_store
from adt_ai.ut import queries
from adt_ai.ut.inventory import PackageCoverage, SuitePackage

#: The store's filename under ``config/internal/``.
STORE_NAME = "ut.db"

#: The first version the file carries (ADT #642). A file from before it has no
#: `_meta`, an index with no prefix, an ISO stamp with a `T`, and possibly no
#: `variant` column; :func:`_lift_legacy` fixes all four, history intact.
SCHEMA_VERSION = "1"

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

#: The selection key a run with no `-name` filter records itself under.
#:
#: Spelled the same as `ut/timers.py`'s, and deliberately not imported from it:
#: that module owns how long a run took and this one owns what it measured, and
#: a shared constant between them would be the only edge either has on the
#: other. Both read `variant_key`'s output, which is where the spelling is
#: actually decided.
ALL_SUITES_VARIANT = "%"


@dataclass(frozen=True)
class RunSnapshot:
    """One recorded run: when it happened and what it measured.

    ``percents`` holds only the packages that run could measure, so two
    snapshots comparing equal means the two runs found the same thing, which is
    the test :func:`baseline_percents` walks back on.
    """

    run_id      : int
    recorded_at : str
    percents    : dict[str, float]


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


def run_history(
    root: Path | str,
    schema: str,
    variant: str = ALL_SUITES_VARIANT,
) -> tuple[RunSnapshot, ...]:
    """Every recorded run for one schema and selection, newest first.

    Empty for a root with no store, a schema with no runs of that selection, and
    any store the process cannot read: an unreadable history is a missing
    comparison, never a reason to fail the command the user actually asked for.

    Packages a run could not measure are absent from its snapshot rather than
    zero, so a package Oracle collected nothing for can neither gain nor lose a
    figure.

    **A row that predates the selection column is not returned at all.** It
    carries a NULL variant and matches no live key, because nothing stored can
    say whether it was a full run or a `-name` one: reading them as full runs
    puts a single-package run back in the baseline position, which is the defect
    `#436` fixed, and reading them as filtered discards real history. So the
    first run after an upgrade reports no comparison and the second compares
    normally, which costs one round and cannot report a wrong one.
    """
    path = store_path(root)
    if not path.is_file():
        return ()
    try:
        with contextlib.closing(_connect(path)) as connection, connection:
            runs = connection.execute(queries.RUNS_QUERY, (_key(schema), variant)).fetchall()
            return tuple(
                RunSnapshot(
                    run_id      = int(run_id),
                    recorded_at = str(recorded_at),
                    percents    = {
                        str(package): float(percent)
                        for package, percent in connection.execute(
                            queries.RUN_PERCENTS_QUERY,
                            (run_id,),
                        )
                    },
                )
                for run_id, recorded_at in runs
            )
    except sqlite3.Error:
        return ()


def measured_percents(packages: tuple[PackageCoverage, ...]) -> dict[str, float]:
    """This run's figures in the shape the store keeps them.

    One function so the write and the comparison cannot disagree about the
    shape: same upper-cased key, same rule that an unmeasured package is absent
    rather than zero. A mismatch here would make every run look different from
    every other, which is the same empty table by the opposite route.
    """
    return {
        package.name.upper(): package.percent
        for package in packages
        if package.percent is not None
    }


def baseline_percents(
    history: tuple[RunSnapshot, ...],
    current: dict[str, float],
) -> dict[str, float] | None:
    """The newest recorded run whose figures are not the ones this run measured.

    **Not simply the newest run.** A `ut` run changes no coverage by itself, so
    consecutive runs of unchanged code record identical figures and comparing
    against the immediately previous one reports nothing moved, on the run
    after a deploy, which is the one a reader opens the table for. Walking back
    to the last run that was actually different is what makes the table say what
    the reader came to find out, and it costs nothing when the previous run
    already differs: the walk stops on its first candidate.

    **``None``, never ``{}``, when no earlier run differs.** The two are opposite
    reports and an empty mapping cannot tell them apart: an empty *baseline* says
    every package here is new and appearing for the first time, which is what
    :func:`coverage_changes` renders it as, while "nothing to compare against"
    has to render as no rows at all. Returning the mapping for one and ``None``
    for the other is what keeps a schema whose coverage simply has not moved from
    printing its entire roster as new.
    """
    for snapshot in history:
        if snapshot.percents != current:
            return dict(snapshot.percents)
    return None


def record_run(
    root: Path | str,
    schema: str,
    packages: tuple[PackageCoverage, ...],
    *,
    variant: str = ALL_SUITES_VARIANT,
    retain: int = DEFAULT_RETAINED_RUNS,
) -> int | None:
    """Store what this run measured, then prune to the last ``retain`` runs.

    Returns the new run id, or ``None`` when the store could not be written. A
    project root that is read-only still gets its test run, its report and its
    exit code; only the history is skipped.

    ``variant`` is the run's own `-name` selection, so a filtered run keeps its
    own history instead of standing in front of the full runs either side of it.
    """
    path = store_path(root)
    try:
        with contextlib.closing(_connect(path)) as connection, connection:
            cursor = connection.execute(
                queries.INSERT_RUN_STATEMENT,
                (
                    _key(schema),
                    datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                    variant,
                ),
            )
            run_id = cursor.lastrowid
            if run_id is None:  # pragma: no cover - sqlite sets it on an INSERT
                return None
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
        with contextlib.closing(_connect(path)) as connection, connection:
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
    """The file at version 1, through the shared opener; plain tuple rows."""
    return open_store(
        path,
        schema      = queries.STORE_SCHEMA_SCRIPT,
        version     = SCHEMA_VERSION,
        migrations  = MIGRATIONS,
        row_factory = None,
    )


def _lift_legacy(connection: sqlite3.Connection) -> None:
    """Bring a file written before version 1 to the shape the schema declares.

    ``CREATE TABLE IF NOT EXISTS`` is a no-op on a table that is already there,
    so the schema script cannot deliver a new column to an existing store and
    the column has to arrive here. Idempotent by inspection rather than by
    catching the error, so a genuine failure is not swallowed with it.
    """
    columns = {row[1] for row in connection.execute(queries.RUN_COLUMNS_PRAGMA)}
    if "variant" not in columns:
        connection.execute(queries.ADD_VARIANT_STATEMENT)
    connection.execute(queries.DROP_LEGACY_INDEX_STATEMENT)
    connection.execute(queries.LIFT_RECORDED_AT_STATEMENT)


MIGRATIONS: tuple[Migration, ...] = (Migration(None, "1", _lift_legacy),)


def _key(schema: str) -> str:
    """Schemas are Oracle identifiers, so the store keys on one spelling.

    `-schema APP_OWNER` and `-schema app_owner` are the same schema, and
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
