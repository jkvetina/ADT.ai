from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from adt_ai.shared.git_files import (
    default_branch_ref,
    fetch_origin,
    git_checkout,
    run_git,
)
from adt_ai.shared.identity import resolve_commit_email

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
        fetch_origin(root)
    infos = _branch_infos(root)
    if mine:
        # One resolver for every `-my` in the tool (ADT #469).
        email = resolve_commit_email(root=root).lower()
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
    # like `feat/PROJ-4995_...` should match `feat*4995` even though text
    # trails the digits. Anchor only where the user anchored, wrap each end with
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
    fmt = (
        "%(refname:short)\t%(committerdate:relative)\t%(committerdate:short)\t"
        "%(authoremail)\t%(authorname)"
    )
    out = run_git(
        root, ["for-each-ref", "refs/remotes/origin", f"--format={fmt}", "--sort=-committerdate"]
    )
    infos: list[BranchInfo] = []
    # "\n" only, `str.splitlines()` would truncate an author name holding an
    # embedded `\r` and discard the tail as a phantom row.
    for line in out.split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        name, updated, committed, email, author = parts[0], parts[1], parts[2], parts[3], parts[4]
        if name in {"origin", "origin/HEAD"}:
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

def switch_to_branch(root: Path, name: str) -> None:
    # Check out `name` in the working tree at `root`. `git checkout` DWIMs a
    # local tracking branch from `origin/<name>` when no local branch exists, so
    # a name straight off the `-reveal` list (origin prefix already stripped)
    # works. A dirty tree that would be clobbered makes git refuse, the shared
    # adapter surfaces that stderr verbatim instead of swallowing it.
    git_checkout(root, name)

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

    Only commits unique to the branch are returned, those it inherited from the
    default branch at creation are excluded by listing the `origin/<default>..name`
    range. When `name` IS the default branch (or no default can be resolved), all
    of its commits are listed.
    """
    args = ["log", "--format=%cd\t%s", "--date=format:%Y-%m-%d %H:%M"]
    if mine:
        email = resolve_commit_email(root=root)
        if email:
            args.append(f"--author={email}")
    if limit:
        args.append(f"--max-count={limit}")
    base, default_short = default_branch_ref(root)
    if base and name != default_short:
        args.append(f"{base}..{name}")
    else:
        args.append(name)
    commits: list[tuple[str, str]] = []
    for line in run_git(root, args).splitlines():
        if not line.strip():
            continue
        when, _, subject = line.partition("\t")
        commits.append((when, subject))
    return commits
