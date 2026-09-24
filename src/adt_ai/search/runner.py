from __future__ import annotations

import math
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from adt_ai.patch.layout import database_object_name, database_object_type
from adt_ai.rebuild.runner import REVEAL_DEFAULT_LIMIT
from adt_ai.shared import text_files
from adt_ai.shared.author_aliases import author_aliases, canonical_author
from adt_ai.shared.commit_cache import (
    DEFAULT_COMMITS_TEMPLATE,
    current_branch,
    open_store,
)
from adt_ai.shared.commit_discovery import commit_ref_matches
from adt_ai.shared.commit_store import AuthorClause, CommitFilter, CommitStore, StoredCommit
from adt_ai.shared.dates import within_recent_window
from adt_ai.shared.git_files import run_git_bytes, save_work_in_progress
from adt_ai.shared.identity import resolve_commit_email
from adt_ai.shared.sql_like import matches_sql_like

FIELD_SEPARATOR = "\x1f"

#: Commits read per page. A page is narrowed in SQL first, so a search that
#: matches early stops after one read and one that matches rarely walks pages
#: rather than loading the branch.
PAGE_SIZE = 200


class SearchError(Exception):
    """Search failed for a reason worth showing the user verbatim."""


@dataclass(frozen=True)
class SearchRequest:
    root: Path
    branch: str | None = None
    commit_limit: int | None = REVEAL_DEFAULT_LIMIT
    show_files: bool = False
    file_limit: int = 0
    summary_terms: list[str] | None = None
    file_terms: list[str] | None = None
    object_types: list[str] | None = None
    object_names: list[str] | None = None
    authors: list[str] | None = None
    commit_refs: list[str] | None = None
    hash_refs: list[str] | None = None
    since: str | None = None
    until: str | None = None
    recent: int | None = None
    my: bool = False
    restore: bool = False
    # Where the stores live. `search` used to hardcode the default, so a
    # project that had configured `repo_commits_file` could rebuild happily and
    # then be told its cache did not exist.
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE
    # `object_types`, which is what `-type`/`-name` mean. Carried on the request
    # rather than re-derived here because the caller has already loaded it, and
    # because the vocabulary being config is the whole of ADT #471: an empty
    # mapping resolves no type, which is the honest answer for a caller that
    # supplied none.
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchRecord:
    number: int
    id: str
    summary: str
    author: str
    date: str
    files: list[str]
    deleted: list[str] = field(default_factory=list)
    file_statuses: dict[str, str] = field(default_factory=dict)

    @property
    def commit_hash(self) -> str:
        return self.id


@dataclass(frozen=True)
class SearchResult:
    records: list[SearchRecord]
    restored_files: list[Path] = field(default_factory=list)
    failed_restores: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Commit:
    number: int
    id: str
    author: str
    date: str
    summary: str
    files: list[str]
    deleted: list[str]
    file_statuses: dict[str, str]


