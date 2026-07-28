"""Assemble a validatable APEXlang tree without duplicating a single byte.

`export_apex -apexlang` deliberately omits the `shared-components/static-files/`
payloads so `-files` stays the single static-file channel and the repo never
holds two copies of a static file. The compiler does not accept that: the
exported `static-files.apx` names each payload in a `fileName` property, so a
committed `apexlang/` tree reports one `REFERENCE_NOT_FOUND` per payload — and
since `apex import` validates first, the export is not importable as it stands
(cards `#160`, `#163`).

This module closes that gap the way a build step should: it mirrors `apexlang/`
into a gitignored staging tree with **hardlinked** files, then hardlinks
`files/**` into place under `shared-components/static-files/`. One inode per
file, no bytes copied, nothing new in git — the repo keeps its single copy and
the compiler gets the complete tree it needs. The same tree is what a future
`apex import` should be handed.

**Hardlinks, not symlinks.** A hardlink is indistinguishable from a regular file
to any directory walker; Java's `Files.walk` does not descend a symlinked
directory unless the caller passes `FOLLOW_LINKS`, so a symlinked tree risks the
compiler silently skipping whole folders. `os.link` is verified to work under the
macOS Dropbox File Provider, with project and staging tree on one device; a copy
is the fallback when a filesystem refuses.

**A payload we do not have is left missing — never touched into existence.**
Measured on 2026-07-27 against one tree in three variants: real payload bytes and
payloads truncated to *zero bytes* validate identically, while deleting one file
produces its `REFERENCE_NOT_FOUND`. The compiler checks that the referenced path
exists, not what is in it. An empty placeholder would therefore turn the gate
green and then import an application with broken images — the failure this whole
module exists to prevent. A genuine gap must reach the compiler and be reported.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Already gitignored by the project scaffolding, so nothing staged can be
# committed by accident.
STAGING_DIR = ("config", "temp", "apexlang")

# Where the compiler expects the payloads, relative to the app tree root.
PAYLOAD_DIR = ("shared-components", "static-files")

Linker = Callable[[Path, Path], None]


@dataclass(frozen=True)
class StagedTree:
    path           : Path
    metadata_files : int
    payload_files  : int
    copied         : bool = False


def staging_root_for(root: Path, apexlang_root: Path) -> Path:
    """One staging tree per app folder, named after it so runs stay inspectable."""
    return root.joinpath(*STAGING_DIR) / apexlang_root.parent.name


def stage_apexlang(
    apexlang_root : Path,
    files_root    : Path,
    staging_root  : Path,
    link          : Linker | None = None,
) -> StagedTree:
    """Mirror `apexlang/` plus its `files/` payloads into `staging_root`.

    Recreated per run, mirroring the `apexlang/` folder's own contract: a
    component deleted in App Builder must not survive as a stale `.apx` here
    either.
    """
    linker = link or os.link
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True)

    state = _LinkState(linker)
    metadata = _mirror(apexlang_root, staging_root, state)
    payloads = _mirror(files_root, staging_root.joinpath(*PAYLOAD_DIR), state)
    return StagedTree(staging_root, metadata, payloads, state.copied)


class _LinkState:
    """Carries the linker plus whether it ever had to fall back to copying."""

    def __init__(self, linker: Linker) -> None:
        self.linker = linker
        self.copied = False

    def place(self, source: Path, target: Path) -> None:
        try:
            self.linker(source, target)
        except OSError:
            # Cross-device, or a filesystem with no link support. Correctness
            # beats the disk saving: the bytes still have to arrive.
            shutil.copy2(source, target)
            self.copied = True


def _mirror(source_root: Path, target_root: Path, state: _LinkState) -> int:
    """Link every file under `source_root` into `target_root`, keeping the shape.

    A missing source root places nothing at all — no directory, no placeholder.
    That is deliberate: see the module docstring on why an empty stand-in is
    worse than an absent file.
    """
    if not source_root.is_dir():
        return 0
    placed = 0
    for source in sorted(source_root.rglob("*")):
        if not source.is_file():
            continue
        target = target_root / source.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        state.place(source, target)
        placed += 1
    return placed


__all__ = [name for name in globals() if not name.startswith("_")]
