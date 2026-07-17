from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from adt_ai.rebuild.models import RebuildError, RebuildReporter, RebuildRequest
from adt_ai.shared import text_files
from adt_ai.shared.commit_cache import (
    cache_path as history_cache_path,
)
from adt_ai.shared.commit_cache import (
    current_branch as history_current_branch,
)
from adt_ai.shared.commit_cache import (
    load_history_cache,
)
from adt_ai.shared.commit_discovery import (
    FIELD_SEPARATOR,
    CommitRecord,
    _classify_file,
    _detected_patch,
)
from adt_ai.shared.git_files import changed_files, run_git


def _resolve_branches(request: RebuildRequest) -> list[str]:
    if request.branches:
        return list(request.branches)
    return [_current_branch(request.root)]

def _require_branches_exist(root: Path, branches: list[str]) -> None:
    # Catch a typo'd `-branch` name up front: `git log <branch>` would otherwise
    # exit 128 and surface a raw "Command '[...]' returned non-zero exit status
    # 128" dump. Validate each resolvable commit-ish and name the offenders.
    missing = [b for b in branches if not _branch_exists(root, b)]
    if not missing:
        return
    names = ", ".join(f"'{b}'" for b in missing)
    label = "branch" if len(missing) == 1 else "branches"
    raise RebuildError(
        f"{label} {names} not found in this repo "
        "— run 'adtai rebuild -reveal' to list available branches"
    )

def _branch_exists(root: Path, branch: str) -> bool:
    # `--verify --quiet` resolves any commit-ish git log accepts (local branch,
    # origin/<name>, tag, SHA) and stays silent + returns non-zero when it can't.
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{branch}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0

def _current_branch(root: Path) -> str:
    return history_current_branch(root)

def _build_records(
    request: RebuildRequest,
    branches: list[str],
    reporter: RebuildReporter,
) -> dict[str, dict[int, CommitRecord]]:
    # Phase 1 — cheap counting pass: read commit metadata per branch (git log
    # only, no content hashing). Each branch keeps its own oldest-first commit
    # order; the unique set drives the progress total and dedupes hashing work.
    #
    # In --update mode each branch resumes from its cached tip: existing records
    # are reused as-is (never re-hashed) and only commits after the last cached
    # id are fetched. A branch with no usable cache falls back to a full window.
    branch_lines: dict[str, list[tuple[str, str, str, str]]] = {}
    existing_records: dict[str, dict[int, CommitRecord]] = {}
    unique_order: list[str] = []
    seen_hashes: set[str] = set()
    resumed_any = False

    for branch in branches:
        existing, since = _resume_point(request, branch)
        existing_records[branch] = existing
        if since is not None:
            resumed_any = True
        # --update ignores any commit_limit: the window is "everything new".
        commit_limit = None if request.update_only else request.commit_limit
        lines = _commit_lines(
            request.root,
            branch,
            commit_limit,
            since=since,
            since_date=request.since_date,
        )
        branch_lines[branch] = lines
        for commit_hash, _author, _date, _summary in lines:
            if commit_hash not in seen_hashes:
                seen_hashes.add(commit_hash)
                unique_order.append(commit_hash)

    total = len(unique_order)
    # One `git rev-list --count` per branch, shared by the header display below
    # and the absolute-numbering offsets in the assembly loop — computing it at
    # each use point doubled the subprocess cost per branch.
    branch_counts = {
        branch: _branch_commit_count(request.root, branch) for branch in branches
    }
    # Display total is the FULL branch history (unlimited). With a commit_limit
    # the window holds only the newest N, so len(unique_order) == limit, not the
    # real branch size — recover the unlimited count for the header. In --update
    # mode that actually resumed from a cache, show both the full branch size and
    # the number of commits missing from the cache. When no branch had a usable
    # cache, update mode is really a full rebuild from scratch (the common first
    # run, now the default) — show the plain total, like a non-update full run,
    # instead of a confusing "N + N".
    if request.update_only and resumed_any:
        display_total = max(branch_counts.values(), default=total)
        missing_commits = total
    elif request.update_only or request.commit_limit is None:
        display_total = total
        missing_commits = None
    else:
        display_total = max(branch_counts.values(), default=total)
        missing_commits = None
    header_limit = None if request.update_only else request.commit_limit
    reporter.on_count(display_total, len(branches), header_limit, missing_commits)

    # Phase 2 — expensive pass: hash the changed files once per unique commit,
    # reporting per-commit progress as we go. Shared commits are hashed once.
    file_data: dict[str, _CommitFiles] = {}
    for index, commit_hash in enumerate(unique_order, start=1):
        reporter.on_commit_start(index, total)
        file_data[commit_hash] = _commit_files(request, commit_hash)
        reporter.on_commit(index, total)
    if total == 0:
        # No commits to process (e.g. -update already current) — still close the
        # progress bar at an instant 100% so the module matches the export style.
        reporter.on_commit(0, 0)

    # Assemble one numbered record set per branch. Numbers are ABSOLUTE
    # positions in the branch history: the newest commit is numbered with the
    # branch's full commit count, descending to older commits. With a
    # commit_limit the window holds only the newest N commits, so the oldest in
    # the window is (total - N + 1), not 1. Without a limit the window is the
    # full history and the oldest commit is 1 (matches old ADT).
    branch_records: dict[str, dict[int, CommitRecord]] = {}
    for branch, lines in branch_lines.items():
        offset = branch_counts[branch] - len(lines)
        # In --update mode the previously cached records are kept verbatim and
        # the new commits append onto them with continuing absolute numbers.
        numbered: dict[int, CommitRecord] = dict(existing_records[branch])
        for number, (commit_hash, author, date, summary) in enumerate(lines, start=offset + 1):
            data = file_data[commit_hash]
            numbered[number] = CommitRecord(
                number  = number,
                id      = commit_hash,
                summary = summary,
                author  = author,
                date    = date,
                files   = data.files,
                deleted = data.deleted,
                patch   = data.patch,
            )
        branch_records[branch] = numbered

    return branch_records