class SearchRunner:
    def run(self, request: SearchRequest) -> SearchResult:
        root = request.root.resolve()
        records: list[SearchRecord] = []
        for commit in self._commits(request, root):
            record = self._matching_record(request, root, commit)
            if record is None:
                continue
            records.append(record)
            if request.commit_limit is not None and len(records) >= request.commit_limit:
                break
        restored_files: list[Path] = []
        failed_restores: list[str] = []
        if request.restore:
            restored_files, failed_restores = self._restore(records, root)
        return SearchResult(
            records         = records,
            restored_files  = restored_files,
            failed_restores = failed_restores,
        )

    def _commits(self, request: SearchRequest, root: Path) -> Iterator[_Commit]:
        """The branch's commits newest first, narrowed in SQL, one page at a time.

        It used to load the whole branch and filter in Python, which on a
        10,000-commit repository was the whole cost of the command. The SQL
        filter only ever keeps too much, never too little: `_matching_record`
        still decides every commit, so a filter SQLite cannot state exactly
        (a regex, an object type, a non-ASCII term) is simply left to it.
        """
        branch = request.branch or current_branch(root)
        aliases = author_aliases(request.config)
        with open_store(root, branch, request.cache_file_template) as store:
            if store.ceiling() is None:
                raise SearchError(
                    f"NO COMMIT STORE FOR BRANCH '{branch}'\n\n"
                    "It is missing or empty, and git could not fill it."
                )
            criteria = _sql_filter(request, aliases)
            page = max(request.commit_limit or 0, PAGE_SIZE)
            below: int | None = None
            while True:
                stored = store.search(criteria, limit=page, below=below)
                for record in stored:
                    yield _as_commit(store, record, aliases)
                if len(stored) < page:
                    return
                below = stored[-1].number

    def _matching_record(
        self,
        request: SearchRequest,
        root: Path,
        commit: _Commit,
    ) -> SearchRecord | None:
        if not _matches_refs(commit, request.commit_refs, request.hash_refs):
            return None
        if not _matches_date(commit.date, request):
            return None
        # `resolve_commit_email` since ADT #469, so `-my` here means what it means
        # in every other command: `IDENTITY.yaml`'s `email` when the project
        # states one, `git config user.email` when it does not.
        # The stored author is already mapped through `repo_authors` (ADT #831),
        # so the identity and the `-by` patterns are mapped the same way.
        aliases = author_aliases(request.config) if request.my or request.authors else {}
        if request.my and (
            canonical_author(resolve_commit_email(root=request.root), aliases)
            != commit.author
        ):
            return None
        if not _contains_all(commit.summary, request.summary_terms):
            return None
        if request.authors and not _matches_any_pattern(
            commit.author, [canonical_author(p, aliases) for p in request.authors]
        ):
            return None

        files: list[str] = []
        deleted: list[str] = []
        file_statuses: dict[str, str] = {}
        for path in [*commit.files, *commit.deleted]:
            if path in file_statuses:
                continue
            if _matches_file(path, request):
                files.append(path)
                file_statuses[path] = commit.file_statuses.get(path, "M")
                if file_statuses[path] == "D":
                    deleted.append(path)
        if not files:
            return None
        return SearchRecord(
            number        = commit.number,
            id            = commit.id,
            summary       = commit.summary,
            author        = commit.author,
            date          = commit.date,
            files         = files,
            deleted       = deleted,
            file_statuses = file_statuses,
        )

    def _restore(
        self,
        records: list[SearchRecord],
        root: Path,
    ) -> tuple[list[Path], list[str]]:
        """Each path's newest matching version, written over the path itself (ADT #897).

        Jan: *"-restore & -stage has to merge, it has to be consistent with
        current diff -pull; the assumption is we have code committed in our
        active branch and we apply the result from diff/search on top so we can
        see the difference."* So what `-stage` did, without its `git add` and
        without the `<name>.<commit>.<ext>` copy plain `-restore` wrote beside
        the original, and over a `WIP` commit of whatever the checkout held
        uncommitted, so nothing is lost and `git diff` shows the restore alone.
        A save git refuses raises `WorkInProgressError` before anything is
        written, and the screen names it `GIT COMMIT FAILED`.
        """
        failed: list[str] = []
        # Every version is read before anything is saved or written, so a
        # restore that resolves nothing touches nothing. Records arrive
        # newest-first, so the first version of a path is the newest, and the
        # oldest can no longer land last on the one working-tree path (ADT #659).
        payloads: dict[str, bytes] = {}
        for record in records:
            for file_path in record.files:
                # A deletion holds no version to put back, so the newest match
                # that does wins; asking git for it read as a stale store (#923).
                if file_path in payloads or file_path in record.deleted:
                    continue
                try:
                    payloads[file_path] = run_git_bytes(
                        root, ["show", f"{record.id}:{file_path}"]
                    )
                except subprocess.CalledProcessError:
                    # A stale cache entry (rebased/rewritten history), record
                    # it so a partial restore never looks like a full one.
                    failed.append(file_path)
        # Saved only when a write is really coming: a restore whose every path
        # already holds its version changes nothing, and committing the tree
        # then would only fold a previous restore's diff into a `WIP` commit.
        writes = any(
            not text_files.bytes_match(root / file_path, payload)
            for file_path, payload in payloads.items()
        )
        if writes:
            save_work_in_progress(root)
        restored: list[Path] = []
        for file_path, payload in payloads.items():
            target = root / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            # Through the shared writer, so restoring a file that already
            # holds the requested bytes leaves its mtime alone (`#593`). It is
            # still a restore either way: what the row reports is that the path
            # now carries that commit's content. Every destination is
            # overwritten unconditionally (ADT #732): what stood there is in
            # the WIP commit, and `WARNING - COULD NOT RESTORE:` reports only what git
            # could not resolve.
            text_files.write_bytes(target, payload)
            restored.append(target)
        return restored, failed


