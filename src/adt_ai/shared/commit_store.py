"""The per-branch commit store, and the numbering rule that is its whole point.

**A commit's number is its position on the branch's first-parent line** (ADT
#895): the commit `git log --first-parent --reverse <branch>` lists first is 1.
Jan, 2026-09-19: *"We care about continuity in main/master. What happen in
other branches, nobody cares. When you merge to main, you merge as 1 new
commit."* So a merge takes the next number and carries everything it brought,
and the commits behind its second parent are not stored: they have no place on
the line, and giving them one would push every later number up. Three things
follow from a number being a fact about the line rather than about which runs
built the store:

* a merge moves no number already handed out, however old the merged branch;
* a store deleted and rebuilt, or rebuilt by `-force`, reads the same numbers
  back, and a `-limit` or `-since` window keeps them, so its first commit is
  not 1 and the range below it is simply not stored yet;
* a dropped unpushed commit frees its number, so the next commit takes it and
  the store keeps no hole.

`reconcile` is how the store follows the line when it was cut back. A commit id
fixes its whole first-parent ancestry, so the ceiling alone says whether the
store still sits on the line: one key probe. When it does not, or when the file
never recorded that it was numbered this way (a store written by the allocator
before #895), `renumber` moves every row in SQL, file rows included, and forgets
a commit the line no longer has.

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
from collections.abc import Collection, Iterable, Sequence
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
    never carried it, so `search` guessed ("M" when the path appeared in an
    older commit, else "A") and the patch install script could not split
    NEW/DELETED/MODIFIED from anything but that guess. ``deleted`` stays as its
    own list for the old-ADT payload shape, and is derivable from ``statuses``.
    """

    #: 0 on a record about to be written: `place` takes the number beside it,
    #: read off the branch's first-parent line, and a read fills it in.
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

    # -- writes ------------------------------------------------------------

    def place(self, numbered: Iterable[tuple[int, StoredCommit]]) -> None:
        """Write each commit at the number it is given, its place on the line.

        The caller has already read the positions off the branch and left out
        the commits the store holds, so this is a write and nothing else.
        """
        self._write(list(numbered))

    # -- numbering by first-parent position --------------------------------

    def reconcile(self, history: Sequence[str], merges: Collection[str] = ()) -> bool:
        """Make every stored number its commit's position on ``history``.

        ``history`` is the branch's first-parent line, oldest first, the whole
        of it, and ``merges`` the merge commits on it. A store this method
        already numbered whose ceiling still sits at its position is trusted as
        it stands: that commit's id fixes every commit below it, so one row is
        read and nothing is written. Returns whether it renumbered.

        A store numbered any other way is moved once, and its merges are
        forgotten so the run reads them again: before first-parent numbering a
        merge was stored with no files, its changes kept on the second-parent
        commits this move drops.
        """
        span = self.span()
        numbered = self._first_parent()
        if span.ceiling is None:
            if not numbered:
                self._mark_first_parent()
            return False
        if numbered and self._ceiling_agrees(span.ceiling, history):
            return False
        self.renumber(history, forget=() if numbered else merges)
        if not numbered:
            self._mark_first_parent()
        return True

    def renumber(self, history: Sequence[str], forget: Collection[str] = ()) -> None:
        """Move every stored commit to its position on ``history``, one transaction.

        A commit ``history`` does not hold, or ``forget`` names, is dropped with
        its file rows, which is what a dropped unpushed commit, a rewritten
        branch, or a merged branch's own commits leave behind.
        """
        connection = self.connection
        connection.execute(queries.COMMIT_POSITIONS_DDL)
        try:
            connection.execute(queries.COMMIT_POSITIONS_CLEAR)
            connection.executemany(
                queries.COMMIT_POSITIONS_INSERT,
                (
                    (commit, number)
                    for number, commit in enumerate(history, start=1)
                    if commit not in forget
                ),
            )
            connection.execute(queries.DEFER_FOREIGN_KEYS)
            for statement in queries.COMMIT_RENUMBER_STEPS:
                connection.execute(statement)
            connection.execute(queries.COMMIT_POSITIONS_CLEAR)
            connection.commit()
        except BaseException:
            # sqlite3 leaves the failed transaction open, and the next commit
            # on this connection would write the half-moved rows.
            connection.rollback()
            raise

    def _first_parent(self) -> bool:
        row = self.connection.execute(queries.META_NUMBERING_QUERY).fetchone()
        return row is not None and row[0] == queries.NUMBERING_FIRST_PARENT

    def _mark_first_parent(self) -> None:
        self.connection.execute(queries.META_NUMBERING_UPSERT)
        self.connection.commit()

    def _ceiling_agrees(self, ceiling: int, history: Sequence[str]) -> bool:
        if ceiling > len(history):
            return False
        (found,) = self.connection.execute(
            queries.COMMIT_ID_AT_NUMBER_QUERY, (ceiling,)
        ).fetchone()
        return bool(found == history[ceiling - 1])

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

    Contiguity is checkable because `rebuild` fills every position on the
    first-parent line from the store's floor to the branch's tip, the gap
    between a window and what the store already held included, so a gap means
    something outside ADT.ai wrote the store.
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
