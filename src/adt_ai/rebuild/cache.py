"""Scanning a branch into the store, and letting the store do the numbering.

The line this file used to carry, `offset = branch_counts[branch] - len(lines)`,
was the defect: it DERIVED a commit's number from its position in `git log`, so
anything that moved a position moved a number that had already been handed out.
A merge of an older-dated branch does exactly that. Numbering now happens in
`CommitStore`, once per commit, and this module's job is only to say which
commits it found and in what order.
"""

from __future__ import annotations

from dataclasses import dataclass

# Aliased: `date` is a loop variable in the assembly loop below, and a bare
# `from datetime import date` shadows it (ruff F402).
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path

from adt_ai.rebuild.models import RebuildError, RebuildReporter, RebuildRequest
from adt_ai.shared.commit_cache import (
    cache_path as history_cache_path,
)
from adt_ai.shared.commit_cache import (
    current_branch as history_current_branch,
)
from adt_ai.shared.commit_cache import (
    open_store,
    store_path,
)
from adt_ai.shared.commit_discovery import (
    FIELD_SEPARATOR,
    CommitRecord,
    _detected_patch,
)
from adt_ai.shared.commit_store import CommitStore, StoredCommit
from adt_ai.shared.commit_window import position_of
from adt_ai.shared.git_files import changed_files, git_is_ancestor, git_ref_exists, run_git


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
        f"{label} {names} not found in this repo, "
        "run 'adtai rebuild -reveal' to list available branches"
    )

def _branch_exists(root: Path, branch: str) -> bool:
    # `^{commit}` resolves any commit-ish git log accepts (local branch,
    # origin/<name>, tag, SHA); the shared adapter owns the subprocess.
    return git_ref_exists(root, f"{branch}^{{commit}}")

def _current_branch(root: Path) -> str:
    return history_current_branch(root)

def _build_records(
    request: RebuildRequest,
    branches: list[str],
    reporter: RebuildReporter,
) -> tuple[dict[str, dict[int, CommitRecord]], dict[str, Path]]:
    # Phase 1, cheap counting pass: read commit metadata per branch (git log
    # only, no content hashing). Each branch keeps its own oldest-first commit
    # order; the unique set drives the progress total and dedupes hashing work.
    #
    # In --update mode each branch resumes from its stored tip: existing records
    # are reused as-is (never re-hashed) and only commits after the last stored
    # id are fetched. A branch with no usable store falls back to a full window.
    branch_lines: dict[str, list[tuple[str, str, str, str]]] = {}
    stores: dict[str, CommitStore] = {}
    store_paths: dict[str, Path] = {}
    seeds: dict[str, int] = {}
    unique_order: list[str] = []
    seen_hashes: set[str] = set()
    resumed_any = False

    for branch in branches:
        store = open_store(request.root, branch, request.cache_file_template)
        stores[branch] = store
        store_paths[branch] = store_path(request.root, request.cache_file_template, branch)
        since = _resume_point(request, store, branch)
        if since is not None:
            resumed_any = True
        # --update ignores any commit_limit: the window is "everything new".
        commit_limit = None if request.update_only else request.commit_limit
        # `patch_history_bottom_days` applies only where there is nothing to
        # resume from, which is exactly "building this branch from scratch". A
        # run that resumed already has its floor: the cache below the tip, and
        # re-flooring it would drop commits the store already numbered. An
        # explicit -limit or -since outranks the project default, because a
        # window the operator typed is the more specific instruction.
        since_date = request.since_date
        if since is None and commit_limit is None and since_date is None:
            since_date = _history_floor_date(request.history_bottom_days)
        lines = _commit_lines(
            request.root,
            branch,
            commit_limit,
            since=since,
            since_date=since_date,
        )
        branch_lines[branch] = lines
        # The seed is read once, while the branch is empty, and it is what makes
        # a bounded first build safe: numbering the newest N from 1 would leave
        # no room underneath, so widening the window later could only renumber.
        # Starting the oldest commit IN THE WINDOW at its true position in
        # history reserves everything below it for a backfill.
        seeds[branch] = (
            position_of(request.root, lines[0][0])
            if lines and not store.numbers(branch)
            else 1
        )
        for commit_hash, _author, _date, _summary in lines:
            if commit_hash not in seen_hashes:
                seen_hashes.add(commit_hash)
                unique_order.append(commit_hash)

    total = len(unique_order)
    # One `git rev-list --count` per branch, shared by the header display below
    # and the absolute-numbering offsets in the assembly loop, computing it at
    # each use point doubled the subprocess cost per branch.
    branch_counts = {
        branch: _branch_commit_count(request.root, branch) for branch in branches
    }
    # Display total is the FULL branch history (unlimited). With a commit_limit
    # the window holds only the newest N, so len(unique_order) == limit, not the
    # real branch size, recover the unlimited count for the header. In --update
    # mode that actually resumed from a cache, show both the full branch size and
    # the number of commits missing from the cache. When no branch had a usable
    # cache, update mode is really a full rebuild from scratch (the common first
    # run, now the default), show the plain total, like a non-update full run,
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

    # Phase 2, expensive pass: hash the changed files once per unique commit,
    # reporting per-commit progress as we go. Shared commits are hashed once.
    file_data: dict[str, _CommitFiles] = {}
    for index, commit_hash in enumerate(unique_order, start=1):
        reporter.on_commit_start(index, total)
        file_data[commit_hash] = _commit_files(request, commit_hash)
        reporter.on_commit(index, total)
    if total == 0:
        # No commits to process (e.g. -update already current), still close the
        # progress bar at an instant 100% so the module matches the export style.
        reporter.on_commit(0, 0)

    # Hand each branch's scan to its store and read the numbered result back.
    # Nothing here computes a number: `allocate` mints above the tip, `backfill`
    # mints below the floor, and a commit that already has one keeps it.
    branch_records: dict[str, dict[int, CommitRecord]] = {}
    for branch, lines in branch_lines.items():
        store = stores[branch]
        _allocate(
            store,
            branch,
            [
                StoredCommit(
                    id       = commit_hash,
                    summary  = summary,
                    author   = author,
                    date     = date,
                    files    = file_data[commit_hash].files,
                    deleted  = file_data[commit_hash].deleted,
                    statuses = file_data[commit_hash].statuses,
                    patch    = file_data[commit_hash].patch,
                )
                for commit_hash, author, date, summary in lines
            ],
            seeds[branch],
        )
        branch_records[branch] = {
            stored.number: _as_commit_record(stored) for stored in store.records(branch)
        }
        store.close()

    return branch_records, store_paths


