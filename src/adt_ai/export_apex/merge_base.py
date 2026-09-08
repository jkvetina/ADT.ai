"""What an APEXlang export is BASED ON, recorded so a refusal ends in a rebase.

`patch -deploy -app` already refuses an import onto an application somebody moved
since the tree was exported (`patch/apex_signature.py`). Until this module the
refusal could only name two checksums and tell the developer to export again and
reconcile by hand, because ADT recorded the identity of the base state and never
the base state itself. Two mutable copies and no merge base is compare-and-swap:
a three-way merge needs base, ours and theirs.

APEXlang is the format that makes the third one worth having. It carries no
component ids and writes one file per page, so two branches that touched
different pages merge as text. What was missing was a commit to merge against,
and this module supplies both halves of it:

  * ``head_commit`` is the free one. The tree on disk was exported while the
    repository sat at some commit, so that commit is what the change is based
    on, and recording it costs one `rev-parse`.
  * ``mirror_export`` is the shared one, behind `-mirror db/<ENV>`. Every export
    of that environment is committed onto one ref, so the commit naming the
    state now live on the target is a commit every developer has. `git rebase
    db/DEV` is then a real instruction rather than a shape of words.

**The mirror never touches HEAD, the index or the working tree.** It reads the
ref, stages the exported folder through an index file of its own, writes a tree,
commits it with `commit-tree` and moves the ref with a compare-and-swap
`update-ref`. A developer with staged work finds it exactly as they left it, and
an exporter that lost a race to another exporter records nothing rather than
overwriting a commit nobody has seen yet.

**The tree is mirrored at its own repository path, not at the ref's root.** That
is the whole point: a branch commit and a mirror commit have to touch the same
paths for git to three-way merge them.

**Nothing here can fail an export.** The tree on disk is the deliverable; the
base commit is a fact recorded beside it. A root outside version control, a
repository with no commits, a folder outside the root, a ref that moved under the
run: every one answers "" and the export finishes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from adt_ai.shared.git_files import git_output

#: Where a short ref name lands. `db/dev` is a branch, so a developer can fetch
#: it, log it and rebase onto it with no ref syntax to learn.
MIRROR_REF_PREFIX = "refs/heads/"

#: What a value has to open with to be taken as a ref path the user spelled out.
_FULL_REF_PREFIX = "refs/"

#: `update-ref`'s spelling of "and it must not exist yet", which is the guard the
#: very first mirror export needs: an empty old value refuses when the ref is
#: already there, so two first exporters cannot both think they created it.
_NO_SUCH_REF = ""


def mirror_message(app_id: int, environment: str) -> str:
    """The subject line of a mirror commit: which application, from where.

    Read by a human scanning `git log db/dev` for the export their branch is
    behind, so it names the two facts that separate one mirror commit from the
    next rather than restating the ref.
    """
    where = f" from {environment}" if environment else ""
    return f"export_apex -apexlang: app {app_id}{where}"


def head_commit(root: Path) -> str:
    """The commit ``root`` sits at, or "" when it sits at none.

    Empty covers three ordinary cases and no error case: a project outside git,
    a repository whose first commit has not been made, and a `git` that is not
    installed at all.
    """
    return git_output(root, ["rev-parse", "HEAD"]) or ""


def mirror_ref_name(ref: str) -> str:
    """The full ref ``ref`` names: `db/dev` is a branch, `refs/...` is verbatim.

    A blank value names nothing, which is what `-mirror` being absent looks like
    by the time it reaches here.
    """
    value = str(ref or "").strip()
    if not value:
        return ""
    return value if value.startswith(_FULL_REF_PREFIX) else f"{MIRROR_REF_PREFIX}{value}"


def mirror_export(root: Path, ref: str, tree_root: Path, *, message: str) -> str:
    """Commit ``tree_root`` onto ``ref``; the new commit, or "" if none was made.

    Returns the EXISTING tip when the export changed nothing, so a ref carries
    one commit per real change rather than one per run, and the recorded base
    still names a commit that holds this exact tree.
    """
    full_ref = mirror_ref_name(ref)
    relative = _repository_path(root, tree_root)
    if not full_ref or not relative:
        return ""
    parent = git_output(root, ["rev-parse", "--verify", "--quiet", full_ref]) or ""
    tree = _staged_tree(root, parent, relative)
    if not tree:
        return ""
    if parent and tree == git_output(root, ["rev-parse", f"{parent}^{{tree}}"]):
        return parent
    parentage = ["-p", parent] if parent else []
    commit = git_output(root, ["commit-tree", tree, *parentage, "-m", message])
    if not commit:
        return ""
    # The value read above is handed back as the expected one, so a second
    # exporter that landed in between takes the ref and this run reports nothing.
    moved = git_output(root, ["update-ref", full_ref, commit, parent or _NO_SUCH_REF])
    return "" if moved is None else commit


def _staged_tree(root: Path, parent: str, relative: str) -> str:
    """The tree of ``parent`` with ``relative`` replaced by what is on disk.

    Staged through a throwaway index so the developer's own index is untouched,
    and `--force` because the mirror ref is not their branch: a project that
    keeps the generated tree out of its own history still gets a shared base.
    """
    with tempfile.TemporaryDirectory(prefix="adt-mirror-") as folder:
        environment = {"GIT_INDEX_FILE": str(Path(folder) / "index")}
        seed = ["read-tree", parent] if parent else ["read-tree", "--empty"]
        if git_output(root, seed, environment) is None:
            return ""
        if git_output(root, ["add", "--all", "--force", "--", relative], environment) is None:
            return ""
        return git_output(root, ["write-tree"], environment) or ""


def _repository_path(root: Path, tree_root: Path) -> str:
    """``tree_root`` as a path inside ``root``, or "" when it is not one.

    A tree that holds no file answers "" as well, the same distinction
    `patch.apex_signature.tree_signature` already draws between "no tree" and "a
    tree of empty files": `git add` is perfectly happy to stage an empty folder
    as nothing at all, so without this the mirror would carry a commit claiming
    an export that wrote no application.
    """
    if not tree_root.is_dir() or not any(path.is_file() for path in tree_root.rglob("*")):
        return ""
    try:
        return tree_root.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return ""


__all__ = [
    "MIRROR_REF_PREFIX",
    "head_commit",
    "mirror_export",
    "mirror_message",
    "mirror_ref_name",
]
