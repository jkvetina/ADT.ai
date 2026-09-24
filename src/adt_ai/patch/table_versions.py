"""Which two versions of a table file a patch compares (ADT #494, moved by #753).

This is git's half of the table diff and it is unchanged: the versions a patch
carries, and the version standing on the target before it runs. It moved out of
`table_alter.py` when `#753` deleted that file, because the question it answers
was never the one that file was named for -- `table_alter` asked what CHANGED
between two `CREATE TABLE` texts, and Oracle answers that now
(`table_diff_runner.py`). Reading the two texts out of the repository is still
Python's job and always was.

Every text is decoded the way a shipped file is, through
`content.decode_repo_text` and the project's `repo_encoding` (ADT #923). A bare
UTF-8 decode here stopped a cp1250 project's `-create` on a raw
`UnicodeDecodeError` naming no file, for a table the same run shipped fine.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch.content import decode_repo_text
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_files import git_show


def _table_versions(
    root    : Path,
    file    : str,
    records : list[CommitRecord],
    *,
    config  : Mapping[str, Any] | None,
) -> list[tuple[int, str]]:
    versions: list[tuple[int, str]] = []
    for record in records:
        if file not in record.usable_files:
            continue
        content = git_show(root, record.commit_hash, file)
        if content is not None:
            versions.append((record.number, decode_repo_text(content, file, config)))
    return versions


def _table_baseline(
    root    : Path,
    file    : str,
    records : list[CommitRecord],
    *,
    config  : Mapping[str, Any] | None,
) -> str | None:
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
    return None if content is None else decode_repo_text(content, file, config)


def _body_at_content_hash(
    root         : Path,
    file         : str,
    window       : list[CommitRecord],
    content_hash : str,
    *,
    config       : Mapping[str, Any] | None,
) -> str | None:
    """``file`` as it looked when its content hashed to ``content_hash``.

    Searched newest first, because the same content can appear at several
    commits and the newest is the one whose blob is cheapest to reach and
    likeliest still present. `None` when no scanned commit recorded that hash,
    which is the case the caller reports rather than guesses at.

    Moved here from `helpers.py` by ADT #830, whose sequence ALTER reads the
    hash-mode base the same way the table ALTER does.
    """
    for record in sorted(window, key=lambda item: item.number, reverse=True):
        if record.files.get(file) != content_hash or not record.commit_hash:
            continue
        content = git_show(root, record.commit_hash, file)
        if content is not None:
            return decode_repo_text(content, file, config)
    return None


__all__ = ["_body_at_content_hash", "_table_baseline", "_table_versions"]
