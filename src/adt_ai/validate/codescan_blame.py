"""Who last changed the file a new codescan finding sits in (ADT #1023).

A new finding is somebody's to fix, and the one to ask is whoever changed its
file last. The answer is read off the branch's commit store, the one `rebuild`
keeps under `repo_commits_file` and `search` reads: the newest stored commit
carrying the file is its last change. Nothing here runs `git blame`: the store
already answers per file in one indexed read, where git would cost a process
per file, and a finding about a whole component has no line to blame anyway.

**An answer it cannot give is no line, never a failure.** No git checkout, no
store for the branch, a store refusing to open, or a file no stored commit
carries all leave the stanza as it was: the attribution is a hint beside the
finding, and the gate's verdict never waits on it. The store is never created
here, so a project that never ran `rebuild` gets no `config/commits/` from a
scan.
"""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from adt_ai.shared.author_aliases import author_aliases, canonical_author
from adt_ai.shared.commit_cache import (
    DEFAULT_COMMITS_TEMPLATE,
    current_branch,
    open_store,
    store_path,
)
from adt_ai.shared.commit_store import StoredCommit
from adt_ai.shared.fixed_width import LABEL_ELISION

#: The width of a hash on this line, the one `search` prints a commit by.
SHORT_HASH = 8


def last_changes(
    root   : Path,
    config : Mapping[str, Any] | None,
    paths  : Iterable[str],
) -> dict[str, StoredCommit]:
    """The newest stored commit carrying each repository path; ``{}`` when none can say."""
    wanted = sorted(set(paths))
    if not wanted:
        return {}
    template = str((config or {}).get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE)
    try:
        branch = current_branch(root)
        if not store_path(root, template, branch).is_file():
            return {}
        with open_store(root, branch, template) as store:
            found = {path: store.last_change(path) for path in wanted}
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        return {}
    return {path: commit for path, commit in found.items() if commit is not None}


def blame_line(
    commit  : StoredCommit,
    config  : Mapping[str, Any] | None,
    width   : int,
) -> str:
    """`<author>, <hash> <subject>`, the subject cut to ``width`` columns.

    The author goes through `repo_authors`, as every history reader shows it.
    The subject is a look-up hint, the hash is what finds the commit, so it is
    cut rather than wrapped, keeping the stanza's one line per attribution.
    """
    try:
        aliases = author_aliases(config or {})
    except ValueError:
        aliases = {}
    author = canonical_author(commit.author, aliases)
    head = f"{author}, {commit.id[:SHORT_HASH]}"
    summary = " ".join(commit.summary.split())
    line = f"{head} {summary}" if summary else head
    if len(line) <= width:
        return line
    return line[: max(width - len(LABEL_ELISION), 0)] + LABEL_ELISION


__all__ = ["SHORT_HASH", "blame_line", "last_changes"]
