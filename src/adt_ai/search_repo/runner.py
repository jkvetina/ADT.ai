from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from adt_ai.patch.layout import database_object_name, database_object_type
from adt_ai.rebuild.runner import REVEAL_DEFAULT_LIMIT
from adt_ai.shared import text_files
from adt_ai.shared.commit_cache import (
    DEFAULT_COMMITS_TEMPLATE,
    current_branch,
    open_store,
)
from adt_ai.shared.commit_discovery import commit_ref_matches
from adt_ai.shared.dates import within_recent_window
from adt_ai.shared.git_files import git_status_porcelain, run_git, run_git_bytes
from adt_ai.shared.identity import resolve_commit_email
from adt_ai.shared.sql_like import matches_sql_like

FIELD_SEPARATOR = "\x1f"


class SearchRepoError(Exception):
    """Search failed for a reason worth showing the user verbatim."""


@dataclass(frozen=True)
class SearchRepoRequest:
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
    stage: bool = False
    # Where the stores live. `search_repo` used to hardcode the default, so a
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
class SearchRepoRecord:
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
class SearchRepoResult:
    records: list[SearchRepoRecord]
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


class SearchRepoRunner:
    def run(self, request: SearchRepoRequest) -> SearchRepoResult:
        root = request.root.resolve()
        records = [
            record
            for commit in self._commits(request, root)
            if (record := self._matching_record(request, root, commit)) is not None
        ]
        records.sort(key=lambda record: record.number, reverse=True)
        if request.commit_limit is not None:
            records = records[:request.commit_limit]
        restored_files: list[Path] = []
        failed_restores: list[str] = []
        if request.restore:
            restored_files, failed_restores = self._restore(request, records, root)
        return SearchRepoResult(
            records         = records,
            restored_files  = restored_files,
            failed_restores = failed_restores,
        )

    def _commits(self, request: SearchRepoRequest, root: Path) -> list[_Commit]:
        branch = request.branch or current_branch(root)
        with open_store(root, branch, request.cache_file_template) as store:
            records = store.records(branch)
        if not records:
            raise SearchRepoError(
                f"commit store not found or empty for branch '{branch}', run adtai rebuild first"
            )
        existing_paths: set[str] = set()
        commits: list[_Commit] = []
        for record in records:
            files = [path for path in record.files if path]
            deleted = [path for path in record.deleted if path]
            # Git's own letters, which the store carries because `rebuild` no
            # longer throws them away. A store written by an early SQLite build
            # may still have NULL statuses; there A and M are genuinely
            # indistinguishable, so the old approximation is the honest answer.
            file_statuses = {path: status for path, status in record.statuses.items() if path}
            for path in files:
                file_statuses.setdefault(path, "M" if path in existing_paths else "A")
            for path in deleted:
                file_statuses[path] = "D"
            commits.append(
                _Commit(
                    number        = record.number,
                    id            = record.id,
                    author        = record.author,
                    date          = record.date,
                    summary       = record.summary,
                    files         = files,
                    deleted       = deleted,
                    file_statuses = file_statuses,
                )
            )
            existing_paths.difference_update(deleted)
            existing_paths.update(files)
        return commits

    def _matching_record(
        self,
        request: SearchRepoRequest,
        root: Path,
        commit: _Commit,
    ) -> SearchRepoRecord | None:
        if not _matches_refs(commit, request.commit_refs, request.hash_refs):
            return None
        if not _matches_date(commit.date, request):
            return None
        # `resolve_commit_email` since ADT #469, so `-my` here means what it means
        # in every other command: `IDENTITY.yaml`'s `email` when the project
        # states one, `git config user.email` when it does not.
        if request.my and resolve_commit_email(root=request.root) != commit.author:
            return None
        if not _contains_all(commit.summary, request.summary_terms):
            return None
        if request.authors and not _matches_any_pattern(commit.author, request.authors):
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
        return SearchRepoRecord(
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
        request: SearchRepoRequest,
        records: list[SearchRepoRecord],
        root: Path,
    ) -> tuple[list[Path], list[str]]:
        restored: list[Path] = []
        failed: list[str] = []
        # `-stage` writes every matching version to the one working-tree path,
        # so without this the OLDEST version landed last and got staged, against
        # the newest-wins promise in docs/search_repo.md. Records arrive
        # newest-first, so the first writer of a path is the newest (ADT #659).
        # Without `-stage` each version has a path of its own and none collide.
        staged_paths: set[str] = set()
        for record in records:
            for file_path in record.files:
                if request.stage and file_path in staged_paths:
                    continue
                if request.stage and git_status_porcelain(root, file_path):
                    # `-stage` writes straight onto this working-tree path and
                    # `git add`s the result, so an uncommitted local edit here
                    # would be overwritten and staged with no trace. Refuse it,
                    # the same way a stale cache entry is refused below, rather
                    # than silently discard the edit (ADT #670).
                    failed.append(file_path)
                    staged_paths.add(file_path)
                    continue
                try:
                    payload = run_git_bytes(root, ["show", f"{record.id}:{file_path}"])
                except subprocess.CalledProcessError:
                    # A stale cache entry (rebased/rewritten history), record
                    # it so a partial restore never looks like a full one.
                    failed.append(file_path)
                    continue
                target = root / file_path
                if not request.stage:
                    target = _versioned_restore_path(target, record.number)
                target.parent.mkdir(parents=True, exist_ok=True)
                # Through the shared writer, so restoring a file that already
                # holds the requested bytes leaves its mtime alone (`#593`). It
                # is still a restore either way: what the row reports is that
                # the path now carries that commit's content.
                text_files.write_bytes(target, payload)
                restored.append(target)
                if request.stage:
                    staged_paths.add(file_path)
                    run_git(root, ["add", file_path])
        return restored, failed


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
    # ONE resolver with `patch` (ADT #309). `search_repo` understood `N+` and
    # `patch` understood neither spelling, so the same argument written the same
    # way meant different things depending on which command read it, and
    # `docs/patch.md` documented the syntax `patch` did not have. `N-M` arrives
    # here for free, which is the point of sharing rather than copying.
    return commit_ref_matches(commit.number, commit.id, ref)


def _matches_date(commit_date: str, request: SearchRepoRequest) -> bool:
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


def _matches_file(path: str, request: SearchRepoRequest) -> bool:
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
    literal character on `search_repo -type`: measured at the CLI, `-type
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


def _versioned_restore_path(path: Path, number: int) -> Path:
    return path.with_name(f"{path.stem}.{number}{path.suffix}")
