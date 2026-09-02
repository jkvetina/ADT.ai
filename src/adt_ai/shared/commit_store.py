"""The per-branch commit store, and the numbering rule that is its whole point.

**A commit number is allocated once and never re-derived.** It is a surrogate
key, not a position in history. ADT.ai used to number by position in
`git log --reverse` offset from `rev-list --count`, and that produced three
defects, all of them measurable on a four-commit repo with one merged side
branch:

* a merge of an older-dated branch **renumbered commits already cached**,
  because merged-in commits sort in by date and push everything below them up;
* an incremental run and a full rebuild **disagreed about the same commit**, so
  deleting a cache silently rewrote every number a patch folder had recorded;
* a bounded window **left holes**, since only the newest N commits were kept.

Allocation here is monotonic and additive, so none of the three can happen:
new commits take numbers above the tip, older commits pulled in by a wider
`patch_history_bottom_days` take numbers below the floor, and a commit that
already carries a number keeps it.

The seed exists for that bottom-days window. A first build bounded to a year of
an 85,000-commit repo would otherwise start at 1 and leave no room underneath,
so the caller seeds the floor at the oldest included commit's true position and
the range below stays free. The seed is read **once**, on an empty branch: a
later run never re-seeds, because re-deriving a floor is the same mistake as
re-deriving a number.

SQLite rather than a text file because the corpus is large: measured on a real
20,000-commit corpus, a text cache costs a full parse (0.67 s and about 512 MB
resident) before it can answer "the newest 40 commits", which is all `patch`
ever needs, while the same question here is an indexed lookup.

The file follows the store convention since ADT #642 (`docs/storage.md`): the
version lives in `_meta`, the author date in `authored_at` as
`YYYY-MM-DD HH:MM:SS+HH:MM`, git's own instant with the author's offset kept
because it is the one stamp ADT reads off somebody else's clock, and the
opener is the shared one. A version 1 file is lifted in place on open.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adt_ai.shared import queries
from adt_ai.shared.sqlite_store import Migration, open_store

SCHEMA_VERSION = "2"

#: Version 1 to 2 (ADT #642): `meta` to `_meta`, `date` to `authored_at`, and
#: the foreign key `commit_files` always kept by hand declared with a cascade.
#: The step starts from ``None`` because a version 1 file keeps its row in
#: `meta`, which to the shared opener is a file carrying no version at all.
def _lift_1(connection: sqlite3.Connection) -> None:
    connection.executescript(queries.COMMIT_STORE_LIFT_1)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(None, "2", _lift_1),
)


@dataclass(frozen=True)
class StoredCommit:
    """One commit as the store holds it.

    ``statuses`` is git's per-file letter (``A``/``M``/``D``). The YAML cache
    never carried it, so `search_repo` guessed ("M" when the path appeared in an
    older commit, else "A") and the patch install script could not split
    NEW/DELETED/MODIFIED from anything but that guess. ``deleted`` stays as its
    own list for the old-ADT payload shape, and is derivable from ``statuses``.
    """

    #: 0 while the record is still unallocated. A scanner hands the store what
    #: git told it and the store decides the number, so a caller that filled
    #: this in itself would be re-deriving the one thing it must not.
    number: int = 0
    id: str = ""
    summary: str = ""
    author: str = ""
    #: The author date. Stored as `authored_at`, `YYYY-MM-DD HH:MM:SS+HH:MM`;
    #: git's ISO form with its `T` is accepted here and normalised on write.
    date: str = ""
    files: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    statuses: dict[str, str] = field(default_factory=dict)
    patch: str | None = None

    @property
    def commit_hash(self) -> str:
        return self.id


def authored_stamp(value: str) -> str:
    """The author date as the store keeps it: git's instant, the `T` a space.

    `git log --format=%aI` prints `2026-08-22T12:00:25+02:00`; the store holds
    `2026-08-22 12:00:25+02:00`, the same instant on the same clock in the
    `YYYY-MM-DD HH:MM:SS` shape every other store uses, with the offset kept
    because the clock is the author's rather than this machine's. Every reader
    parses with `datetime.fromisoformat`, which accepts both spellings.
    """
    if len(value) > 10 and value[10] == "T":
        return f"{value[:10]} {value[11:]}"
    return value


class CommitStore:
    """Query and write API over one branch-scoped commit database."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open(cls, db_path: str | Path) -> CommitStore:
        # Close-on-failure (ADT #510) is the opener's. Plain tuples rather than
        # `sqlite3.Row`: every read here indexes by position.
        connection = open_store(
            db_path,
            schema      = queries.COMMIT_STORE_SCHEMA,
            version     = SCHEMA_VERSION,
            migrations  = MIGRATIONS,
            row_factory = None,
        )
        return cls(connection)

    def close(self) -> None:
        self.connection.close()

    def claim_branch(self, branch: str) -> None:
        """Bind this file to one branch, rejecting a filename collision."""
        row = self.connection.execute(queries.META_BRANCH_QUERY).fetchone()
        recorded = str(row[0]) if row else ""
        if not recorded:
            existing = [
                str(item[0])
                for item in self.connection.execute(queries.COMMIT_BRANCHES_QUERY)
            ]
            if len(existing) > 1:
                raise ValueError(f"commit store already contains multiple branches: {existing}")
            recorded = existing[0] if existing else branch
            self.connection.execute(queries.META_BRANCH_INSERT, (recorded,))
            self.connection.commit()
        if recorded != branch:
            raise ValueError(
                f"branch {branch!r} maps to a commit store already owned by {recorded!r}; "
                "rename one branch to keep cache filenames distinct"
            )

    def __enter__(self) -> CommitStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- reads -------------------------------------------------------------

    def floor(self, branch: str) -> int | None:
        lowest: int | None = self.connection.execute(
            queries.COMMIT_FLOOR_QUERY, (branch,)
        ).fetchone()[0]
        return lowest

    def ceiling(self, branch: str) -> int | None:
        highest: int | None = self.connection.execute(
            queries.COMMIT_CEILING_QUERY, (branch,)
        ).fetchone()[0]
        return highest

    def numbers(self, branch: str) -> dict[str, int]:
        """Every stored commit hash mapped to its number."""
        return {
            row[0]: row[1]
            for row in self.connection.execute(queries.COMMIT_NUMBERS_QUERY, (branch,))
        }

    def tip(self, branch: str) -> StoredCommit | None:
        found = self.recent(branch, 1)
        return found[0] if found else None

    def recent(self, branch: str, limit: int) -> list[StoredCommit]:
        """The newest ``limit`` commits, newest first.

        This is the query `patch` runs, and it never materialises the branch:
        that is the difference between a bounded index scan and parsing a
        gigabyte of text to read forty rows off the end of it.
        """
        rows = self.connection.execute(queries.COMMIT_RECENT_QUERY, (branch, limit)).fetchall()
        return self._with_files(branch, rows)

    def records(self, branch: str) -> list[StoredCommit]:
        """Every commit on the branch, oldest first."""
        rows = self.connection.execute(queries.COMMIT_RECORDS_QUERY, (branch,)).fetchall()
        return self._with_files(branch, rows)

    def by_path(self, branch: str, path: str) -> list[int]:
        """Commit numbers that touched ``path``, newest first."""
        return [
            row[0]
            for row in self.connection.execute(queries.COMMIT_BY_PATH_QUERY, (branch, path))
        ]

    def _with_files(self, branch: str, rows: list[tuple[Any, ...]]) -> list[StoredCommit]:
        if not rows:
            return []
        numbers = [row[1] for row in rows]
        placeholders = ",".join("?" for _ in numbers)
        files: dict[int, dict[str, str]] = {number: {} for number in numbers}
        statuses: dict[int, dict[str, str]] = {number: {} for number in numbers}
        deleted: dict[int, list[str]] = {number: [] for number in numbers}
        for number, path, hash_, status in self.connection.execute(
            queries.COMMIT_FILES_FOR_NUMBERS_TEMPLATE.format(placeholders=placeholders),
            (branch, *numbers),
        ):
            if status == "D":
                deleted[number].append(path)
            else:
                files[number][path] = hash_
            if status:
                statuses[number][path] = status
        return [
            StoredCommit(
                number   = row[1],
                id       = row[2],
                summary  = row[3] or "",
                author   = row[4] or "",
                date     = row[5] or "",
                files    = files[row[1]],
                deleted  = sorted(deleted[row[1]]),
                statuses = statuses[row[1]],
                patch    = row[6],
            )
            for row in rows
        ]

    # -- allocation --------------------------------------------------------

    def allocate(
        self,
        branch: str,
        records: Iterable[StoredCommit],
        *,
        seed: int | None = None,
    ) -> list[int]:
        """Number ``records`` (oldest first) at or above the current tip.

        A commit already carrying a number keeps it and is returned unchanged,
        so an overlapping re-run is a no-op rather than a second allocation.
        ``seed`` is honoured only while the branch is empty.
        """
        ordered = list(records)
        existing = self.numbers(branch)
        ceiling = self.ceiling(branch)
        # The seed is read only while the branch is empty. Re-seeding a branch
        # that already has commits is the same mistake as re-deriving a number.
        next_number = (seed if seed is not None else 1) if ceiling is None else ceiling + 1
        assigned: list[int] = []
        fresh: list[tuple[int, StoredCommit]] = []
        for item in ordered:
            if item.id in existing:
                assigned.append(existing[item.id])
                continue
            assigned.append(next_number)
            fresh.append((next_number, item))
            next_number += 1
        self._write(branch, fresh)
        return assigned

    def backfill(self, branch: str, records: Iterable[StoredCommit]) -> list[int]:
        """Number ``records`` (oldest first) below the current floor.

        This is the `patch_history_bottom_days` path: raising the window pulls
        older commits in, and they cannot take numbers above the tip without
        claiming to be newer than commits they precede. Allocating downward from
        the floor keeps every number already handed out exactly where it was,
        which is the invariant the whole card rests on.
        """
        ordered = list(records)
        if not ordered:
            return []
        existing = self.numbers(branch)
        wanted = [item for item in ordered if item.id not in existing]
        floor = self.floor(branch)
        if floor is None:
            return self.allocate(branch, ordered)
        first = floor - len(wanted)
        if first < 1:
            raise ValueError(
                f"backfilling {len(wanted)} commit(s) below floor {floor} would run "
                f"below 1; the store cannot hold them without renumbering"
            )
        fresh: list[tuple[int, StoredCommit]] = []
        next_number = first
        assigned: list[int] = []
        for item in ordered:
            if item.id in existing:
                assigned.append(existing[item.id])
                continue
            assigned.append(next_number)
            fresh.append((next_number, item))
            next_number += 1
        self._write(branch, fresh)
        return assigned

    def reset(self, branch: str) -> None:
        """Forget everything on ``branch``.

        The one legitimate caller is rewritten history: after a rebase or a
        force-push the stored numbers point at commits the branch no longer
        has, so they describe nothing. Any other use is a renumbering.
        """
        self.connection.execute(queries.COMMIT_DELETE_BRANCH, (branch,))
        self.connection.commit()

    def _all_numbers(self, branch: str) -> list[int]:
        return list(self.numbers(branch).values())

    def _write(self, branch: str, fresh: list[tuple[int, StoredCommit]]) -> None:
        if not fresh:
            return
        self.connection.executemany(
            queries.COMMIT_INSERT,
            [
                (
                    branch, number, item.id, item.summary, item.author,
                    authored_stamp(item.date), item.patch,
                )
                for number, item in fresh
            ],
        )
        rows: list[tuple[Any, ...]] = []
        for number, item in fresh:
            for path, hash_ in item.files.items():
                # NULL, not a default letter: a caller that has no status does
                # not know whether a file was added or modified, and writing
                # "M" would be a guess indistinguishable from git's own answer.
                rows.append((branch, number, path, hash_, item.statuses.get(path)))
            for path in item.deleted:
                rows.append((branch, number, path, None, "D"))
        if rows:
            self.connection.executemany(queries.COMMIT_FILE_INSERT, rows)
        self.connection.commit()

    # -- verification ------------------------------------------------------

    def verify(self, branch: str) -> list[str]:
        """Problems with the branch's numbering, empty when it is sound.

        Contiguity is checkable because allocation is additive: floor to ceiling
        with no gap is the only shape allocate/backfill can produce, so a gap
        means something outside this module wrote the store.
        """
        low, high, count = self.connection.execute(
            queries.COMMIT_SPAN_QUERY, (branch,)
        ).fetchone()
        if count == 0:
            return []
        problems: list[str] = []
        expected = high - low + 1
        if expected != count:
            problems.append(
                f"hole in {branch}: numbers {low} to {high} span {expected} slots "
                f"but only {count} commits are stored"
            )
        return problems
