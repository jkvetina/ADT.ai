"""Which two versions of a table file a patch compares (ADT #494, moved by #753).

This is git's half of the table diff and it is unchanged: the versions a patch
carries, and the version standing on the target before it runs. It moved out of
`table_alter.py` when `#753` deleted that file, because the question it answers
was never the one that file was named for -- `table_alter` asked what CHANGED
between two `CREATE TABLE` texts, and Oracle answers that now
(`table_diff_runner.py`). Reading the two texts out of the repository is still
Python's job and always was.
"""

from __future__ import annotations

from pathlib import Path

from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_files import git_show


def _table_versions(root: Path, file: str, records: list[CommitRecord]) -> list[tuple[int, str]]:
    versions: list[tuple[int, str]] = []
    for record in records:
        if file not in record.usable_files:
            continue
        content = git_show(root, record.commit_hash, file)
        if content is not None:
            versions.append((record.number, content.decode("utf-8")))
    return versions


def _table_baseline(root: Path, file: str, records: list[CommitRecord]) -> str | None:
    """The version of ``file`` standing before this patch touched it.

    Read from the PARENT of the first selected commit carrying the file, which
    is the state the target database sits at when the patch runs. `None` when
    nothing is there: the window's own first commit created the file, or that
    commit is the repository's first and has no parent.

    Resolved through the commit hash rather than a commit number. The numbers
    come from the commit store and count only what the store holds, so
    `number - 1` names a different commit wherever the store skips one, and
    names nothing at all for the oldest commit it has cached.
    """
    first = next((record for record in records if file in record.usable_files), None)
    if first is None:
        return None
    content = git_show(root, f"{first.commit_hash}^", file)
    return None if content is None else content.decode("utf-8")


__all__ = ["_table_baseline", "_table_versions"]