def _as_commit(store: CommitStore, record: StoredCommit, aliases: Mapping[str, str]) -> _Commit:
    files = [path for path in record.files if path]
    deleted = [path for path in record.deleted if path]
    # Git's own letters, which the store carries because `rebuild` no longer
    # throws them away. A store written by an early SQLite build may still have
    # NULL statuses; there A and M are genuinely indistinguishable, so the old
    # approximation is the honest answer: M when the path's newest older row
    # left it in the tree, A otherwise.
    file_statuses = {path: status for path, status in record.statuses.items() if path}
    for path in files:
        if path not in file_statuses:
            prior = store.prior_status(path, record.number)
            file_statuses[path] = "M" if prior not in (None, "D") else "A"
    for path in deleted:
        file_statuses[path] = "D"
    return _Commit(
        number        = record.number,
        id            = record.id,
        author        = canonical_author(record.author, aliases),
        date          = record.date,
        summary       = record.summary,
        files         = files,
        deleted       = deleted,
        file_statuses = file_statuses,
    )


def _sql_filter(request: SearchRequest, aliases: Mapping[str, str]) -> CommitFilter:
    """The part of the request SQLite can narrow by without dropping a match.

    SQLite folds case for ASCII only, so a term or pattern carrying anything
    else stays with the Python check alone. `-recent` is widened by a day on
    each side of the author's clock and still decided exactly afterwards.
    """
    since = date.fromisoformat(request.since).isoformat() if request.since else None
    if request.recent:
        loose = date.today() - timedelta(days=math.ceil(request.recent) + 1)
        since = max(since or "", loose.isoformat())
    until = date.fromisoformat(request.until).isoformat() if request.until else None
    authors: list[AuthorClause] = []
    keys_ascii = all(key.isascii() for key in aliases)
    if request.authors and keys_ascii:
        patterns = [canonical_author(str(p), aliases) for p in request.authors]
        if all(pattern.isascii() for pattern in patterns):
            authors.append(AuthorClause(
                likes     = tuple(patterns),
                addresses = tuple(
                    key for key, company in aliases.items()
                    if _matches_any_pattern(company, patterns)
                ),
            ))
    if request.my and keys_ascii:
        identity = canonical_author(resolve_commit_email(root=request.root), aliases)
        if identity.isascii():
            authors.append(AuthorClause(
                exacts    = (identity,),
                addresses = tuple(key for key, company in aliases.items() if company == identity),
            ))
    return CommitFilter(
        since         = since,
        until         = until,
        summary_terms = _ascii_terms(request.summary_terms),
        path_terms    = _ascii_terms(request.file_terms),
        authors       = tuple(authors),
    )


def _ascii_terms(terms: list[str] | None) -> tuple[str, ...]:
    return tuple(str(term).lower() for term in terms or [] if str(term).isascii())


def _matches_refs(
    commit: _Commit,
    commit_refs: list[str] | None,
    hash_refs: list[str] | None,
) -> bool:
    if commit_refs and not any(_matches_ref(commit, ref) for ref in commit_refs):
        return False
    return not (
        hash_refs and not any(commit.id.lower().startswith(str(ref).lower()) for ref in hash_refs)
    )


