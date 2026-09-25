"""The repo-wide `git status`, split out of `git_files.py` (ADT #967).

Split to respect the 24 KB context-size guard
(`tests/contracts/test_context_file_size.py`): `git_files.py` already carried
every other git reader in the tree, and this one function pushed it over. One
function, its own module, same seam `export_apex_validate.py` sits on.
"""

from __future__ import annotations

from pathlib import Path

from adt_ai.shared.git_files import run_git_bytes

__all__ = ["every_uncommitted_path"]


def every_uncommitted_path(root: Path) -> list[str]:
    """Every dirty or untracked path in the WHOLE repo, from one `git status` call.

    ADT #967. Jan, mid `patch -create`: *"if we have uncommitted changes in the
    repo, it should list the files as a file tree ... Looks like you are
    printing something, but not all uncommitted files, why is that?"*
    Unfiltered, unlike `git_files.git_status_paths`, which only ever answers
    for the specific paths a caller names; the caller here (`patch/build.py`)
    filters the result down to what is worth reporting.

    **A rename or a copy prints two NUL-terminated fields, and they are NOT
    old-then-new.** Verified against a real rename (`git status --porcelain -z`
    on a repo that renamed a tracked file): the record is `R  new_path\\0
    old_path\\0`, this status's own path field already IS the current name, and
    the record right after it is the path the tree no longer holds. That is the
    opposite order `changed_files` (`git_files.py`) reads out of `diff-tree -z`
    for the same two statuses, so the two cannot share one reader: this drops
    that second field rather than folding it into `path[3:]` as a path of its
    own, which would otherwise report a file under a name the working tree does
    not have and silently swallow whatever real record followed it.

    **Sorted by path**, because the renderer draws a tree and git does not hand
    the records over in one: tracked changes come first and untracked files
    after them, so on SANDBOX a folder holding both read `demo_rec_*` above
    `adt_anno_*`.
    """
    output = run_git_bytes(
        root, ["status", "--porcelain", "-z", "--untracked-files=all"]
    ).decode("utf-8", errors="surrogateescape")
    records = [record for record in output.split("\0") if record]
    paths: list[str] = []
    index = 0
    while index < len(records):
        record = records[index]
        paths.append(record[3:])
        if record[:1] in {"R", "C"} or record[1:2] in {"R", "C"}:
            index += 1  # the old path a rename/copy carried, not a file of its own
        index += 1
    return sorted(paths)
