from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from adt_ai.rebuild.runner import REVEAL_DEFAULT_LIMIT
from adt_ai.shared.commit_cache import current_branch, load_history_cache
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
        restored_files = self._restore(request, records, root) if request.restore else []
        return SearchRepoResult(records=records, restored_files=restored_files)

    def _commits(self, request: SearchRepoRequest, root: Path) -> list[_Commit]:
        branch = request.branch or current_branch(root)
        records = load_history_cache(root, branch)
        if not records:
            raise SearchRepoError(
                f"commit cache not found or empty for branch '{branch}', run adtai rebuild first"
            )
        existing_paths: set[str] = set()
        commits: list[_Commit] = []
        for number, record in sorted(records.items()):
            file_statuses: dict[str, str] = {}
            files = [path for path in record.files if path]
            deleted = [path for path in record.deleted if path]
            for path in files:
                file_statuses[path] = "M" if path in existing_paths else "A"
            for path in deleted:
                file_statuses[path] = "D"
            commits.append(
                _Commit(
                    number        = number,
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
    ) -> list[Path]:
        restored: list[Path] = []
        for record in records:
            for file_path in record.files:
                try:
                    payload = run_git_bytes(root, ["show", f"{record.id}:{file_path}"])
                except subprocess.CalledProcessError:
                    continue
                target = root / file_path
                if not request.stage:
                    target = _versioned_restore_path(target, record.number)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                restored.append(target)
                if request.stage:
                    run_git(root, ["add", file_path])
        return restored


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
    value = str(ref).lower()
    if value.endswith("+") and value[:-1].isdigit():
        return commit.number >= int(value[:-1])
    return value == str(commit.number) or commit.id.lower().startswith(value)


def _matches_date(commit_date: str, request: SearchRepoRequest) -> bool:
    value = datetime.fromisoformat(commit_date).date()
    if request.since and value < date.fromisoformat(request.since):
        return False
    if request.until and value > date.fromisoformat(request.until):
        return False
    if request.recent is not None:
        cutoff = datetime.now().date() - timedelta(days=request.recent)
        if value < cutoff:
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
