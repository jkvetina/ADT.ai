from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from adt_ai.shared import text_files
from adt_ai.shared.git_files import ChangedFile, changed_files, run_git

FIELD_SEPARATOR = "\x1f"
PATCH_FOLDER_RE = re.compile(r"^(?P<day>\d{6})-(?P<sequence>\d+)-(?P<code>.+)$")


@dataclass(frozen=True)
class PatchRequest:
    root: Path
    commit_limit: int
    patch_code: str | None = None
    rebuild: bool = False
    search_terms: list[str] | None = None
    authors: list[str] | None = None
    commit_refs: list[str] | None = None
    ignore_commits: list[str] | None = None
    files_only: bool = False
    include_full_exports: bool = False


@dataclass(frozen=True)
class CommitRecord:
    number: int
    id: str
    summary: str
    author: str
    date: str
    files: dict[str, str]
    deleted: list[str]
    patch: str | None = None

    @property
    def commit_hash(self) -> str:
        return self.id

    @property
    def usable_files(self) -> dict[str, str]:
        return self.files

    @property
    def deleted_files(self) -> list[str]:
        return self.deleted

    @property
    def detected_patch(self) -> str | None:
        return self.patch

    @property
    def file_classes(self) -> dict[str, str]:
        return {
            path: file_class
            for path in self.files
            if (file_class := _classify_file(path, include_full_exports=True)) is not None
        }


@dataclass(frozen=True)
class PatchFolder:
    folder: str
    path: Path
    patch_code: str
    driving_sql: str | None
    commits: list[str]
    files: list[str]
    target_status: dict[str, str]


def discover_patch_folders(patch_root: Path, *, ref: str | None = None) -> list[PatchFolder]:
    if not patch_root.exists():
        return []
    folders = [
        parse_patch_folder(path)
        for path in sorted(patch_root.iterdir(), key=lambda item: item.name, reverse=True)
        if path.is_dir()
    ]
    if ref:
        needle = ref.upper()
        folders = [
            folder
            for folder in folders
            if needle in folder.folder.upper() or needle in folder.patch_code.upper()
        ]
    return folders


def parse_patch_folder(path: Path) -> PatchFolder:
    match = PATCH_FOLDER_RE.match(path.name)
    patch_code = match.group("code") if match else path.name
    sql_files = sorted(path.glob("*.sql"))
    files = sorted({*_read_lines(path / "files.txt"), *_patch_file_references(sql_files)})
    return PatchFolder(
        folder        = path.name,
        path          = path,
        patch_code    = patch_code,
        driving_sql   = sql_files[0].name if sql_files else None,
        commits       = _read_lines(path / "commits.txt"),
        files         = files,
        target_status = _target_status(path),
    )


class GitCommitCache:
    def build(self, request: PatchRequest) -> list[CommitRecord]:
        commit_lines = run_git(
            request.root,
            [
                "log",
                f"-n{request.commit_limit}",
                "--reverse",
                f"--format=%H{FIELD_SEPARATOR}%ae{FIELD_SEPARATOR}%aI{FIELD_SEPARATOR}%s",
            ],
        ).splitlines()
        # Number commits by ABSOLUTE position in history: the newest commit
        # carries the full HEAD commit count and older commits descend from it.
        # With a commit_limit the window holds only the newest N commits, so the
        # oldest in the window is (total - N + 1), not 1.
        offset = self._head_commit_count(request.root) - len(commit_lines)
        records: list[CommitRecord] = []
        for number, line in enumerate(commit_lines, start=offset + 1):
            commit_hash, author, date, summary = line.split(FIELD_SEPARATOR, 3)
            changed_files = self._changed_files(request.root, commit_hash)
            usable_files: dict[str, str] = {}
            for item in changed_files:
                file_class = _classify_file(
                    item.path, include_full_exports=request.include_full_exports
                )
                if item.content_hash is not None and file_class is not None:
                    usable_files[item.path] = item.content_hash
            records.append(
                CommitRecord(
                    number  = number,
                    id      = commit_hash,
                    summary = summary,
                    author  = author,
                    date    = date,
                    files   = usable_files,
                    deleted = [item.path for item in changed_files if item.status == "D"],
                    patch   = _detected_patch(changed_files),
                )
            )
        return _filter_records(records, request)

    def write(self, root: Path, records: list[CommitRecord]) -> Path:
        cache_path = root / ".adt-ai" / "patch_commits.yaml"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_text(
            cache_path,
            yaml.safe_dump(
                [
                    {
                        "number": record.number,
                        "id": record.id,
                        "summary": record.summary,
                        "author": record.author,
                        "date": record.date,
                        "files": record.files,
                        "deleted": record.deleted,
                        **({"patch": record.patch} if record.patch else {}),
                    }
                    for record in records
                ],
                sort_keys = False,
            ),
        )
        return cache_path

    @staticmethod
    def file_history(records: list[CommitRecord]) -> dict[str, dict[str, object]]:
        history: dict[str, dict[str, object]] = {}
        for record in records:
            for path in record.usable_files:
                entry = history.setdefault(
                    path,
                    {
                        "first_commit": record.number,
                        "last_commit": record.number,
                        "last_hash": record.commit_hash,
                        "newer_committed": False,
                    },
                )
                if record.number != entry["last_commit"]:
                    entry["last_commit"] = record.number
                    entry["last_hash"] = record.commit_hash
                    entry["newer_committed"] = True
        return history

    def _head_commit_count(self, root: Path) -> int:
        # Total commits reachable from HEAD, independent of the window limit —
        # the absolute number of the newest commit.
        return int(run_git(root, ["rev-list", "--count", "HEAD"]).strip() or "0")

    def _changed_files(self, root: Path, commit_hash: str) -> list[ChangedFile]:
        return changed_files(root, commit_hash)


