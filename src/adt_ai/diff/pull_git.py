"""The git half of `diff -restore`: a work tree in, git's own record out (ADT #893, #897).

Jan, 2026-09-19: *"if this is passed, you will resurrect these target versions
into their location in current/requested branch, so user can see all the
changes there!"* The restore writes into the project checkout, so three things
belong to git rather than to the exporters:

* **`-root` must be a git work tree, checked before anything connects.** A
  checkout carrying edits is no longer refused (`#897`). Jan: *"before we do
  -pull, we should commit what we have as "WIP" and dont push, so after -pull
  we would be able to see the changes and not lost anything."* The screen saves
  it with `shared/git_files.save_work_in_progress` right before the branch
  switch, so `git diff` shows the restore alone and nothing is lost.
* **`-branch` picks where the restore lands**, an existing branch or a new one
  made from `HEAD`, and without it the current branch does. Jan chose *"Current
  branch, or -branch override"*. The restore itself commits and pushes nothing:
  the user reviews the working tree.
* **What the restore changed is read back from `git status`**, not from what
  the exporters meant to write, because a rewrite of identical bytes is not a
  change and git is the one that decides that.

Every call goes through `shared/git_files.git_output`, the adapter's
returncode-tolerant half, so a refusal is an answer here rather than a raise.
"""
from __future__ import annotations

import os
from pathlib import Path

from adt_ai.shared.git_files import git_output, run_git_bytes

#: What the `RESTORED FILES:` listing says a file went through.
NEW      = "NEW"
MODIFIED = "MODIFIED"
DELETED  = "DELETED"


def checkout_refusal(root: Path, branch: str | None) -> str | None:
    """Why `-restore` cannot write into `root`, or `None` when it can."""
    if git_output(root, ["rev-parse", "--is-inside-work-tree"]) != "true":
        return f"-restore writes into -root, and {root} is not a git work tree."
    if branch is not None and git_output(root, ["check-ref-format", "--branch", branch]) is None:
        return f"-branch {branch} is not a valid branch name."
    return None


def switch_branch(root: Path, branch: str | None) -> str:
    """Put the checkout on `branch`, creating it from `HEAD` when it is new, and name it."""
    if branch:
        exists = git_output(root, ["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"])
        switch = ["switch", branch] if exists is not None else ["switch", "-c", branch]
        if git_output(root, switch) is None:
            raise RuntimeError(
                f"git could not switch to {branch} in {root}; the checkout is left as it was."
            )
    name = git_output(root, ["branch", "--show-current"])
    if name:
        return name
    return f"HEAD {git_output(root, ['rev-parse', '--short', 'HEAD'])}"


def pulled_files(root: Path) -> list[tuple[str, str]]:
    """Every file under `root` git sees as changed, project-relative, with what happened to it."""
    top = Path(str(git_output(root, ["rev-parse", "--show-toplevel"]))).resolve()
    output = run_git_bytes(
        root, ["status", "--porcelain", "-z", "--untracked-files=all", "--", "."]
    ).decode("utf-8", errors="surrogateescape")
    base = root.resolve()
    rows = [
        (Path(os.path.relpath(top / record[3:], base)).as_posix(), _status(record[:2]))
        for record in output.split("\0")
        if record
    ]
    return sorted(rows)


def _status(code: str) -> str:
    """`??` and `A` are new, `D` in either column is deleted, anything else modified."""
    if code == "??" or "A" in code:
        return NEW
    if "D" in code:
        return DELETED
    return MODIFIED


__all__ = [name for name in globals() if not name.startswith("_")]
