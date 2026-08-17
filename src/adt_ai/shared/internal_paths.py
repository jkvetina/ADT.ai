"""Where ADT.ai keeps the data it writes about a project root.

``config/`` is for configuration a human edits, ``config.yaml``, ``IDENTITY.yaml``,
``STARTUP.sql``. Everything ADT.ai generates for its own use is data wearing a
config folder's name: the bare-``-recent`` watermark and the SQLite stores behind
``apex``, ``dependencies`` and ``flow``. Those live under ``config/internal/``
instead, so what a user is expected to edit and what the tool maintains are never
in the same listing.

Two sweeps run beside the relocation, and both are about the same thing: a name
this folder no longer needs does not get to sit there. `#369` converts the four
APEX YAML caches into ``apex.db`` and deletes them, and
:data:`SUPERSEDED_FILES` drops a legacy file whose replacement store is already
present.

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
#:
#: Empty since `#358` retired the only name it ever held, but the tuple and the
#: sweep stay: the folder itself must still go, and emptying the list without
#: keeping the `rmdir` would have left every root that ever ran the old `patch`
#: carrying the stray dot-folder `#319` existed to remove.
LEGACY_ROOT_FILES: tuple[str, ...] = ()

#: Generated files ADT.ai no longer writes and nothing can read: removed on the
#: next run rather than relocated, wherever the sweep finds them.
#:
#: `patch_commits.yaml` is the whole list. `patch` wrote it on every
#: commit-scanning run and a full `git grep` across `src/` found one writer and
#: no reader at all, so it recorded the window and answered nothing. `#358`
#: replaced it with the per-branch commit store, which `rebuild` and `patch`
#: share. Deleting is the honest treatment: it is data this tool generated for
#: itself, gitignored, that no version of ADT.ai can read again, and relocating
#: it would only move the confusion into `config/internal/`.
OBSOLETE_FILES: tuple[str, ...] = ("patch_commits.yaml",)

#: Generated files that belong under ``config/internal/``.
#:
#: ``db_dependencies.yaml`` is the odd one out: it is an **old ADT** artifact that
#: ADT.ai never writes (no reader or writer exists anywhere in ``src/``). It is
#: listed purely so the migration sweeps it out of ``config/`` alongside the rest
#: instead of leaving one legacy file behind to explain forever. Once the store
#: that replaced it is present, :data:`SUPERSEDED_FILES` deletes it outright.
#:
#: The three ``apex_*.yaml`` names and ``recent.yaml`` still relocate here even
#: though `#369` folded their contents into ``apex.db``. Relocation runs FIRST
#: and the conversion reads only ``config/internal/``, so a root that has not
#: been swept since `#316` still gets its caches converted rather than stranded
#: one folder up.
INTERNAL_FILES: tuple[str, ...] = (
    "apex_apps.yaml",
    "apex_developers.yaml",
    "apex_timers.yaml",
    "recent.yaml",
    "db_dependencies.yaml",
    "apex.db",
    "dependencies.db",
    "flow.db",
)

#: ``{replacement: superseded}`` - a generated file that becomes duplicate junk
#: the moment its successor exists, deleted wherever the sweep finds the pair.
#:
#: ``db_dependencies.yaml`` is old ADT's flattened dependency report. Nothing in
#: ADT.ai reads it, and it cannot be converted: the store is a raw mirror of the
#: data dictionary while the file is a ``LISTAGG`` of rows derived from one, so
#: there is no content to carry across, only a stale second answer to a question
#: ``dependencies.db`` already answers. Jan, 2026-08-15: *"it is a duplicate junk
#: at that point"*.
#:
#: The deletion is CONDITIONAL on the replacement being there. A project that
#: never ran ``dependencies -refresh`` has nothing but that file, and taking it
#: away would leave the root with less than it arrived with.
SUPERSEDED_FILES: dict[str, str] = {
    "dependencies.db": "db_dependencies.yaml",
}


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
    # A root swept before `#358` already has the dead cache under
    # `config/internal/`, so clear it there too rather than only at its
    # pre-`#319` address.
    _drop_obsolete(target_dir)
    # Not gated on `config/` existing: a root that only ever ran `patch` has a
    # `.adt-ai/` and no config folder at all.
    _sweep_legacy_root(root, target_dir, moved)
    # After relocation, never before: a pre-`#316` root keeps its caches under
    # `config/`, and converting reads only `config/internal/`.
    _convert_apex_caches(root, moved)
    # Last, because `dependencies.db` may itself have just been relocated: the
    # pair has to be judged at its final address, not its previous one.
    _drop_superseded(target_dir)
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
    _drop_obsolete(legacy_dir)
    with contextlib.suppress(OSError):
        legacy_dir.rmdir()


def _convert_apex_caches(root: Path | str, moved: list[str]) -> None:
    """Fold the APEX YAML caches into ``apex.db`` and drop what converted.

    Imported inside the function because `apex_store` reads its paths from this
    module, so a module-level import would close the cycle. The conversion owns
    its own failure posture (it returns an empty list rather than raising), and
    the names it consumed are reported here as moved so one run's console can
    say what happened to a file the user may still be looking for.
    """
    from adt_ai.shared.apex_store import migrate_apex_files

    moved.extend(migrate_apex_files(root))


def _drop_superseded(folder: Path) -> None:
    """Delete each :data:`SUPERSEDED_FILES` entry whose replacement is present.

    Runs on every command, not only the one that performs a conversion: a root
    swept by an earlier build carries the pair already, and a rule that only
    fires during a migration would never reach it. Jan asked for both halves,
    2026-08-15: removed *"during conversion to dependencies.db, but also later
    if it still exists along dependencies.db file"*.
    """
    for replacement, superseded in SUPERSEDED_FILES.items():
        if not (folder / replacement).is_file():
            continue
        candidate = folder / superseded
        if candidate.is_file():
            with contextlib.suppress(OSError):
                candidate.unlink()


def _drop_obsolete(folder: Path) -> None:
    """Delete :data:`OBSOLETE_FILES` from ``folder``, if any are there.

    Same posture as every other move here: only regular files, never raises. A
    directory wearing one of these names is not ours.
    """
    for name in OBSOLETE_FILES:
        candidate = folder / name
        if candidate.is_file():
            with contextlib.suppress(OSError):
                candidate.unlink()


__all__ = [
    "CONFIG_DIR",
    "GITIGNORE_ENTRY",
    "INTERNAL_DIR",
    "INTERNAL_FILES",
    "LEGACY_ROOT_DIR",
    "LEGACY_ROOT_FILES",
    "OBSOLETE_FILES",
    "SUPERSEDED_FILES",
    "config_dir",
    "ensure_internal_ignored",
    "internal_dir",
    "internal_path",
    "legacy_path",
    "migrate_internal_files",
]
