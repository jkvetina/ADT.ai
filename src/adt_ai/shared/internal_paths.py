"""Where ADT.ai keeps the data it writes about a project root.

``config/`` is for configuration a human edits, ``config.yaml``, ``IDENTITY.yaml``,
``STARTUP.sql``. Everything ADT.ai generates for its own use is data wearing a
config folder's name: the APEX metadata caches ``export_apex`` refreshes, the
bare-``-recent`` watermark, and the SQLite stores behind ``dependencies`` and
``flow``. Those live under ``config/internal/`` instead, so what a user is
expected to edit and what the tool maintains are never in the same listing.

Only **files** move. The generated folders beside them, ``config/commits/``,
``config/discovery/``, ``config/flow/``, ``config/temp/``, keep their documented
locations (Jan, 2026-08-13: "I dont want to move existing folders as subfolders
in internal/"); ``config/commits/`` in particular is the default value of the
published ``repo_commits_file`` key, so relocating it would change a setting
projects already carry.

:func:`migrate_internal_files` is the relocation, and it runs from the one CLI
entry point on **every** module rather than being wired per command. That makes
it invisible in the normal case: an already-migrated root does no filesystem
work beyond a handful of ``exists()`` calls. Three properties keep it safe to
run that often and that early, before the command banner, where nothing may
print:

* **It never overwrites.** A name present in both places is left in both; the
  legacy copy stays on disk rather than being deleted behind the user's back.
* **It never raises.** A root ADT.ai cannot write (a read-only checkout, a
  permission problem) is left exactly as found and the command proceeds.
* **It only moves regular files.** A directory that happens to carry one of
  these names is not a stale artifact of ours to relocate.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from adt_ai.shared import text_files

CONFIG_DIR = "config"
INTERNAL_DIR = "internal"
GITIGNORE_ENTRY = "config/internal/"

#: The hidden folder `patch` used to write straight into its `-root`.
LEGACY_ROOT_DIR = ".adt-ai"

#: Generated files that lived in :data:`LEGACY_ROOT_DIR`.
#:
#: `patch` composed `root / ".adt-ai" / "patch_commits.yaml"` itself and created
#: it on every commit-scanning run. `-root` defaults to `"."` and `adt`/`adtai`
#: are installed globally, so the folder landed in whatever directory the
#: operator happened to be standing in, untracked, undocumented, and outside
#: every convention this module exists to hold. Sweeping it here reclaims the
#: roots that already have one (`#319`).
LEGACY_ROOT_FILES: tuple[str, ...] = ("patch_commits.yaml",)

#: Generated files that belong under ``config/internal/``.
#:
#: ``db_dependencies.yaml`` is the odd one out: it is an **old ADT** artifact that
#: ADT.ai never writes (no reader or writer exists anywhere in ``src/``). It is
#: listed purely so the migration sweeps it out of ``config/`` alongside the rest
#: instead of leaving one legacy file behind to explain forever.
INTERNAL_FILES: tuple[str, ...] = (
    "apex_apps.yaml",
    "apex_developers.yaml",
    "apex_timers.yaml",
    "recent.yaml",
    "db_dependencies.yaml",
    "dependencies.db",
    "flow.db",
)


def config_dir(root: Path | str) -> Path:
    """The project's configuration folder."""
    return Path(root) / CONFIG_DIR


def internal_dir(root: Path | str) -> Path:
    """The folder holding the data ADT.ai maintains for ``root``."""
    return config_dir(root) / INTERNAL_DIR


def internal_path(root: Path | str, name: str) -> Path:
    """Location of one generated file for ``root``.

    The single accessor every reader and writer goes through, so the layout
    cannot drift back to a literal ``root / "config" / name`` at one call site
    while the rest have moved on.
    """
    return internal_dir(root) / name


def legacy_path(root: Path | str, name: str) -> Path:
    """Where ``name`` used to live, straight under ``config/``."""
    return config_dir(root) / name


def migrate_internal_files(root: Path | str) -> list[str]:
    """Relocate stragglers from ``config/`` into ``config/internal/``.

    Returns the names actually moved, in :data:`INTERNAL_FILES` order, empty on
    an already-migrated root, a root with no ``config/`` folder, and any root the
    process cannot write. Never raises, never prints, never overwrites.
    """
    moved: list[str] = []
    target_dir = internal_dir(root)
    source_dir = config_dir(root)
    if source_dir.is_dir():
        for name in INTERNAL_FILES:
            # is_file() also answers the directory case: a folder carrying one
            # of these names is not ours to move.
            _relocate(source_dir / name, target_dir / name, target_dir, name, moved)
    # Not gated on `config/` existing: a root that only ever ran `patch` has a
    # `.adt-ai/` and no config folder at all.
    _sweep_legacy_root(root, target_dir, moved)
    # Not `if moved`: a root swept by an earlier build has an internal folder and
    # no ignore entry, and would never move anything again to earn one. Keying on
    # the folder's existence makes the guarantee self-healing instead of
    # depending on which version happened to do the move.
    if target_dir.is_dir():
        ensure_internal_ignored(Path(root))
    return moved


def ensure_internal_ignored(root: Path) -> None:
    """Idempotently ensure ``config/internal/`` is git-ignored in ``root``.

    Mirrors ``ensure_discovery_ignored`` for ``config/discovery/``, and exists for
    the reason the first live sweep exposed: a project scaffolded before `#316`
    carries an older copy of the shipped ``.gitignore``, so relocating the files
    turned a folder git had never seen into an untracked entry, a 273 MB SQLite
    store one `git add .` away from a commit. Only called when something actually
    moved, so an already-migrated root writes nothing.
    """
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if GITIGNORE_ENTRY in {line.strip() for line in existing.splitlines()}:
        return
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    try:
        text_files.write_text(gitignore, prefix + GITIGNORE_ENTRY + "\n")
    except OSError:
        # Same posture as the move itself: housekeeping never fails a command.
        return


def _relocate(source: Path, target: Path, target_dir: Path, name: str, moved: list[str]) -> None:
    """Move one generated file, honouring the three safety properties."""
    if not source.is_file() or target.exists():
        return
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
    except OSError:
        return
    moved.append(name)


def _sweep_legacy_root(root: Path | str, target_dir: Path, moved: list[str]) -> None:
    """Reclaim :data:`LEGACY_ROOT_DIR` and drop it once it holds nothing of ours.

    The folder is removed with ``rmdir``, which succeeds only when it is empty,
    so a root where something else also wrote under that name keeps every file
    it did not get from us, and the deletion can never take content ADT.ai does
    not own.
    """
    legacy_dir = Path(root) / LEGACY_ROOT_DIR
    if not legacy_dir.is_dir():
        return
    for name in LEGACY_ROOT_FILES:
        _relocate(legacy_dir / name, target_dir / name, target_dir, name, moved)
    with contextlib.suppress(OSError):
        legacy_dir.rmdir()


__all__ = [
    "CONFIG_DIR",
    "GITIGNORE_ENTRY",
    "INTERNAL_DIR",
    "INTERNAL_FILES",
    "LEGACY_ROOT_DIR",
    "LEGACY_ROOT_FILES",
    "config_dir",
    "ensure_internal_ignored",
    "internal_dir",
    "internal_path",
    "legacy_path",
    "migrate_internal_files",
]
