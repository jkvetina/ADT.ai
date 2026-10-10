"""What an APEXlang export is BASED ON: the commit the tree was exported at.

`patch -deploy -app` refuses an import onto an application somebody moved since
the tree was exported (`patch/apex_signature.py`). The checksum it compares
identifies the base state and nothing more, so the export also records the
commit the repository sat at when the tree was written (ADT #725), and the
refusal prints it as YOUR BASE.

A shared ref carrying every export was tried and removed (ADT #1062): its commits
held only the APEX tree and shared no history with the branches a developer
works on, so neither a rebase nor a merge onto it could work. The way out of a
refusal is the fetch the refusal names, and the reconciling is the developer's.

**Nothing here can fail an export.** The tree on disk is the deliverable; the
base commit is a fact recorded beside it. A root outside version control or a
repository with no commits answers "" and the export finishes.
"""

from __future__ import annotations

from pathlib import Path

from adt_ai.shared.git_files import git_output


def head_commit(root: Path) -> str:
    """The commit ``root`` sits at, or "" when it sits at none.

    Empty covers three ordinary cases and no error case: a project outside git,
    a repository whose first commit has not been made, and a `git` that is not
    installed at all.
    """
    return git_output(root, ["rev-parse", "HEAD"]) or ""


__all__ = ["head_commit"]
