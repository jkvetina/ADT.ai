from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from adt_ai.shared import text_files
from adt_ai.shared.deploy_status import latest_deploy_status, target_status
from adt_ai.shared.git_files import ChangedFile, changed_files, run_git

FIELD_SEPARATOR = "\x1f"
PATCH_FOLDER_RE = re.compile(r"^(?P<day>\d{6})-(?P<sequence>\d+)-(?P<code>.+)$")


@dataclass(frozen=True)
class PatchRequest:
    root: Path
    commit_limit: int
    patch_code: str | None = None
    # Hash mode picks its commits by which file HASHES moved, so the patch code
    # must not also narrow them by subject — old ADT replaced the filtered list
    # outright in that mode (`filtered_commits = self.hash_commits`,
    # patch.py:417).
    hash_mode: bool = False
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
    # Git's per-file status letter for this commit (`A`/`M`/`D`/...), kept so the
    # install script can split its file list into NEW / DELETED / MODIFIED the way
    # old ADT did (patch.py:1766-1780). Never serialized into
    # `patch_commits.yaml` -- that payload stays on old ADT's field set.
    statuses: dict[str, str] | None = None

    @property
    def commit_hash(self) -> str:
        return self.id

    @property
    def file_statuses(self) -> dict[str, str]:
        return self.statuses or {}

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
    # The single newest deploy log in the folder, as `<TARGET>:<OUTCOME>` --
    # `None` for a patch nobody has deployed (ADT #268). `target_status` above
    # answers a different question (has THIS target succeeded) and is what the
    # deploy skip-guard reads; this one is what the listing shows.
    latest_status: str | None = None


def patch_id(patch_code: str) -> str | None:
    """The ticket/card number a patch code carries, or ``None``.

    Jan, 2026-08-10: "ID should be number from the ticket/card. For ivory 65 it
    should be 65, for SASDSG-5566 it should be 5566."

    The number lives in the FIRST underscore-separated segment -- the ticket
    reference -- and everything after it is the human label. Scanning the whole
    code would read `66_LAYER0_FIX` as `660`, because the `0` of `LAYER0` is not
    part of any card id. Within that segment the LAST digit run wins, so a
    project-prefixed reference (`SASDSG-5566`, `IVORY67`) yields the number
    rather than a fragment of the prefix.
    """
    numbers = re.findall(r"\d+", patch_code.split("_", 1)[0])
    return numbers[-1] if numbers else None


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
        if needle.isdigit():
            # An all-digit ref is an ID lookup, compared against the parsed card
            # number EXACTLY -- the substring path would match `67` inside
            # `260809-1-66_LAYER67_FIX`, and inside the `yymmdd-seq-` prefix of
            # every folder created that day (ADT #268).
            folders = [
                folder
                for folder in folders
                if (found := patch_id(folder.patch_code)) is not None
                and int(found) == int(needle)
            ]
        else:
            folders = [
                folder
                for folder in folders
                if needle in folder.folder.upper() or needle in folder.patch_code.upper()
            ]
    return folders


def parse_patch_folder(path: Path) -> PatchFolder:
    """Read a patch folder back from the only artifact that gets deployed.

    Old ADT recovered both lists from the generated install script —
    ``get_file_references`` (patch.py:1028-1037) off the ``@"..."`` lines,
    ``get_file_commits`` (patch.py:1041-1056) off the ``-- COMMITS:`` block. The
    ``files.txt`` / ``commits.txt`` sidecars ADT.ai wrote instead were never an
    old-ADT artifact (Jan, 2026-08-09: "Looks like shit I never asked for"), and
    unioning ``files.txt`` with the link lines double-counted every file: the
    sidecar spelled it repo-relative, the link line under ``snapshots/`` (ADT
    #259). Old folders that still carry the sidecars keep parsing, so a patch
    built before this change stays deployable.
    """
    match = PATCH_FOLDER_RE.match(path.name)
    patch_code = match.group("code") if match else path.name
    sql_files = sorted(path.glob("*.sql"))
    # Both halves are needed and neither is a superset: a DELETED file is named
    # in the header but has no `@` line to link, and a file injected without a
    # commit behind it (a grant script) is linked but carries no change status.
    files = [*_patch_file_references(sql_files), *_patch_script_files(sql_files)]
    files = files or _read_lines(path / "files.txt")
    commits = _patch_script_commits(sql_files) or _read_lines(path / "commits.txt")
    return PatchFolder(
        folder        = path.name,
        path          = path,
        patch_code    = patch_code,
        driving_sql   = sql_files[0].name if sql_files else None,
        commits       = commits,
        files         = sorted(set(files)),
        target_status = target_status(path),
        latest_status = latest_deploy_status(path),
    )


