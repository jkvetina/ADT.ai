from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import yaml

from adt_ai.git_files import changed_files
from adt_ai.patch.discovery import (
    FIELD_SEPARATOR,
    CommitRecord,
    _classify_file,
    _detected_patch,
)


class RebuildError(Exception):
    """Rebuild failed for a reason worth showing the user verbatim (no traceback)."""


class RebuildReporter(Protocol):
    def on_count(
        self,
        total_commits: int,
        branch_count: int,
        commit_limit: int | None = None,
        missing_commits: int | None = None,
    ) -> None: ...
    def on_commit_start(self, index: int, total: int) -> None: ...
    def on_commit(self, index: int, total: int) -> None: ...


class _NullReporter:
    def on_count(
        self,
        total_commits: int,
        branch_count: int,
        commit_limit: int | None = None,
        missing_commits: int | None = None,
    ) -> None:
        pass

    def on_commit_start(self, index: int, total: int) -> None:
        pass

    def on_commit(self, index: int, total: int) -> None:
        pass


@dataclass(frozen=True)
class RebuildRequest:
    root: Path
    commit_limit: int | None = None
    branches: list[str] | None = None
    include_full_exports: bool = False
    cache_file_template: str = "./config/commits/#BRANCH#.yaml"
    update_only: bool = False
    # Resolved ISO date (YYYY-MM-DD) bounding the window for `-since`. When set,
    # the per-branch window is "every commit since this date" instead of a fixed
    # commit count; runs a full bounded rebuild like commit_limit (never update).
    since_date: str | None = None


@dataclass(frozen=True)
class RebuildResult:
    cache_paths: dict[str, Path]
    branches: list[str]
    record_counts: dict[str, int]


class RebuildRunner:
    def run(self, request: RebuildRequest, reporter: RebuildReporter | None = None) -> RebuildResult:
        _reporter = reporter or _NullReporter()
        branches = _resolve_branches(request)
        _require_branches_exist(request.root, branches)
        branch_records = _build_records(request, branches, _reporter)
        cache_paths = _write_caches(
            request.root,
            branch_records,
            cache_file_template=request.cache_file_template,
        )
        return RebuildResult(
            cache_paths   = cache_paths,
            branches      = branches,
            record_counts = {branch: len(records) for branch, records in branch_records.items()},
        )


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
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "HEAD"


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
    # Display total is the FULL branch history (unlimited). With a commit_limit
    # the window holds only the newest N, so len(unique_order) == limit, not the
    # real branch size — recover the unlimited count for the header. In --update
    # mode that actually resumed from a cache, show both the full branch size and
    # the number of commits missing from the cache. When no branch had a usable
    # cache, update mode is really a full rebuild from scratch (the common first
    # run, now the default) — show the plain total, like a non-update full run,
    # instead of a confusing "N + N".
    if request.update_only and resumed_any:
        display_total = max(
            (_branch_commit_count(request.root, branch) for branch in branches),
            default=total,
        )
        missing_commits = total
    elif request.update_only or request.commit_limit is None:
        display_total = total
        missing_commits = None
    else:
        display_total = max(
            (_branch_commit_count(request.root, branch) for branch in branches),
            default=total,
        )
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
        offset = _branch_commit_count(request.root, branch) - len(lines)
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
        cache_path.write_text(
            yaml.safe_dump(payload, sort_keys=False),
            encoding="utf-8",
        )
        paths[branch] = cache_path
    return paths


def _cache_path(root: Path, cache_file_template: str, branch: str) -> Path:
    path = Path(cache_file_template.replace("#BRANCH#", branch)).expanduser()
    return path if path.is_absolute() else root / path


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
    path = _cache_path(root, cache_file_template, branch)
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    records: dict[int, CommitRecord] = {}
    for number, fields in data.items():
        records[int(number)] = CommitRecord(
            number  = int(number),
            id      = fields["id"],
            summary = fields.get("summary", ""),
            author  = fields.get("author", ""),
            date    = fields.get("date", ""),
            files   = fields.get("files") or {},
            deleted = fields.get("deleted") or [],
            patch   = fields.get("patch"),
        )
    return records


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
    return int(_git(root, ["rev-list", "--count", branch]).strip() or "0")


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
    for line in _git(root, args).splitlines():
        if not line.strip():
            continue
        parts = line.split(FIELD_SEPARATOR, 3)
        if len(parts) < 4:
            continue
        commit_hash, author, date, summary = parts
        result.append((commit_hash, author, date, summary))
    return result


