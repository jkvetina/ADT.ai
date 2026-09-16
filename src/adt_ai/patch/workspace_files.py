"""`patch -create -files_ws`: every workspace static file, not only the changed ones.

ADT #812. A bare patch carries the workspace static files its selected commits
touched. With `-files_ws` it carries all of them, and "all" is read from the
same source the content mode reads bytes from, so the listing and the payload
cannot disagree:

* **committed** (the default): the tree of the NEWEST selected commit. A file
  that commit's tree does not hold was never committed by the time the patch was,
  and a working-tree edit to one it does hold must not leak in, so each file is
  pinned to that commit for its bytes (`content._blob_ref`).
* **head**: `HEAD`'s tree; the bytes are per file, as `-head` already reads them.
* **local** and **nosnap**: the working tree, untracked files included, ignored
  files not.

Application static files are not widened. Jan, on the card: *"No need for app
files, user either deploy changed files or the full app"*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adt_ai.patch.content import CONTENT_MODE_COMMITTED, CONTENT_MODE_HEAD
from adt_ai.patch.layout import is_apex_workspace_static_file
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_files import run_git_paths


def workspace_static_files(
    root: Path,
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    content_mode: str,
) -> dict[str, str | None]:
    """Every workspace static file the mode can see, mapped to its pinned ref.

    The ref is the commit to read a file's bytes from when no selected commit
    touched it, and ``None`` where the mode resolves bytes its own way.
    """
    if content_mode == CONTENT_MODE_COMMITTED:
        if not records:
            return {}
        ref: str | None = records[-1].commit_hash
        paths = run_git_paths(root, ["ls-tree", "-r", "--name-only", str(ref)])
    elif content_mode == CONTENT_MODE_HEAD:
        ref = None
        paths = run_git_paths(root, ["ls-tree", "-r", "--name-only", "HEAD"])
    else:
        ref = None
        listed = run_git_paths(root, ["ls-files", "--cached", "--others", "--exclude-standard"])
        paths = [path for path in listed if (root / path).is_file()]
    return {
        path: ref
        for path in sorted(set(paths))
        if is_apex_workspace_static_file(path, config)
    }