def _allocate(
    store: CommitStore, branch: str, records: list[StoredCommit], seed: int
) -> None:
    """Give every scanned commit a number, each one exactly once.

    The walk is oldest first, so an unknown commit that sits BEFORE the first
    one the store recognises is older history a widened window pulled in, and it
    needs a number below the floor. Everything else unknown is new to the branch
    and goes above the tip, merged-in commits included: they interleave by date
    in `git log`, and taking a number in the middle is precisely the renumbering
    this store exists to prevent.
    """
    if not records:
        return
    existing = store.numbers(branch)
    if not existing:
        store.allocate(branch, records, seed=seed)
        return
    first_known = next(
        (index for index, record in enumerate(records) if record.id in existing), None
    )
    if first_known is None:
        # The window overlaps nothing stored, so there is no floor to sit under.
        # These are newer commits (a bounded scan that outran the stored tip),
        # and appending is the only reading that cannot renumber.
        store.allocate(branch, records)
        return
    if first_known:
        store.backfill(branch, records[:first_known])
    store.allocate(branch, records[first_known:])


def _as_commit_record(stored: StoredCommit) -> CommitRecord:
    return CommitRecord(
        number   = stored.number,
        id       = stored.id,
        summary  = stored.summary,
        author   = stored.author,
        date     = stored.date,
        files    = stored.files,
        deleted  = stored.deleted,
        patch    = stored.patch,
        statuses = stored.statuses,
    )


@dataclass(frozen=True)
class _CommitFiles:
    files: dict[str, str]
    deleted: list[str]
    statuses: dict[str, str]
    patch: str | None

def _commit_files(request: RebuildRequest, commit_hash: str) -> _CommitFiles:
    changed = changed_files(request.root, commit_hash)
    return _CommitFiles(
        # The store holds git's answer unfiltered. `include_full_exports` is a
        # per-run reading policy (`patch -fullapp`), not a property of history,
        # and a store that dropped `apex/<app>/f<id>.sql` at write time could
        # never serve the run that wanted it: nothing ever set the flag, so
        # `-fullapp` reading the cache lost those files silently. Store the data,
        # classify at read time, where the policy is actually known.
        files    = {i.path: i.content_hash for i in changed if i.content_hash is not None},
        deleted  = [i.path for i in changed if i.status == "D"],
        # Git's own status letter per file, which the YAML payload never carried.
        # `patch/summary.py` needs it to split NEW/DELETED/MODIFIED, and
        # `search_repo` was guessing it from whether a path had been seen before.
        statuses = {i.path: i.status for i in changed},
        patch    = _detected_patch(changed),
    )

def _cache_path(root: Path, cache_file_template: str, branch: str) -> Path:
    return history_cache_path(root, cache_file_template, branch)


def _history_floor_date(bottom_days: int | None) -> str | None:
    """`patch_history_bottom_days` as the ISO date `git log --since` wants."""
    if not bottom_days or bottom_days <= 0:
        return None
    return (date_type.today() - timedelta(days=bottom_days)).isoformat()

def _resume_point(request: RebuildRequest, store: CommitStore, branch: str) -> str | None:
    # The commit to resume after, or None to walk the window from its bottom.
    # Records already in the store are never re-hashed and never re-numbered, so
    # a resume is purely about which commits git is asked for.
    #
    # Rewritten history is the one case that drops rows: when the stored tip is
    # no longer an ancestor of the branch, the numbers describe commits that do
    # not exist, so the branch is reset and rebuilt. That is not a renumbering,
    # it is the only honest answer to a force-push.
    if not request.update_only:
        return None
    tip = store.tip(branch)
    if tip is None:
        return None
    if not _commit_in_history(request.root, branch, tip.id):
        store.reset(branch)
        return None
    return tip.id

def _commit_in_history(root: Path, branch: str, commit: str) -> bool:
    return git_is_ancestor(root, commit, branch)

def _branch_commit_count(root: Path, branch: str) -> int:
    # Total commits reachable from the branch tip, independent of any window
    # limit, this is the absolute number of the newest commit.
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
    # Split on "\n" only: `str.splitlines()` also breaks on `\r`/`\x0c`/U+2028,
    # so a commit subject with an embedded control char would be truncated.
    for line in run_git(root, args).split("\n"):
        if not line.strip():
            continue
        parts = line.split(FIELD_SEPARATOR, 3)
        if len(parts) < 4:
            continue
        commit_hash, author, date, summary = parts
        result.append((commit_hash, author, date, summary))
    return result
