"""Scanning a branch into the store, each commit at its place on the branch.

A commit's number is its position on the branch's first-parent line, `git log
--first-parent --reverse <branch>` counted from 1 (ADT #895). Jan, 2026-09-19:
*"We care about continuity in main/master. [...] When you merge to main, you
merge as 1 new commit."* So a merge is one commit here, carrying everything the
merged branch changed, and the commits it brought in through its second parent
are not stored at all: they have no place on the line, and a number for them
would move every number above it. One `rev-list` per branch reads the line as
ids, and everything numbered here is looked up in it: the window a run reads,
the gap between that window and what the store already holds, and the store
itself, which `CommitStore.reconcile` follows when the line was cut back.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

# Aliased: `date` is a loop variable in the assembly loop below, and a bare
# `from datetime import date` shadows it (ruff F402).
from datetime import date as date_type
from datetime import timedelta
from pathlib import Path

from adt_ai.rebuild.models import RebuildError, RebuildReporter, RebuildRequest
from adt_ai.shared.commit_cache import (
    current_branch as history_current_branch,
)
from adt_ai.shared.commit_cache import (
    open_store,
    store_path,
)
from adt_ai.shared.commit_discovery import FIELD_SEPARATOR
from adt_ai.shared.commit_store import CommitStore, StoredCommit
from adt_ai.shared.git_files import changed_files, git_ref_exists, run_git

#: One scanned commit: hash, author, author date, subject.
CommitLine = tuple[str, str, str, str]


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
    label = "BRANCH" if len(missing) == 1 else "BRANCHES"
    # Two lines and a backticked command, because this reaches the reader through
    # the shared refusal screen (ADT #764) where the body is indented two columns
    # into an 80-column terminal. As one sentence it measured 99 and wrapped
    # itself, which is the ragged look that card exists to end. The first line
    # is a short uppercase headline (ADT #934).
    raise RebuildError(
        f"{label} {names} NOT FOUND IN THIS REPO\n\n"
        "Run `adtai rebuild -reveal` to list available branches."
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
) -> tuple[dict[str, int], dict[str, Path]]:
    stores: dict[str, CommitStore] = {}
    try:
        return _build_records_with_stores(request, branches, reporter, stores)
    finally:
        # A git/hash failure can happen after several branch stores have opened
        # but before the assembly loop reaches their old per-branch close.
        for store in reversed(list(stores.values())):
            with contextlib.suppress(Exception):
                store.close()


def _build_records_with_stores(
    request: RebuildRequest,
    branches: list[str],
    reporter: RebuildReporter,
    stores: dict[str, CommitStore],
) -> tuple[dict[str, int], dict[str, Path]]:
    # Phase 1, cheap counting pass: read commit metadata per branch (git log
    # only, no content hashing). Each branch keeps its own oldest-first commit
    # order; the unique set drives the progress total and dedupes hashing work.
    #
    # In --update mode each branch resumes from its stored tip: existing records
    # are reused as-is (never re-hashed) and only commits after the last stored
    # id are fetched. A branch with no usable store falls back to a full window.
    branch_lines: dict[str, list[CommitLine]] = {}
    store_paths: dict[str, Path] = {}
    positions: dict[str, dict[str, int]] = {}
    unique_order: list[str] = []
    seen_hashes: set[str] = set()
    resumed_any = False

    for branch in branches:
        store = stores.get(branch)
        if store is None:
            store = open_store(request.root, branch, request.cache_file_template)
            stores[branch] = store
        store_paths[branch] = store_path(request.root, request.cache_file_template, branch)
        history, merges = _first_parent_line(request.root, branch)
        positions[branch] = {commit: number for number, commit in enumerate(history, start=1)}
        # Before the resume point is read: a dropped commit cuts the line back,
        # and a store numbered any other way is moved once to the line here.
        store.reconcile(history, merges)
        since = _resume_point(request, store)
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
        # Every later read walks from the tip the walk above ended on, never
        # the branch name again, so a commit landing mid-run cannot reach a
        # scan without a position; the next run takes it.
        head = history[-1]
        lines = _commit_lines(
            request.root,
            head,
            commit_limit,
            since=since,
            since_date=since_date,
        )
        lines += _gap_lines(request.root, head, store, history, positions[branch], lines)
        branch_lines[branch] = lines
        for commit_hash, _author, _date, _summary in lines:
            if commit_hash not in seen_hashes:
                seen_hashes.add(commit_hash)
                unique_order.append(commit_hash)

    total = len(unique_order)
    # The walk read once per branch above also sizes the header below.
    branch_counts = {branch: len(numbered) for branch, numbered in positions.items()}
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

    # Each branch's scan goes to its store at the positions the walk gave it. A
    # commit the store already holds keeps its row, which `reconcile` has
    # already put at its position. The count is read off the key.
    record_counts: dict[str, int] = {}
    for branch, lines in branch_lines.items():
        store = stores[branch]
        stored = store.numbers_for(commit_hash for commit_hash, *_ in lines)
        store.place(
            (
                positions[branch][commit_hash],
                StoredCommit(
                    id       = commit_hash,
                    summary  = summary,
                    author   = author,
                    date     = date,
                    files    = file_data[commit_hash].files,
                    deleted  = file_data[commit_hash].deleted,
                    statuses = file_data[commit_hash].statuses,
                ),
            )
            for commit_hash, author, date, summary in lines
            if commit_hash not in stored
        )
        record_counts[branch] = store.count()

    return record_counts, store_paths


def _gap_lines(
    root: Path,
    head: str,
    store: CommitStore,
    history: list[str],
    positions: dict[str, int],
    lines: list[CommitLine],
) -> list[CommitLine]:
    """The commits between the store and the window, so the store keeps no hole.

    A window is the newest end of the line. When the store holds an older
    stretch that stops short of it, or a store moved to first-parent numbering
    forgot its merges so they are read again, the positions in between belong to
    commits neither the store nor the window has, and they are read here. The
    usual run, a window resuming at the stored tip, is answered by the count
    alone and reads nothing more.
    """
    span = store.span()
    bottoms = [positions[commit_hash] for commit_hash, *_ in lines]
    if span.floor is not None:
        bottoms.append(span.floor)
    if not bottoms:
        return []
    bottom = min(bottoms)
    scanned = {commit_hash for commit_hash, *_ in lines}
    held = span.size + len(scanned) - len(store.numbers_for(scanned))
    if held >= len(history) - bottom + 1:
        return []
    wanted = history[bottom - 1:]
    stored = store.numbers_for(wanted)
    missing = {commit for commit in wanted if commit not in stored and commit not in scanned}
    lowest = min(positions[commit] for commit in missing)
    return [
        line
        for line in _commit_lines(root, head, len(history) - lowest + 1)
        if line[0] in missing
    ]


@dataclass(frozen=True)
class _CommitFiles:
    files: dict[str, str]
    deleted: list[str]
    statuses: dict[str, str]

def _commit_files(request: RebuildRequest, commit_hash: str) -> _CommitFiles:
    changed = changed_files(request.root, commit_hash)
    return _CommitFiles(
        # The store holds git's answer unfiltered. `include_full_exports` is a
        # per-run reading policy (`patch -app`), not a property of history,
        # and a store that dropped `apex/<app>/f<id>.sql` at write time could
        # never serve the run that wanted it: nothing ever set the flag, so
        # the reading run lost those files silently. Store the data,
        # classify at read time, where the policy is actually known. The patch
        # folder a commit shipped is the same kind of reading since ADT #851: it
        # depends on the project's `patch_root`, so `patch` takes it off these
        # rows rather than off a column written here under a hardcoded `patch/`.
        files    ={i.path: i.content_hash for i in changed if i.content_hash is not None},
        deleted  = [i.path for i in changed if i.status == "D"],
        # Git's own status letter per file, which the YAML payload never carried.
        # `patch/summary.py` needs it to split NEW/DELETED/MODIFIED, and
        # `search` was guessing it from whether a path had been seen before.
        statuses = {i.path: i.status for i in changed},
    )

def _history_floor_date(bottom_days: int | None) -> str | None:
    """`patch_history_bottom_days` as the ISO date `git log --since` wants."""
    if not bottom_days or bottom_days <= 0:
        return None
    return (date_type.today() - timedelta(days=bottom_days)).isoformat()

def _resume_point(request: RebuildRequest, store: CommitStore) -> str | None:
    # The commit to resume after, or None to walk the window from its bottom.
    # Records already in the store are never re-hashed, so a resume is purely
    # about which commits git is asked for. `reconcile` has already dropped
    # anything the branch no longer has, so the stored tip is always in history.
    if not request.update_only:
        return None
    tip = store.tip()
    return tip.id if tip is not None else None

def _first_parent_line(root: Path, branch: str) -> tuple[list[str], set[str]]:
    """The branch's first-parent line, oldest first, and the merges on it.

    The same walk `_commit_lines` reads, so a commit's index here plus one is
    its number, whatever window a run reads. `--parents` names the merges in the
    same walk; only a store moving to this numbering needs them.
    """
    history: list[str] = []
    merges: set[str] = set()
    output = run_git(root, ["rev-list", "--first-parent", "--reverse", "--parents", branch])
    # Each line is the commit and then its parents, so a merge has three ids.
    for ids in (line.split() for line in output.split("\n")):
        if ids:
            history.append(ids[0])
            if len(ids) > 2:
                merges.add(ids[0])
    return history, merges

def _commit_lines(
    root: Path,
    branch: str,
    commit_limit: int | None,
    since: str | None = None,
    since_date: str | None = None,
) -> list[CommitLine]:
    # `--first-parent`: the line commits are numbered on. A merge is one entry
    # and the commits behind its second parent are never listed.
    args = ["log", "--first-parent", "--reverse"]
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
    result: list[CommitLine] = []
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
