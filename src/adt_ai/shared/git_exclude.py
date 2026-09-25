"""Keep a file ADT generates out of the user's git, without touching `.gitignore`.

The repository's private `.git/info/exclude` is the one ignore list ADT writes
to. The project's tracked `.gitignore` is the user's, and a nested `.gitignore`
inside a folder ADT fills can be read as content by whatever consumes that
folder: the APEXlang compiler imports one as an application static file, which
is why `apex_payloads` moved here first (ADT #765). The `-app` revert backups
(ADT #963) take the same route: a full export of a target application has no
business in a commit.

Best-effort by design. A repository nobody can write the exclude file of, or a
project that is not in git at all, loses nothing but the hygiene, and the work
that produced the file never fails over it.
"""

from __future__ import annotations

from pathlib import Path

from adt_ai.shared import text_files


def exclude_from_git(start: Path, pattern: str) -> None:
    """Add ``pattern`` to the exclude file of the repository holding ``start``.

    A pattern already listed is not written again, so every run can ask.
    """
    found = git_exclude_file(start)
    if found is None:
        return
    exclude = found[1]
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if pattern in {line.strip() for line in existing.splitlines()}:
            return
        prefix = existing + ("\n" if existing and not existing.endswith("\n") else "")
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_text(exclude, prefix + pattern + "\n")
    except OSError:
        return


def git_exclude_file(path: Path) -> tuple[Path, Path] | None:
    """The working tree holding ``path`` and its `.git/info/exclude`, if in git.

    A linked worktree carries a `.git` FILE naming its git dir, and git reads
    the exclude file of the COMMON repository, so that is the one resolved.
    """
    for root in (path, *path.parents):
        marker = root / ".git"
        if marker.is_dir():
            return root, marker / "info" / "exclude"
        if not marker.is_file():
            continue
        try:
            label, value = marker.read_text(encoding="utf-8").strip().split(":", 1)
            if label != "gitdir":
                return None
            git_dir = Path(value.strip())
            if not git_dir.is_absolute():
                git_dir = (root / git_dir).resolve()
            common = git_dir / "commondir"
            if common.is_file():
                git_dir = (git_dir / common.read_text(encoding="utf-8").strip()).resolve()
            return root, git_dir / "info" / "exclude"
        except (OSError, ValueError):
            return None
    return None


__all__ = ["exclude_from_git", "git_exclude_file"]
