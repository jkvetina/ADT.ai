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

**One file is one branch** (ADT #873). `commit_cache.open_store` binds the file
to its branch before any read and `_meta.branch_name` records which, so no
method takes a branch and no row repeats one. Every read is bounded: by a
limit, by a list of ids, by a month, or by an aggregate over the key. Nothing
here reads the whole branch, because a 10,000-commit repository made that the
slowest thing any command did.

The file follows the store convention since ADT #642 (`docs/storage.md`): the
version lives in `_meta`, the author date in `authored_at` as
`YYYY-MM-DD HH:MM:SS+HH:MM`, git's own instant with the author's offset kept
because it is the one stamp ADT reads off somebody else's clock, and the
opener is the shared one. Older files are lifted in place on open.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from adt_ai.shared import queries
from adt_ai.shared.sqlite_store import Migration, open_store

SCHEMA_VERSION = "4"

#: How many ids one `IN` list carries. Well under SQLite's variable limit on
#: every build ADT runs on, and large enough that a scan window is one query.
IN_CHUNK = 900

#: The `number < ?` bound of a search that starts at the tip.
ABOVE_EVERY_NUMBER = 2**62


#: Version 1 to 2 (ADT #642): `meta` to `_meta`, `date` to `authored_at`, and
#: the foreign key `commit_files` always kept by hand declared with a cascade.
#: The step starts from ``None`` because a version 1 file keeps its row in
#: `meta`, which to the shared opener is a file carrying no version at all.
def _lift_1(connection: sqlite3.Connection) -> None:
    connection.executescript(queries.COMMIT_STORE_LIFT_1)


#: Version 2 to 3 (ADT #851): `commits.patch` goes. It was decided at write time
#: under a hardcoded `patch/` and read by nothing; the marker is a reading of the
#: file rows with the project's `patch_root`, taken by `commit_discovery`.
def _lift_2(connection: sqlite3.Connection) -> None:
    connection.executescript(queries.COMMIT_STORE_LIFT_2)


#: Version 3 to 4 (ADT #873): `branch` goes from both tables. A file holding
#: two branches cannot be flattened into one, so it is refused before anything
#: moves, with the message `claim_branch` always gave that case.
def _lift_3(connection: sqlite3.Connection) -> None:
    branches = [str(row[0]) for row in connection.execute(queries.COMMIT_V3_BRANCHES_QUERY)]
    if len(branches) > 1:
        raise ValueError(f"commit store already contains multiple branches: {branches}")
    connection.executescript(queries.COMMIT_STORE_LIFT_3)
    # The rebuild frees every page the old tables held, and SQLite keeps them.
    # Measured on a real 8,790-commit store: 71 MB before the lift, 87 MB after
    # it without this, so the file would only ever have grown.
    connection.execute(queries.COMMIT_STORE_VACUUM)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(None, "2", _lift_1),
    Migration("2", "3", _lift_2),
    Migration("3", "4", _lift_3),
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

    @property
    def commit_hash(self) -> str:
        return self.id


class Span(NamedTuple):
    floor: int | None
    ceiling: int | None
    size: int


@dataclass(frozen=True)
class AuthorClause:
    """One author test, as alternatives any of which admits a commit.

    ``likes`` are SQL LIKE patterns, ``exacts`` exact addresses, ``addresses``
    lower-cased addresses matched after trimming, which is how `repo_authors`
    keys its personal addresses.
    """

    likes: tuple[str, ...] = ()
    exacts: tuple[str, ...] = ()
    addresses: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommitFilter:
    """What a search can narrow in SQL. Every field left empty narrows nothing.

    ``since``/``until`` are `YYYY-MM-DD` on the author's clock. ``summary_terms``
    and ``path_terms`` are lower-cased substrings, every one required. Each
    author clause is required, and inside one any alternative admits.
    """

    since: str | None = None
    until: str | None = None
    summary_terms: tuple[str, ...] = ()
    path_terms: tuple[str, ...] = ()
    authors: tuple[AuthorClause, ...] = ()


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
    """Query and write API over one branch's commit database."""

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

    def branch_name(self) -> str | None:
        row = self.connection.execute(queries.META_BRANCH_QUERY).fetchone()
        return str(row[0]) if row else None

    def claim_branch(self, branch: str) -> None:
        """Bind this file to one branch, rejecting a filename collision."""
        recorded = self.branch_name()
        if recorded is None:
            self.connection.execute(queries.META_BRANCH_INSERT, (branch,))
            self.connection.commit()
            return
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

    def span(self) -> Span:
        low, high, count = self.connection.execute(queries.COMMIT_SPAN_QUERY).fetchone()
        return Span(low, high, count)

    def floor(self) -> int | None:
        return self.span().floor

    def ceiling(self) -> int | None:
        return self.span().ceiling

    def count(self) -> int:
        return self.span().size

    def numbers_for(self, ids: Iterable[str]) -> dict[str, int]:
        """The numbers of the given commit hashes that are stored, and no others."""
        wanted = list(dict.fromkeys(ids))
        found: dict[str, int] = {}
        for start in range(0, len(wanted), IN_CHUNK):
            chunk = wanted[start:start + IN_CHUNK]
            sql = queries.COMMIT_NUMBERS_FOR_IDS_TEMPLATE.format(
                placeholders=",".join("?" for _ in chunk)
            )
            found.update({row[0]: row[1] for row in self.connection.execute(sql, chunk)})
        return found

    def tip(self) -> StoredCommit | None:
        found = self.recent(1)
        return found[0] if found else None

    def recent(self, limit: int) -> list[StoredCommit]:
        """The newest ``limit`` commits, newest first.

        This is the query `patch` runs, and it never materialises the branch:
        that is the difference between a bounded index scan and parsing a
        gigabyte of text to read forty rows off the end of it.
        """
        rows = self.connection.execute(queries.COMMIT_RECENT_QUERY, (limit,)).fetchall()
        return self._with_files(rows)

    def month(self, month: str) -> list[StoredCommit]:
        """The commits authored in ``month`` (`YYYY-MM`), oldest first, without files."""
        rows = self.connection.execute(queries.COMMIT_MONTH_QUERY, (month,)).fetchall()
        return [_commit(row) for row in rows]

    def search(
        self,
        criteria: CommitFilter,
        *,
        limit: int,
        below: int | None = None,
    ) -> list[StoredCommit]:
        """A page of commits matching ``criteria``, newest first, below ``below``."""
        conditions: list[str] = []
        params: list[Any] = [ABOVE_EVERY_NUMBER if below is None else below]
        if criteria.since:
            conditions.append(queries.COMMIT_SEARCH_SINCE)
            params.append(criteria.since)
        if criteria.until:
            conditions.append(queries.COMMIT_SEARCH_UNTIL)
            params.append(criteria.until)
        for term in criteria.summary_terms:
            conditions.append(queries.COMMIT_SEARCH_SUMMARY)
            params.append(term)
        for term in criteria.path_terms:
            conditions.append(queries.COMMIT_SEARCH_PATH)
            params.append(term)
        for clause in criteria.authors:
            alternatives, values = _author_alternatives(clause)
            conditions.append(f"({' OR '.join(alternatives)})")
            params.extend(values)
        params.append(limit)
        sql = queries.COMMIT_SEARCH_TEMPLATE.format(
            conditions="".join(f"\n  AND {condition}" for condition in conditions)
        )
        return self._with_files(self.connection.execute(sql, params).fetchall())

    def prior_status(self, path: str, number: int) -> str | None:
        """The status ``path`` had in its newest commit below ``number``, if any.

        A file row an early store wrote without a letter reads as a modify.
        """
        row = self.connection.execute(
            queries.COMMIT_FILE_PRIOR_STATUS_QUERY, (path, number)
        ).fetchone()
        return str(row[0]) if row else None

    def _with_files(self, rows: Sequence[tuple[Any, ...]]) -> list[StoredCommit]:
        if not rows:
            return []
        numbers = [row[0] for row in rows]
        files: dict[int, dict[str, str]] = {number: {} for number in numbers}
        statuses: dict[int, dict[str, str]] = {number: {} for number in numbers}
        deleted: dict[int, list[str]] = {number: [] for number in numbers}
        for start in range(0, len(numbers), IN_CHUNK):
            chunk = numbers[start:start + IN_CHUNK]
            sql = queries.COMMIT_FILES_FOR_NUMBERS_TEMPLATE.format(
                placeholders=",".join("?" for _ in chunk)
            )
            for number, path, hash_, status in self.connection.execute(sql, chunk):
                if status == "D":
                    deleted[number].append(path)
                else:
                    files[number][path] = hash_
                if status:
                    statuses[number][path] = status
        return [
            _commit(
                row,
                files    = files[row[0]],
                deleted  = sorted(deleted[row[0]]),
                statuses = statuses[row[0]],
            )
            for row in rows
        ]

    # -- allocation --------------------------------------------------------

    def allocate(
        self,
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
        existing = self.numbers_for(item.id for item in ordered)
        ceiling = self.ceiling()
        # The seed is read only while the branch is empty. Re-seeding a branch
        # that already has commits is the same mistake as re-deriving a number.
        next_number = (seed if seed is not None else 1) if ceiling is None else ceiling + 1
        return self._assign(ordered, existing, next_number)

    def backfill(self, records: Iterable[StoredCommit]) -> list[int]:
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
        floor = self.floor()
        if floor is None:
            return self.allocate(ordered)
        existing = self.numbers_for(item.id for item in ordered)
        wanted = [item for item in ordered if item.id not in existing]
        first = floor - len(wanted)
        if first < 1:
            raise ValueError(
                f"backfilling {len(wanted)} commit(s) below floor {floor} would run "
                f"below 1; the store cannot hold them without renumbering"
            )
        return self._assign(ordered, existing, first)

    def reset(self) -> None:
        """Forget every commit.

        The one legitimate caller is rewritten history: after a rebase or a
        force-push the stored numbers point at commits the branch no longer
        has, so they describe nothing. Any other use is a renumbering.
        """
        self.connection.execute(queries.COMMIT_DELETE_ALL)
        self.connection.commit()

    def _assign(
        self, ordered: list[StoredCommit], existing: dict[str, int], next_number: int
    ) -> list[int]:
        assigned: list[int] = []
        fresh: list[tuple[int, StoredCommit]] = []
        for item in ordered:
            if item.id in existing:
                assigned.append(existing[item.id])
                continue
            assigned.append(next_number)
            fresh.append((next_number, item))
            next_number += 1
        self._write(fresh)
        return assigned

    def _write(self, fresh: list[tuple[int, StoredCommit]]) -> None:
        if not fresh:
            return
        self.connection.executemany(
            queries.COMMIT_INSERT,
            [
                (number, item.id, item.summary, item.author, authored_stamp(item.date))
                for number, item in fresh
            ],
        )
        rows: list[tuple[Any, ...]] = []
        for number, item in fresh:
            for path, hash_ in item.files.items():
                # NULL, not a default letter: a caller that has no status does
                # not know whether a file was added or modified, and writing
                # "M" would be a guess indistinguishable from git's own answer.
                rows.append((number, path, hash_, item.statuses.get(path)))
            for path in item.deleted:
                rows.append((number, path, None, "D"))
        if rows:
            self.connection.executemany(queries.COMMIT_FILE_INSERT, rows)
        self.connection.commit()

    # -- verification ------------------------------------------------------

    def verify(self) -> list[str]:
        """Problems with the numbering, empty when it is sound."""
        return problems_in(self.span(), self.branch_name() or "store")


def problems_in(span: Span, label: str) -> list[str]:
    """What a span says is wrong with a store's numbering.

    Contiguity is checkable because allocation is additive: floor to ceiling
    with no gap is the only shape allocate/backfill can produce, so a gap
    means something outside this module wrote the store.
    """
    if span.size == 0 or span.floor is None or span.ceiling is None:
        return []
    expected = span.ceiling - span.floor + 1
    if expected == span.size:
        return []
    return [
        f"hole in {label}: numbers {span.floor} to {span.ceiling} span {expected} slots "
        f"but only {span.size} commits are stored"
    ]


def _commit(
    row: tuple[Any, ...],
    *,
    files: dict[str, str] | None = None,
    deleted: list[str] | None = None,
    statuses: dict[str, str] | None = None,
) -> StoredCommit:
    return StoredCommit(
        number   = row[0],
        id       = row[1],
        summary  = row[2] or "",
        author   = row[3] or "",
        date     = row[4] or "",
        files    = files or {},
        deleted  = deleted or [],
        statuses = statuses or {},
    )


def _author_alternatives(clause: AuthorClause) -> tuple[list[str], list[str]]:
    alternatives: list[str] = []
    values: list[str] = []
    for pattern in clause.likes:
        alternatives.append(queries.COMMIT_SEARCH_AUTHOR_LIKE)
        values.append(pattern)
    for address in clause.exacts:
        alternatives.append(queries.COMMIT_SEARCH_AUTHOR_EXACT)
        values.append(address)
    if clause.addresses:
        alternatives.append(
            queries.COMMIT_SEARCH_AUTHOR_ADDRESSES_TEMPLATE.format(
                placeholders=",".join("?" for _ in clause.addresses)
            )
        )
        values.extend(clause.addresses)
    if not alternatives:
        raise ValueError("an author clause needs at least one alternative")
    return alternatives, values