def _matches_ref(commit: _Commit, ref: str) -> bool:
    # ONE resolver with `patch` (ADT #309). `search` understood `N+` and
    # `patch` understood neither spelling, so the same argument written the same
    # way meant different things depending on which command read it, and
    # `docs/patch.md` documented the syntax `patch` did not have. `N-M` arrives
    # here for free, which is the point of sharing rather than copying.
    return commit_ref_matches(commit.number, commit.id, ref)


def _matches_date(commit_date: str, request: SearchRequest) -> bool:
    moment = datetime.fromisoformat(commit_date)
    value = moment.date()
    if request.since and value < date.fromisoformat(request.since):
        return False
    if request.until and value > date.fromisoformat(request.until):
        return False
    # `within_recent_window` since ADT #467, and that is a behaviour change
    # rather than only a move. This compared against `today - N`, so `-recent 1`
    # covered today AND yesterday, N + 1 calendar days, while
    # `shared/dates.recent_since` renders every export header from "inclusive of
    # today", N days. Two readings of one flag, with `patch` about to become a
    # third. Below a day nothing moves: that is `#340`'s measurement and it is
    # pinned in the shared helper now.
    return within_recent_window(moment, request.recent)


def _matches_file(path: str, request: SearchRequest) -> bool:
    if not _contains_all(path, request.file_terms):
        return False
    object_type, object_name = _object_identity(path, request.config)
    if request.object_types and not object_type:
        return False
    if request.object_names and not object_name:
        return False
    if request.object_types and not _matches_any_pattern(object_type, request.object_types):
        return False
    return not (
        request.object_names and not _matches_any_pattern(object_name, request.object_names)
    )


def _object_identity(path: str, config: dict[str, Any]) -> tuple[str, str]:
    """The object type and name ``path`` holds, read from ``object_types``.

    This was a third file-to-object parser and it read no config at all (ADT
    #471): it took `parts[2]` as the type folder, the hardcoded layout ADT #196
    lifted out of `patch/layout.py`, de-pluralised that folder name with two
    string rules, and special-cased a `.pkb` extension the shipped config does
    not carry. Measured on the shipped config, `indexes/` answered `INDEXE`,
    `mviews/` answered `MVIEW` where the config says `MATERIALIZED VIEW`, and
    `job_schedules/` answered `JOB SCHEDULE` where it says `SCHEDULE`. Worse and
    quieter, both halves of each shared folder reported the SPEC's type, so
    `-type "PACKAGE BODY"` and `-type "TYPE BODY"` selected nothing at all.

    A project on an old-ADT tree keeps working by CONFIGURING `.pkb`, which is
    what `object_types` is for; nothing here knows any extension by name.
    """
    object_type = database_object_type(path, config)
    if object_type is None:
        return "", ""
    return object_type, database_object_name(path, config) or ""


def _contains_all(value: str, terms: list[str] | None) -> bool:
    """`-summary` and `-file`, whose documented meaning is AND-matched WORDS.

    Deliberately not `matches_sql_like`: these two flags search free text for
    words the way a reader would, and their help has always said so. The pattern
    filters below name an object or an author, which is the question every other
    command answers with SQL LIKE.
    """
    haystack = value.lower()
    return all(str(term).lower() in haystack for term in (terms or []))


def _matches_any_pattern(value: str, patterns: list[str]) -> bool:
    """`-type`, `-name` and `-by`, as SQL LIKE patterns (ADT #474).

    These three were substring tests while every other filter in the tool went
    through `shared/sql_like`, so `%` was a wildcard on `export_db -type` and a
    literal character on `search -type`: measured at the CLI, `-type
    "PACKAGE%"` and `-name "CORE%"` each returned nothing at all. Jan had already
    settled the question for `-search` on ADT #423, 2026-08-20: *"Same way as we
    are using SQL LIKE filters elsewhere, it should be reusable code!"*

    Anchored, and matching `export_db`'s own comparator exactly rather than
    wrapping each term in `%...%`. Two things follow, both intended. `-type TAB`
    no longer matches `TABLE`, and `-type PACKAGE` no longer returns the body
    files, which is the ADT #471 carry-over: that card fixed the reader and this
    comparator kept handing the spec filter its body back.
    """
    return any(matches_sql_like(value, pattern) for pattern in patterns)
