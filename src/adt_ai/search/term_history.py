"""The `search TERM` layers read from the project, not the mirror: GIT and FILES (ADT #895).

GIT reads the branch's commit store, the one `rebuild` keeps and the history
search reads, and never runs `git log`: a commit hits when its subject or a path
it changed contains TERM. FILES asks `git grep` for the working tree as it is,
untracked files included, since that is the one tool that already knows which
files are ignored and which are binary.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

from adt_ai.search.term_model import (
    Hit,
    TermRequest,
    TermResult,
    definition_flag,
    excerpt,
    is_big,
    line_matches,
    sql_term,
)
from adt_ai.shared.commit_cache import current_branch, open_store, store_path
from adt_ai.shared.commit_store import CommitFilter, CommitStore, StoredCommit
from adt_ai.shared.git_files import git_output, run_git

#: Commits read per page of one store query.
PAGE_SIZE = 500

#: `git grep` exits 1 when nothing matched, which is an answer, not a failure.
_GREP_NO_MATCH = 1


def is_work_tree(root: Path) -> bool:
    return git_output(root, ["rev-parse", "--is-inside-work-tree"]) == "true"


def search_git_layer(request: TermRequest, result: TermResult) -> None:
    """GIT: every commit on the branch whose subject or changed path holds TERM."""
    if "GIT" not in request.layers:
        return
    root = request.root
    if request.branch is None and not is_work_tree(root):
        result.not_searched.append(
            ("GIT", "the project root is not a git working tree, name a store with -branch")
        )
        return
    branch = request.branch or current_branch(root)
    try:
        path = store_path(root, request.cache_file_template, branch)
        if not path.is_file():
            raise ValueError(
                f"no commit store for branch {branch}, and git could not build it"
            )
        with open_store(root, branch, request.cache_file_template) as store:
            if store.ceiling() is None:
                raise ValueError(
                    f"the commit store for branch {branch} is empty"
                )
            result.searched.add("GIT")
            result.hits.extend(_commit_hits(store, request.term))
    except ValueError as error:
        result.not_searched.append(("GIT", str(error)))


def _commit_hits(store: CommitStore, term: str) -> Iterator[Hit]:
    """One `SUMMARY` row and one `FILE` row per matching path, newest commit first."""
    for commit in _matching_commits(store, term):
        if line_matches(commit.summary, term):
            yield Hit(
                source    = "GIT",
                component = str(commit.number),
                prop      = "SUMMARY",
                excerpt   = excerpt(commit.summary),
            )
        for path in sorted({*commit.files, *commit.deleted}):
            if line_matches(path, term):
                yield Hit(source="GIT", component=str(commit.number), prop="FILE", excerpt=path)


def _matching_commits(store: CommitStore, term: str) -> list[StoredCommit]:
    """The commits either pushdown admits, merged newest first.

    The store ANDs its terms, and a hit is a subject OR a path, so the two are
    asked apart. A term SQLite cannot fold is asked with no pushdown at all and
    decided commit by commit in `_commit_hits`.
    """
    folded = sql_term(term)
    if folded is None:
        criteria = [CommitFilter()]
    else:
        criteria = [CommitFilter(summary_terms=(folded,)), CommitFilter(path_terms=(folded,))]
    found: dict[int, StoredCommit] = {}
    for criterion in criteria:
        below: int | None = None
        while True:
            page = store.search(criterion, limit=PAGE_SIZE, below=below)
            found.update((commit.number, commit) for commit in page)
            if len(page) < PAGE_SIZE:
                break
            below = page[-1].number
    return [found[number] for number in sorted(found, reverse=True)]


def search_files_layer(request: TermRequest, result: TermResult) -> None:
    """FILES: `git grep` over the working tree, untracked files included."""
    if "FILES" not in request.layers:
        return
    root = request.root
    if not is_work_tree(root):
        result.not_searched.append(("FILES", "the project root is not a git working tree"))
        return
    result.searched.add("FILES")
    result.hits.extend(_file_hits(root, request.term))


def _file_hits(root: Path, term: str) -> Iterator[Hit]:
    """One row per matching line, or one per file for a minified or big file.

    `-z` puts a NUL after the path and after the line number, so a path holding
    a colon cannot be misread as the start of the line number.
    """
    listed: set[str] = set()
    for path, number, line in _grep(root, term):
        if is_big(path, (root / path).stat().st_size):
            if path not in listed:
                listed.add(path)
                yield Hit(source="FILES", component=path)
            continue
        yield Hit(
            source    = "FILES",
            component = path,
            line      = number,
            flag      = definition_flag(line, term),
            excerpt   = excerpt(line),
        )


def _grep(root: Path, term: str) -> Iterator[tuple[str, int, str]]:
    args = ["grep", "-n", "-z", "-I", "-i", "-F", "--untracked", "--no-color", "-e", term]
    try:
        output = run_git(root, args)
    except subprocess.CalledProcessError as error:
        if error.returncode != _GREP_NO_MATCH:
            raise
        return
    # Split on `\n` alone: `splitlines()` also breaks on a form feed or a
    # Unicode line separator inside a matching line, and loses the rest of it.
    for record in output.split("\n"):
        if not record:
            continue
        path, number, line = record.split("\0", 2)
        yield path, int(number), line


__all__ = [name for name in globals() if not name.startswith("_")]