def _detected_patch(changed_files: list[ChangedFile]) -> str | None:
    for changed_file in changed_files:
        parts = Path(changed_file.path).parts
        if len(parts) >= 2 and parts[0].lower() == "patch":
            return parts[1]
    return None


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _target_status(path: Path) -> dict[str, str]:
    status: dict[str, str] = {}
    for log_path in sorted(path.glob("deploy_*.log")):
        target = log_path.stem.removeprefix("deploy_")
        text = log_path.read_text(encoding="utf-8", errors="replace").upper()
        status[target] = "SUCCESS" if "SUCCESS" in text and "ERROR" not in text else "ERROR"
    return status


def _patch_file_references(sql_files: list[Path]) -> list[str]:
    references: list[str] = []
    for sql_file in sql_files:
        text = sql_file.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r'@\s*"?\.?/([^";\s]+)"?', text):
            references.append(match.group(1))
    return references


def _filter_records(records: list[CommitRecord], request: PatchRequest) -> list[CommitRecord]:
    filtered = records
    if request.ignore_commits:
        ignored = {value.lower() for value in request.ignore_commits}
        filtered = [
            record
            for record in filtered
            if not _matches_any_ref(record, ignored)
        ]
    if request.commit_refs:
        selected = {value.lower() for value in request.commit_refs}
        filtered = [
            record
            for record in filtered
            if _matches_any_ref(record, selected)
        ]
    if request.authors:
        authors = [value.lower() for value in request.authors]
        filtered = [
            record
            for record in filtered
            if any(author in record.author.lower() for author in authors)
        ]
    if request.search_terms:
        terms = [value.lower() for value in request.search_terms]
        filtered = [
            record
            for record in filtered
            if all(_record_contains(record, term) for term in terms)
        ]
    if request.files_only:
        filtered = [record for record in filtered if record.usable_files or record.deleted_files]
    return filtered


def _matches_any_ref(record: CommitRecord, refs: set[str]) -> bool:
    return any(
        ref == str(record.number)
        or record.commit_hash.lower().startswith(ref)
        for ref in refs
    )


def _record_contains(record: CommitRecord, term: str) -> bool:
    haystack = "\n".join(
        [
            record.summary,
            record.author,
            *record.usable_files.keys(),
            *record.deleted_files,
        ]
    ).lower()
    return term in haystack


def _classify_file(path: str, *, include_full_exports: bool) -> str | None:
    parts = Path(path).parts
    if not parts:
        return "file"
    # `database`/`apex` lead in the legacy layout and follow the schema in the
    # default layout (<schema>/database/..., <schema>/apex/...). Recognise both.
    if len(parts) >= 4 and parts[0].lower() == "database":
        return f"database:{parts[1]}:{parts[2]}"
    if len(parts) >= 4 and parts[1].lower() == "database":
        return f"database:{parts[0]}:{parts[2]}"
    apex_at = (
        0 if parts[0].lower() == "apex"
        else 1 if len(parts) >= 2 and parts[1].lower() == "apex"
        else None
    )
    if apex_at is not None and len(parts) >= apex_at + 3:
        app = parts[apex_at + 1]
        if len(parts) == apex_at + 3 and re.fullmatch(r"f\d+\.sql", parts[apex_at + 2].lower()):
            return f"apex:{app}:full" if include_full_exports else None
        return f"apex:{app}:component"
    if len(parts) >= 2 and parts[0].lower() == "patch":
        return f"patch:{parts[1]}"
    return "file"
