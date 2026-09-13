"""Keep an APEXlang tree complete where it sits, instead of assembling a copy.

`export_apex -apexlang` writes metadata only: the static-file payloads go to the
sibling `files/` folder through `-files`, so the repository holds exactly one copy
of each file. The compiler does not accept that split. `static-files.apx` declares
every payload as `file "css/app.css"` and resolves each against
`shared-components/static-files/css/app.css` inside the tree, which an `-apexlang`
export does not carry, so a committed tree reports one `REFERENCE_NOT_FOUND` per
payload and `apex import`, which compiles before it writes, refuses it (ADT #160,
#163).

ADT #165 closed that by building a whole second tree under `config/temp/apexlang/`
and pointing the compiler at the copy. ADT #765 is the bill for it. The staging
folder was named after the app folder alone, so every `apexlang` tree sharing that
name resolved to one directory, each run wiped it before the previous target was
ever compiled, and a project with patch snapshots reported `EMPTY` for its real
export. Jan, 2026-09-10: *"WHY THE FUCK you dont fix the source, the root cause"*
and, naming the fix this module is: *"why you dont keep in sync the app files as
hard links in the original apexlang location so we can keep import/validate
without this stage shit"*.

So the payloads are linked into the export's OWN tree and kept there. The tree on
disk is the tree that compiles, `validate` and `patch -deploy` both read the folder
the user can open, and there is no second copy to collide with.

**One copy of bytes, and no second path in git.** `os.link` gives the existing
inode another name, so nothing is read or rewritten, measured here as a link count
of 2 against one inode. The repository's private `.git/info/exclude` ignores the
linked payload folder without putting housekeeping inside the APEXLang compiler's
input tree. That distinction is functional: a nested `.gitignore` is a regular
payload to the compiler and would be imported as an application static file.

**Hardlinks, not symlinks.** A hardlink is indistinguishable from a regular file to
any directory walker, while Java's `Files.walk` does not descend a symlinked
directory unless the caller passes `FOLLOW_LINKS`, so a symlinked tree risks the
compiler skipping whole folders. A symlink is also no cheaper: it is a directory
entry plus an inode of its own. A copy is the fallback when a filesystem refuses to
link.

**A payload we do not have is left missing, never touched into existence.**
Measured 2026-07-27 against one tree in three variants: real payload bytes and
payloads truncated to zero bytes validate identically, while deleting one file
produces its `REFERENCE_NOT_FOUND`. The compiler checks that the referenced path
exists, not what is in it, so an empty placeholder would turn the gate green and
then import an application with broken images. A genuine gap must reach the
compiler and be reported.
"""

from __future__ import annotations

import contextlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared import text_files
from adt_ai.shared.internal_paths import CONFIG_DIR

# Where the compiler expects the payloads, relative to the app tree root.
PAYLOAD_DIR = ("shared-components", "static-files")

# Where ADT #165 staged its copies, relative to the project root. Nothing writes
# here any more; `drop_legacy_staging` exists to clear what already did.
LEGACY_STAGING_DIR = (CONFIG_DIR, "temp", "apexlang")

# Keeps the linked payloads out of git without touching the project's tracked
# `.gitignore` or putting a sentinel inside the compiler's input tree.
EXCLUDE_PATTERN = "**/apexlang/shared-components/static-files/"
# Retained for readers that must ignore or remove trees created by older builds.
IGNORE_NAME = ".gitignore"

Linker = Callable[[Path, Path], None]


@dataclass(frozen=True)
class PayloadLinks:
    root    : Path
    linked  : int
    removed : int = 0
    copied  : bool = False


def payload_root(apexlang_root: Path) -> Path:
    """Where this tree's static-file payloads belong."""
    return apexlang_root.joinpath(*PAYLOAD_DIR)


def drop_legacy_staging(root: Path) -> bool:
    """Retire the staging tree ADT #165 built, if this project still carries one.

    Deleting the module that wrote a folder does not delete the folder. Every
    project that ran the old code keeps its staged copy, and nothing will ever
    report it: the path is git-ignored, so `git status` stays clean while a full
    second copy of each application sits under it, measured at 2.7 MB for a single
    app in one live project. The code that created it is gone, so the sweep it owes
    runs from the module that replaced it.

    Only the `apexlang/` subtree is removed. `config/temp/` itself is the shared
    folder every SQLcl call writes its throwaway script into.
    """
    legacy = root.joinpath(*LEGACY_STAGING_DIR)
    if not legacy.is_dir():
        return False
    with contextlib.suppress(OSError):
        shutil.rmtree(legacy)
    return not legacy.exists()