@dataclass(frozen=True)
class _CommitFiles:
    files: dict[str, str]
    deleted: list[str]
    patch: str | None

def _commit_files(request: RebuildRequest, commit_hash: str) -> _CommitFiles:
    changed = changed_files(request.root, commit_hash)
    files: dict[str, str] = {}
    for item in changed:
        file_class = _classify_file(
            item.path,
            include_full_exports=request.include_full_exports,
        )
        if item.content_hash is not None and file_class is not None:
            files[item.path] = item.content_hash
    return _CommitFiles(
        files   = files,
        deleted = [i.path for i in changed if i.status == "D"],
        patch   = _detected_patch(changed),
    )

def _write_caches(
    root: Path,
    branch_records: dict[str, dict[int, CommitRecord]],
    cache_file_template: str = "./config/commits/#BRANCH#.yaml",
) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for branch, numbered in branch_records.items():
        cache_path = _cache_path(root, cache_file_template, branch)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            number: {
                "id":      record.id,
                "summary": record.summary,
                "author":  record.author,
                "date":    record.date,
                "files":   record.files,
                "deleted": record.deleted,
                **({"patch": record.patch} if record.patch else {}),
            }
            for number, record in sorted(numbered.items())
        }
        text = yaml.safe_dump(payload, sort_keys=False)
        # `-update` with nothing new reproduces the file byte-for-byte; skip the
        # write so the mtime doesn't churn (these caches live under Dropbox and
        # every touch re-syncs them).
        if not (cache_path.exists() and cache_path.read_text(encoding="utf-8") == text):
            text_files.write_text(cache_path, text)
        paths[branch] = cache_path
    return paths

def _cache_path(root: Path, cache_file_template: str, branch: str) -> Path:
    return history_cache_path(root, cache_file_template, branch)

def _resume_point(
    request: RebuildRequest, branch: str
) -> tuple[dict[int, CommitRecord], str | None]:
    # Returns (records to reuse verbatim, since-commit). For a normal rebuild,
    # or when the branch has no usable cache, returns ({}, None) so the branch
    # rebuilds from scratch. In --update mode it loads the existing cache and
    # resumes from the highest-numbered (newest) cached commit — unless that
    # commit no longer exists on the branch (rebase / force-push), in which case
    # the stale cache is discarded and the branch is rebuilt in full.
    if not request.update_only:
        return {}, None
    existing = _load_cache(request.root, request.cache_file_template, branch)
    if not existing:
        return {}, None
    last_id = existing[max(existing)].id
    if not _commit_in_history(request.root, branch, last_id):
        return {}, None
    return existing, last_id

def _load_cache(
    root: Path, cache_file_template: str, branch: str
) -> dict[int, CommitRecord]:
    return load_history_cache(root, branch, cache_file_template)

def _commit_in_history(root: Path, branch: str, commit: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, branch],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0

def _branch_commit_count(root: Path, branch: str) -> int:
    # Total commits reachable from the branch tip, independent of any window
    # limit — this is the absolute number of the newest commit.
    return int(run_git(root, ["rev-list", "--count", branch]).strip() or "0")

def _commit_lines(
    root: Path,
    branch: str,
    commit_limit: int | None,
    since: str | None = None,
    since_date: str | None = None,
) -> list[tuple[str, str, str, str]]:
    args = ["log", "--reverse"]
    if commit_limit is not None:
        args.append(f"-n{commit_limit}")
    # `-since`: bound the window by committer date. A bare date is midnight
    # local, so commits made on that day are kept ("first commit on this date").
    if since_date is not None:
        args.append(f"--since={since_date} 00:00:00")
    args.append(
        f"--format=%H{FIELD_SEPARATOR}%ae{FIELD_SEPARATOR}%aI{FIELD_SEPARATOR}%s"
    )
    # With a resume point, only fetch commits after the cached tip (exclusive).
    args.append(f"{since}..{branch}" if since else branch)
    result: list[tuple[str, str, str, str]] = []
    for line in run_git(root, args).splitlines():
        if not line.strip():
            continue
        parts = line.split(FIELD_SEPARATOR, 3)
        if len(parts) < 4:
            continue
        commit_hash, author, date, summary = parts
        result.append((commit_hash, author, date, summary))
    return result
