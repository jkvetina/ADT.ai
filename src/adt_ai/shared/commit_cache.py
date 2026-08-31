"""Where a branch's commit store lives.

The store itself is `commit_store.py`; this module owns the path, which is the
part a project configures. `repo_commits_file` and `#BRANCH#` still choose it,
one file per branch, because that is the control Jan asked for: *"For a better
user control and easier removals for bloated branches I would prefer 1 file per
branch"* (2026-08-15).

The configured path must name the current ``.db`` SQLite format. An earlier
YAML path or cache is rejected rather than migrated because it cannot represent
complete file-status data.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from adt_ai.shared.commit_store import CommitStore
from adt_ai.shared.subprocess_env import safe_subprocess_environment

#: Shipped `repo_commits_file`. One store per branch, under the project's own
#: `config/commits/`, which `#316`'s sweep deliberately left where it was.
DEFAULT_COMMITS_TEMPLATE = "./config/commits/#BRANCH#.db"

#: Git accepts far more than should appear in one readable artifact filename.
#: ADT deliberately has no escaping scheme: only this small alphabet is valid,
#: and `/` is flattened because it is Git's ordinary branch separator.
_SIMPLE_BRANCH = re.compile(r"[A-Za-z0-9._/-]+")


def current_branch(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=safe_subprocess_environment(),
    )
    return result.stdout.strip() or "HEAD"


def branch_filename(branch: str) -> str:
    """One simple branch name flattened into one readable filename.

    Unsupported characters are rejected rather than escaped. The exact branch
    name is also recorded inside the SQLite store, so the unavoidable filename
    collision between ``feat/a`` and ``feat-a`` fails instead of sharing data.
    The real name still drives Git operations and console output.
    """
    components = branch.split("/")
    if (
        not _SIMPLE_BRANCH.fullmatch(branch)
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise ValueError(
            f"Unsupported branch name {branch!r}; use only letters, digits, ., _, -, or /"
        )
    return branch.replace("/", "-")


def cache_path(
    root: Path,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
    branch: str = "main",
) -> Path:
    # Only the substituted branch is sanitized. The template's own separators
    # are the folder layout the project configured through `repo_commits_file`,
    # and flattening those would move every existing cache.
    resolved = cache_file_template.replace("#BRANCH#", branch_filename(branch))
    path = Path(resolved).expanduser()
    return path if path.is_absolute() else root / path


def store_path(
    root: Path,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
    branch: str = "main",
) -> Path:
    """Where this branch's SQLite store sits, rejecting old configurations."""
    path = cache_path(root, cache_file_template, branch)
    if path.suffix.lower() != ".db":
        raise ValueError(
            f"repo_commits_file must name a .db SQLite store, not {path}. "
            "YAML commit history is no longer supported. Update the setting, remove "
            "any old YAML cache, then run 'adt rebuild'."
        )
    return path


def legacy_cache_path(
    root: Path,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
    branch: str = "main",
) -> Path:
    """Where the YAML cache this branch may still have sits."""
    return cache_path(root, cache_file_template, branch).with_suffix(".yaml")


def open_store(
    root: Path,
    branch: str,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
) -> CommitStore:
    """Open the branch's SQLite store and bind it to the exact branch name.

    YAML history caches are deliberately no longer migrated. Continuing from
    that lossy format would preserve guessed file statuses and incomplete
    history. A project that still has one must remove it and rebuild explicitly.
    """
    path = store_path(root, cache_file_template, branch)
    legacy = legacy_cache_path(root, cache_file_template, branch)
    if legacy.is_file():
        raise ValueError(
            f"Legacy YAML commit cache is no longer supported: {legacy}. "
            "Remove it, then run 'adt rebuild' to create a complete SQLite store."
        )
    store = CommitStore.open(path)
    try:
        store.claim_branch(branch)
        return store
    except BaseException:
        store.close()
        raise