def _git(root: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


# --- reveal: read-only branch inspector -------------------------------------
#
# `rebuild -reveal` lists the branches on the remote (`origin/*`) without
# touching any cache, newest commit first and capped to `limit` rows. It reads
# the remote refs — not local heads — so the list stays correct no matter which
# branch you have checked out, and it fetches first (best-effort, prune on) so
# branches deleted on the server drop off. A wildcard pattern narrows the list
# to matching names; `-my` keeps only branches whose tip-commit author email
# matches `git config user.email`.

REVEAL_DEFAULT_LIMIT = 20


@dataclass(frozen=True)
class BranchInfo:
    name: str
    updated: str
    author: str
    author_email: str
    committed: str = ""  # tip-commit committer date, ISO short (YYYY-MM-DD)


@dataclass(frozen=True)
class RevealResult:
    patterns: list[str] = field(default_factory=list)
    mine: bool = False
    since: str | None = None
    limit: int | None = REVEAL_DEFAULT_LIMIT
    branches: list[BranchInfo] = field(default_factory=list)
    total: int = 0


def reveal_branches(
    root: Path,
    *,
    patterns: list[str] | None = None,
    mine: bool = False,
    since: str | None = None,
    limit: int | None = REVEAL_DEFAULT_LIMIT,
    fetch: bool = True,
) -> RevealResult:
    if fetch:
        _fetch_origin(root)
    infos = _branch_infos(root)
    if mine:
        email = _current_user_email(root).lower()
        infos = [b for b in infos if email and b.author_email.lower() == email]
    if since:
        # `-since` in reveal mode is a date filter on the branch's tip commit:
        # keep branches whose latest committer date is on or after the cutoff.
        # Both sides are ISO `YYYY-MM-DD`, so a lexical compare orders correctly,
        # and a commit made on the cutoff day is kept (same inclusive boundary as
        # normal-mode `-since`).
        infos = [b for b in infos if b.committed and b.committed >= since]
    words = [w for w in (patterns or []) if w]
    if words:
        # AND across the words: a branch must contain every word. Each word is a
        # case-insensitive "contains" glob, so `feat 4995` keeps branches whose
        # name holds both `feat` and `4995`, and a single `feat*4995` still works.
        needles = [_contains_glob(w) for w in words]
        infos = [b for b in infos if all(fnmatch.fnmatch(b.name.lower(), n) for n in needles)]
    return RevealResult(
        patterns = words,
        mine     = mine,
        since    = since,
        limit    = limit,
        branches = infos[:limit],
        total    = len(infos),
    )


def _contains_glob(pattern: str) -> str:
    # Treat the user's wildcard as a case-insensitive "contains" match: a branch
    # like `feat/SASDSG-4995_...` should match `feat*4995` even though text
    # trails the digits. Anchor only where the user anchored — wrap each end with
    # `*` unless they already supplied one.
    needle = pattern.lower()
    if not needle.startswith("*"):
        needle = f"*{needle}"
    if not needle.endswith("*"):
        needle = f"{needle}*"
    return needle


def _branch_infos(root: Path) -> list[BranchInfo]:
    # Read the remote branches (`origin/*`), newest commit first. Remote-tracking
    # refs reflect what's actually on the server regardless of the checked-out
    # branch, so the list never goes stale the way local `refs/heads` would.
    fmt = "%(refname:short)\t%(committerdate:relative)\t%(committerdate:short)\t%(authoremail)\t%(authorname)"
    out = _git(root, ["for-each-ref", "refs/remotes/origin", f"--format={fmt}", "--sort=-committerdate"])
    infos: list[BranchInfo] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        name, updated, committed, email, author = parts[0], parts[1], parts[2], parts[3], parts[4]
        if name == "origin/HEAD":
            continue  # symbolic ref, not a real branch
        short = name[len("origin/"):] if name.startswith("origin/") else name
        infos.append(
            BranchInfo(
                name         = short,
                updated      = updated,
                author       = author,
                author_email = email.strip().lstrip("<").rstrip(">"),
                committed    = committed,
            )
        )
    return infos


def _fetch_origin(root: Path) -> None:
    # Best-effort refresh so the remote-tracking refs are current; `--prune`
    # drops branches deleted on the server. Network/offline failures are
    # non-fatal — fall back to whatever refs are already cached locally.
    subprocess.run(
        ["git", "fetch", "--quiet", "--prune", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
    )


def switch_to_branch(root: Path, name: str) -> None:
    # Check out `name` in the working tree at `root`. `git checkout` DWIMs a
    # local tracking branch from `origin/<name>` when no local branch exists, so
    # a name straight off the `-reveal` list (origin prefix already stripped)
    # works. A dirty tree that would be clobbered makes git refuse — surface that
    # stderr verbatim instead of swallowing it.
    result = subprocess.run(
        ["git", "checkout", name],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(message or f"git checkout {name} failed")


def _current_user_email(root: Path) -> str:
    result = subprocess.run(
        ["git", "config", "user.email"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _default_branch(root: Path) -> str:
    """The remote's default branch ref (e.g. `origin/master`), or "" if unknown.

    Prefers the `origin/HEAD` symbolic ref set by clone; falls back to probing
    the conventional `origin/main` / `origin/master` names. Used to exclude
    commits a feature branch merely inherited at creation from the base branch.
    """
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    ref = result.stdout.strip()
    if ref.startswith("refs/remotes/origin/"):
        return ref[len("refs/remotes/"):]
    for candidate in ("origin/main", "origin/master"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", candidate],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return candidate
    return ""


def branch_commits(
    root: Path,
    name: str,
    *,
    limit: int | None = None,
    mine: bool = False,
) -> list[tuple[str, str]]:
    """Recent commits made on `name`, newest first, as `(when, subject)` pairs.

    `when` is the committer date formatted `YYYY-MM-DD HH:MM` (matching the
    committer-date ordering `-reveal` uses). `limit` caps the count (None = all);
    `mine=True` keeps only commits authored by the configured git user
    (`user.email`), via `git log --author`.

    Only commits unique to the branch are returned — those it inherited from the
    default branch at creation are excluded by listing the `origin/<default>..name`
    range. When `name` IS the default branch (or no default can be resolved), all
    of its commits are listed.
    """
    args = ["log", "--format=%cd\t%s", "--date=format:%Y-%m-%d %H:%M"]
    if mine:
        email = _current_user_email(root)
        if email:
            args.append(f"--author={email}")
    if limit:
        args.append(f"--max-count={limit}")
    base = _default_branch(root)
    default_short = base[len("origin/"):] if base.startswith("origin/") else base
    if base and name != default_short:
        args.append(f"{base}..{name}")
    else:
        args.append(name)
    commits: list[tuple[str, str]] = []
    for line in _git(root, args).splitlines():
        if not line.strip():
            continue
        when, _, subject = line.partition("\t")
        commits.append((when, subject))
    return commits
