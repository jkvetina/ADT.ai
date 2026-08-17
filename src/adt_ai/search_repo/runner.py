from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from adt_ai.rebuild.runner import REVEAL_DEFAULT_LIMIT
from adt_ai.shared.commit_cache import (
    DEFAULT_COMMITS_TEMPLATE,
    current_branch,
    open_store,
)
from adt_ai.shared.commit_discovery import commit_ref_matches
from adt_ai.shared.git_files import git_user_email, run_git, run_git_bytes

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
            # longer throws them away. The fallback below is only for rows
            # imported from a YAML cache that never held them: there, A and M
            # are genuinely indistinguishable, so the old approximation is the
            # honest answer rather than a letter invented to look precise.
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
        if request.my and git_user_email(request.root) != commit.author:
            return None
        if not _contains_all(commit.summary, request.summary_terms):
            return None
        if request.authors and not _contains_any(commit.author, request.authors):
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
        for record in records:
            for file_path in record.files:
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
                target.write_bytes(payload)
                restored.append(target)
                if request.stage:
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
    # `USAGE/patch.md` documented the syntax `patch` did not have. `N-M` arrives
    # here for free, which is the point of sharing rather than copying.
    return commit_ref_matches(commit.number, commit.id, ref)


def _matches_date(commit_date: str, request: SearchRepoRequest) -> bool:
    moment = datetime.fromisoformat(commit_date)
    value = moment.date()
    if request.since and value < date.fromisoformat(request.since):
        return False
    if request.until and value > date.fromisoformat(request.until):
        return False
    if request.recent is not None:
        if float(request.recent).is_integer():
            # A whole-day window keeps its date-level meaning: `-recent 1` holds
            # a commit made at 23:00 yesterday whatever time the search runs.
            if value < datetime.now().date() - timedelta(days=int(request.recent)):
                return False
        elif moment < datetime.now() - timedelta(days=request.recent):
            # Below a day both sides have to keep their time, or every commit
            # made earlier today survives an "in the past hour" window.
            return False
    return True


def _matches_file(path: str, request: SearchRepoRequest) -> bool:
    if not _contains_all(path, request.file_terms):
        return False
    object_type, object_name = _object_identity(path)
    if request.object_types and not object_type:
        return False
    if request.object_names and not object_name:
        return False
    if request.object_types and not _contains_any(object_type, request.object_types):
        return False
    return not (request.object_names and not _contains_any(object_name, request.object_names))


def _object_identity(path: str) -> tuple[str, str]:
    parts = Path(path).parts
    # The `database` segment leads in the legacy layout (database/<schema>/...)
    # and follows the schema in the default layout (<schema>/database/...). In
    # both, the object type is parts[2] and the file name is parts[-1].
    if len(parts) < 4 or "database" not in (parts[0].lower(), parts[1].lower()):
        return "", ""
    object_type = parts[2].replace("_", " ").replace("-", " ").upper()
    if object_type.endswith("IES"):
        object_type = f"{object_type[:-3]}Y"
    elif object_type.endswith("S"):
        object_type = object_type[:-1]
    suffix = Path(parts[-1]).suffix.lower()
    if object_type == "PACKAGE" and suffix == ".pkb":
        object_type = "PACKAGE BODY"
    object_name = Path(parts[-1]).stem.upper()
    return object_type, object_name


def _contains_all(value: str, terms: list[str] | None) -> bool:
    haystack = value.lower()
    return all(str(term).lower() in haystack for term in (terms or []))


def _contains_any(value: str, terms: list[str]) -> bool:
    haystack = value.lower()
    return any(str(term).lower() in haystack for term in terms)


def _versioned_restore_path(path: Path, number: int) -> Path:
    return path.with_name(f"{path.stem}.{number}{path.suffix}")