def link_payloads(
    apexlang_root : Path,
    files_root    : Path,
    link          : Linker | None = None,
) -> PayloadLinks:
    """Reconcile the tree's payload folder against the sibling `files/` export.

    Idempotent and incremental, which is the other half of what ADT #765 asked
    for: a run that changed one page relinks nothing, because every entry already
    names the inode its source names. Reconciled rather than rebuilt, so editing
    one page does not churn every entry in the folder.

    A payload whose source is gone is removed, mirroring the `apexlang/` folder's
    own contract: a static file deleted in App Builder must not survive here
    either. Nothing outside the payload folder is ever touched.
    """
    if not apexlang_root.is_dir():
        return PayloadLinks(payload_root(apexlang_root), 0)

    target_root = payload_root(apexlang_root)
    sources = _sources(files_root)
    if not sources and not target_root.is_dir():
        return PayloadLinks(target_root, 0)

    target_root.mkdir(parents=True, exist_ok=True)
    _drop_old_ignore(target_root)
    _ensure_git_excluded(apexlang_root)

    state = _LinkState(link or os.link)
    for relative, source in sorted(sources.items()):
        state.reconcile(source, target_root / relative)
    removed = _drop_stale(target_root, set(sources))
    return PayloadLinks(target_root, len(sources), removed, state.copied)


def _sources(files_root: Path) -> dict[Path, Path]:
    """Every exported payload, keyed by the path the compiler will resolve.

    `files/<X>` maps one to one onto `shared-components/static-files/<X>`, same
    relative paths, no rename and no transform. That is what makes this a link
    operation rather than a translation.
    """
    if not files_root.is_dir():
        return {}
    return {
        source.relative_to(files_root): source
        for source in files_root.rglob("*")
        if source.is_file()
    }


def _drop_old_ignore(target_root: Path) -> None:
    """Remove the pre-fix sentinel before a compiler can mistake it for payload."""
    with contextlib.suppress(OSError):
        (target_root / IGNORE_NAME).unlink()


def _ensure_git_excluded(apexlang_root: Path) -> None:
    """Ignore linked payloads through the repository's untracked exclude file."""
    exclude = _git_exclude(apexlang_root)
    if exclude is None:
        return
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if EXCLUDE_PATTERN in {line.strip() for line in existing.splitlines()}:
            return
        prefix = existing + ("\n" if existing and not existing.endswith("\n") else "")
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_text(exclude, prefix + EXCLUDE_PATTERN + "\n")
    except OSError:
        # Git hygiene is best-effort; compiling the correct tree is mandatory.
        return


def _git_exclude(path: Path) -> Path | None:
    """Resolve `.git/info/exclude` for a checkout or linked worktree."""
    for root in (path, *path.parents):
        marker = root / ".git"
        if marker.is_dir():
            return marker / "info" / "exclude"
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
            return git_dir / "info" / "exclude"
        except (OSError, ValueError):
            return None
    return None


def _drop_stale(target_root: Path, keep: set[Path]) -> int:
    """Remove payloads whose source is gone, then the folders they emptied."""
    removed = 0
    for path in sorted(target_root.rglob("*"), reverse=True):
        if path.is_dir():
            with contextlib.suppress(OSError):
                path.rmdir()
            continue
        relative = path.relative_to(target_root)
        if relative in keep:
            continue
        with contextlib.suppress(OSError):
            path.unlink()
            removed += 1
    return removed


class _LinkState:
    """Carries the linker plus whether it ever had to fall back to copying."""

    def __init__(self, linker: Linker) -> None:
        self.linker = linker
        self.copied = False

    def reconcile(self, source: Path, target: Path) -> None:
        if _same_file(source, target):
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() or target.is_symlink():
            with contextlib.suppress(OSError):
                target.unlink()
        try:
            self.linker(source, target)
        except OSError:
            # Cross-device, or a filesystem with no link support. Correctness
            # beats the disk saving: the bytes still have to arrive.
            shutil.copy2(source, target)
            self.copied = True


def _same_file(source: Path, target: Path) -> bool:
    """Is `target` already the name this source wears here?

    The inode compare is what makes a second run free. A copied fallback has its
    own inode, so it is compared on size and mtime instead rather than being
    relinked on every run.
    """
    try:
        left, right = source.stat(), target.stat()
    except OSError:
        return False
    if (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino):
        return True
    return (left.st_size, int(left.st_mtime)) == (right.st_size, int(right.st_mtime))


__all__ = [
    "EXCLUDE_PATTERN",
    "IGNORE_NAME",
    "LEGACY_STAGING_DIR",
    "PAYLOAD_DIR",
    "PayloadLinks",
    "drop_legacy_staging",
    "link_payloads",
    "payload_root",
]
