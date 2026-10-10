"""Every file a `patch -create` build's own commit does not cover (ADT #967).

Split out of `patch/build.py` when ADT #1064 pushed that module past the 24 KB
context guard (`tests/contracts/test_context_file_size.py`): the repo-wide
question is git's, asked once per build, and has no other tie to the writer.
"""

from __future__ import annotations

from pathlib import Path

from adt_ai.patch.content import CONTENT_MODE_LOCAL
from adt_ai.shared.git_uncommitted import every_uncommitted_path


def repo_uncommitted(
    root: Path,
    folder: Path,
    generated_paths: set[str],
    *,
    mode: str,
) -> list[str]:
    """Every file this build's own commit does not yet cover, repo-wide (ADT #967).

    Jan, mid-run: *"if we have uncommitted changes in the repo, it should list
    the files as a file tree ... Looks like you are printing something, but not
    all uncommitted files, why is that?"* The old `WARNING - UNCOMMITTED FILES:`
    asked a narrower question, `SchemaReport.uncommitted`'s own `_uncommitted()`
    (removed by this card) only ever checked the patch's OWN files; this asks
    git about the whole checkout, once, and `cli/patch_create_warnings.py`
    renders whatever comes back as a file tree.

    Two exclusions, both about what THIS build itself just did rather than what
    was already sitting in the checkout dirty: a generated helper this run wrote
    (``generated_paths``, the same set `report.py` excludes from its own,
    narrower listing) and anything under the patch folder this call is in the
    middle of writing -- a brand new `patch/<code>/` is untracked by
    definition, and reporting it here would be the build warning about its own
    output.

    **Silent under `-local`**, the same carve-out the old `_uncommitted` made:
    that mode ships the working tree on purpose, so an uncommitted file there is
    the instruction rather than a surprise.
    """
    if mode == CONTENT_MODE_LOCAL:
        return []
    try:
        folder_prefix = folder.resolve().relative_to(root.resolve()).as_posix() + "/"
    except ValueError:
        # The patch folder sits outside `root` (a caller's own arrangement,
        # never the CLI's): nothing to strip, so every dirty path is reported.
        folder_prefix = None
    return [
        path
        for path in every_uncommitted_path(root)
        if path not in generated_paths
        and (folder_prefix is None or not path.startswith(folder_prefix))
    ]


__all__ = ["repo_uncommitted"]