class GitCommitCache:
    def build(self, request: PatchRequest) -> list[CommitRecord]:
        return _filter_records(self.scan(request), request)

    def scan(self, request: PatchRequest) -> list[CommitRecord]:
        """Every commit in the ``-commits N`` window, before any filter runs.

        ``build`` is this narrowed to what the patch selects. Both halves are
        needed: the selection is what gets patched, and the full window is what
        `#277` compares it against -- a newer commit dropped by the patch-code or
        author filter is exactly the one worth warning about, because nothing else
        in the run mentions it (old ADT scanned `self.all_files`, everything it
        had cached, patch.py:1636).
        """
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
                    statuses= {item.path: item.status for item in changed_files},
                )
            )
        return records

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



# `@"./snapshots/<path>";` — the repo-relative path is what every consumer wants,
# so the snapshot prefix the link line carries is stripped here, once.
_FILE_LINK_RE = re.compile(r'@\s*"?\.?/([^";\s]+)"?')

# `--   <number>) <summary>` inside the `-- COMMITS:` header block (old ADT
# patch.py:1764, read back at patch.py:1053).
_COMMIT_ROW_RE = re.compile(r"^--\s+(\d+\)\s.*\S)\s*$")

# `-- NEW FILES:` / `-- DELETED FILES:` / `-- MODIFIED FILES:` (old ADT
# patch.py:1768-1779).
_FILE_SECTION_RE = re.compile(r"^-- (NEW|DELETED|MODIFIED) FILES:\s*$")


def _patch_file_references(sql_files: list[Path]) -> list[str]:
    references: list[str] = []
    for sql_file in sql_files:
        text = sql_file.read_text(encoding="utf-8", errors="replace")
        for match in _FILE_LINK_RE.finditer(text):
            reference = match.group(1)
            references.append(_repo_relative_reference(reference))
    return references


def _repo_relative_reference(reference: str) -> str:
    """A link line's path as every consumer wants it: relative to the repo root.

    Two spellings reach here, and both are climbs out of the patch folder in
    different clothes. An exported object is snapshotted, so its link carries a
    `snapshots/` prefix. A template or per-patch script is linked where it already
    lives (ADT #288), so its link carries leading `../` segments — the patch folder
    always sits under the root, so stripping them yields the root-relative path by
    construction, whatever depth `patch_root` puts it at.
    """
    reference = reference.split("snapshots/", 1)[-1]
    parts = [part for part in reference.split("/")]
    while parts and parts[0] in ("..", "."):
        parts.pop(0)
    return "/".join(parts)


def _patch_script_files(sql_files: list[Path]) -> list[str]:
    """Paths listed under the header's NEW / DELETED / MODIFIED sections."""
    paths: list[str] = []
    for sql_file in sql_files:
        section = False
        for line in sql_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if _FILE_SECTION_RE.match(line):
                section = True
                continue
            if not section:
                continue
            if not line.startswith("--   "):
                section = False
                continue
            paths.append(line[5:].strip())
    return [path for path in paths if path]


def _patch_script_commits(sql_files: list[Path]) -> list[str]:
    commits: list[str] = []
    for sql_file in sql_files:
        extracting = False
        for line in sql_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("-- COMMITS:"):
                extracting = True
                continue
            if not extracting:
                continue
            if line.strip() == "--":
                break
            row = _COMMIT_ROW_RE.match(line)
            if row and row.group(1) not in commits:
                commits.append(row.group(1))
    return commits


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
    elif request.patch_code and not request.commit_refs and not request.hash_mode:
        # With no explicit `-search`, the patch code IS the search term — old ADT
        # patch.py:147, matched against the commit SUMMARY only (patch.py:1005),
        # never the file paths. Without this, `-patch 65` listed every recent
        # commit and left the operator to spot theirs (ADT #257).
        #
        # The one deliberate divergence: an explicit `-commit <n>` bypasses it.
        # Old ADT filtered those too, so a named commit whose subject missed the
        # code was dropped silently — and `-patch <name> -create -commit <n>` is
        # the documented IVORY build sequence, where that would build an empty
        # patch and still report success.
        needle = request.patch_code.lower()
        filtered = [record for record in filtered if needle in record.summary.lower()]
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
