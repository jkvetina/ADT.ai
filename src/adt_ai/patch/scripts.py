"""Moving per-patch scripts into the patch folder, ADT #309 (was #300, #17).

`patch_template_dir` holds per-project config every patch reuses, so ADT #288
stopped copying it and links it where it lives. `patch_scripts_dir/<CODE>/` is the
opposite kind of folder: everything in it was written for exactly one patch code
and has no life after that patch ships. Jan settled it on 2026-08-13, *"Move
all, you have to commit the patch and patch result anyway"*, so the scripts
leave the source tree and land inside the patch, which becomes the whole record of
the change.

Three things happen on the way, and the first two only make sense together:

* **hardening** (`harden.py`) rewrites each statement into an existence-checked
  block. The move is the one moment the bytes are rewritten anyway, which is
  exactly where old ADT did it (`create_file_snapshot`, patch.py:1970-1972).
* **recovery** carries scripts forward on a re-create. The first `-create` empties
  the source folder, so a second one would otherwise ship a patch with no scripts
  at all, the loss Jan named in the same answer: *"they can be lost, so it would
  be great to preserve them on possible recreate."* The recovered copy is already
  hardened, which is why that transform is idempotent.
* **the commit filter** (old ADT `get_script_files`, patch.py:1811-1824) decides
  which scripts belong to THIS patch. The folder accumulates across a project's
  life; without it a patch injects every script anyone ever left there.

Nothing here reads a directory a second time to answer a question the writer
already knew, the manifest returned is the writer's own answer, the shape ADT
#18 and ADT #276 both arrived at.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from adt_ai.patch import settings
from adt_ai.patch.files import _patch_map, _patch_scripts_folder
from adt_ai.patch.generated_helpers import (
    ALTER_HELPER_SLOT,
    DROP_HELPER_SLOT,
    is_alter_helper_filename,
    is_drop_helper_filename,
)
from adt_ai.patch.harden import harden_patch_script
from adt_ai.patch.models import PatchScripts
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import CommitRecord

# Where the moved scripts live inside the patch, when the project says nothing.
# Old ADT's `patch_scripts_snap` (config.yaml:122) was this same folder name with
# the `<CODE>` level dropped -- the code is already in the patch folder's own
# name, so repeating it inside would nest a directory that answers a question
# nobody asked. ADT #430 carried the KEY back over on that reading: the `<CODE>`
# level is what was dropped, never the project's right to name the folder, so
# `settings.scripts_snap_folder` is the live answer and this stays as its default.
PATCH_SCRIPTS_FOLDER = "patch_scripts"


def reset_patch_scripts(
    root: Path,
    folder: Path,
    config: dict[str, Any],
    *,
    patch_code: str,
) -> None:
    """Empty the patch folder's own scripts folder, ADT #508. `-create -force`.

    Jan, 2026-08-24: *"If there is -create & -force used, you will delete
    existing patch_scripts tight to this patch at the start, so we make sure
    patch is clean."* Said after a `-create` naming one rename commit, which
    changed no object at all, wrote 194 `PROMPT -- SCRIPT:` lines into a live
    install script: `templates._script_payload` links every file in the slot and
    applies no commit filter, because by the time it reads the folder the filter
    has already had its say. So a folder carrying an earlier window's helpers
    ships them, and `#366` guaranteed in as many words that nothing would ever
    delete them.

    **Clearing the folder is not the same as destroying what is in it**, and the
    difference is the whole of this function. A generated helper is derived from
    the run's own commit window, so a copy of one answers an earlier window and
    this run writes its own: it goes. Anything else was written by a person, and
    since `#309` unlinks the original the moment the first `-create` moves it in,
    the copy in here is the only one there is: it goes back to the source folder,
    where `collect_patch_scripts` puts the ordinary commit filter to it again a
    few lines later. A script this window still owns is moved straight back in
    and nothing changes; one belonging to a commit outside the window stays in
    the source tree and is reported, which is what that filter is for.

    So `-force` means rebuild this patch from its sources under THIS window. The
    deploy logs `#366` protects sit outside this folder and are untouched.
    """
    if not config.get("patch_add_scripts", True):
        return
    destination = folder / settings.scripts_snap_folder(config)
    if not destination.is_dir():
        return
    source_root = _patch_scripts_folder(root, config, patch_code)
    for path in sorted(destination.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(destination)
        if _is_generated_helper(relative, config):
            continue
        target = source_root / relative
        # A script re-edited in the source folder wins, exactly as it wins over a
        # recovered copy in `collect_patch_scripts`: re-editing one is the reason
        # to re-create. Hardening is idempotent (`_SOURCE_HEADER`), so handing
        # back the hardened bytes costs the author nothing.
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    shutil.rmtree(destination)


def _is_generated_helper(relative: Path, config: dict[str, Any]) -> bool:
    """Is this patch-folder path one `patch/helpers.py` wrote? (ADT #508)

    Filename AND slot, never either alone. The filename shapes are the
    generator's (`drop.<type>.<name>.sql`, `<stem>.<number>.sql`,
    `<stem>.hash.sql`) and the slots are the two it writes into, so a
    hand-written `02.sql` in a `_before` slot is an author's file whatever its
    name looks like. Getting this wrong deletes the only copy of somebody's DDL,
    which is why it is the narrow test rather than the clever one.
    """
    if len(relative.parts) < 2:
        return False
    slot = relative.parts[0]
    if slot == DROP_HELPER_SLOT:
        return is_drop_helper_filename(relative.name, config)
    if slot == ALTER_HELPER_SLOT:
        return is_alter_helper_filename(relative.name)
    return False


def collect_patch_scripts(
    root: Path,
    folder: Path,
    config: dict[str, Any],
    *,
    patch_code: str,
    records: list[CommitRecord],
    generated: list[str],
) -> PatchScripts:
    """Recover, filter and move (in that order) returning what happened.

    Order matters. Recovery runs first so a fresh edit in the source folder
    overwrites the carried-forward copy rather than losing to it: re-creating
    after editing a script is the whole reason to re-create.
    """
    if not config.get("patch_add_scripts", True):
        return PatchScripts()
    snap = settings.scripts_snap_folder(config)
    destination = folder / snap
    recovered = _recover_previous_scripts(root, folder, destination, patch_code, config, snap)
    source_root = _patch_scripts_folder(root, config, patch_code)
    slots = _known_slots(config)
    result = PatchScripts(recovered=recovered)
    generated_paths = set(generated)
    committed = {path for record in records for path in record.usable_files}
    for source in sorted(source_root.glob("**/*.sql")):
        relative = source.relative_to(source_root)
        repo_path = source.relative_to(root).as_posix()
        if not relative.parts[:-1] or relative.parts[0] not in slots:
            # No slot, or a slot no group can produce: nothing would ever link it.
            result.unknown.append(repo_path)
            continue
        if repo_path not in generated_paths and repo_path not in committed:
            # A helper THIS run generated has no commit by construction and is
            # exempt, old ADT filtered those too (its drop loop wrote into the
            # same folder at patch.py:1350), so a freshly generated helper was
            # ignored by the very run that wrote it, which is the defect ADT #18
            # and ADT #287 were both about. Deliberate divergence.
            result.ignored.append(repo_path)
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        # `patch_harden` (ADT #431). Hardening rewrites every CREATE, ALTER and
        # DROP into an existence-checked block, which is what makes a re-deploy
        # safe; a project whose scripts carry their own guards ships them
        # verbatim instead. Off means the bytes move unchanged, never a partial
        # rewrite.
        payload = source.read_text(encoding="utf-8", errors="replace")
        if settings.harden_scripts(config):
            payload = harden_patch_script(payload, config, source=repo_path)
        text_files.write_text(target, payload)
        source.unlink()
        result.moved.append(repo_path)
    _prune_empty(source_root, root)
    return result


def _known_slots(config: dict[str, Any]) -> set[str]:
    """`<group><postfix>` for every `patch_map` group and both timings.

    These are exactly the folder names `_database_patch_payload` asks
    `_script_payload` for, plus the `objects` fallback `_database_patch_group`
    returns for an object type no group claims. A name outside this set is what
    old ADT reported as an UNKNOWN script.

    The postfixes come from `patch_postfix_before` / `patch_postfix_after` since
    ADT #430, through the same `settings.slot_name` the payload writer calls, so
    a renamed slot cannot be legal on one side and UNKNOWN on the other.
    """
    groups = {str(group) for group in _patch_map(config)} | {"objects"}
    return {
        settings.slot_name(group, timing, config)
        for group in groups
        for timing in ("before", "after")
    }


def _recover_previous_scripts(
    root: Path,
    folder: Path,
    destination: Path,
    patch_code: str,
    config: dict[str, Any],
    snap_folder: str = PATCH_SCRIPTS_FOLDER,
) -> list[str]:
    """Carry the previous patch folder's HAND-AUTHORED scripts into this one.

    A same-day re-create rewrites the SAME folder (`#266`/`#289`), so the scripts
    are already in `destination` and this finds nothing to do. A later re-create
    mints a new folder, and the newest earlier folder for this patch code is where
    the first run left them.

    **A generated DROP helper is regenerated, never carried (ADT #503.)** It is
    derived from this run's own patch window, so a copy taken from an earlier
    folder answers an earlier window and is stale by construction. `#498` stopped
    `_write_drop_helpers` writing a DROP for a group-moved object and `#499`
    stopped every `[DELETED]` listing naming one, and both were undone here: this
    function ran BEFORE generation and copied every `**/*.sql` with no filter, so
    the 61 helpers a pre-fix run left in `patch/260822-1-APP309/patch_scripts/`
    were reinstated into the next build of that code, into the slot the install
    script links. Measured 2026-08-24: 7 of 7 `drop.synonym.ut_*.sql` carried.

    The filter is not a second copy of `#499`'s identity check. `_write_drop_helpers`
    already owns whether an object earns a DROP; recovery stops second-guessing it
    and lets the generator answer, which is `#474`'s one-reader-per-rule shape. A
    helper the window still earns is written again by the same run and moved in
    over this copy, so nothing is lost.

    **Every generated helper, not only the DROPs.** The filter read
    `is_drop_helper_filename` alone while `patch/helpers.py` writes two families,
    so the ALTER helpers came forward untouched: a `-force` recreate reinstated
    the previous window's `<stem>.<number>.sql` and `<stem>.hash.sql` into the
    slot the install script links, and the generator, finding the file already
    there, left the stale copy in place (`#657`). `_is_generated_helper` is the
    one reader of "did we write this", slot and filename together, so recovery
    now asks it rather than half of it.
    """
    previous = _previous_scripts_folder(root, folder, patch_code, snap_folder)
    if previous is None:
        return []
    carried: list[str] = []
    for source in sorted(previous.glob("**/*.sql")):
        if _is_generated_helper(source.relative_to(previous), config):
            continue
        target = destination / source.relative_to(previous)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        carried.append(target.relative_to(folder).as_posix())
    return carried


def _previous_scripts_folder(
    root: Path,
    folder: Path,
    patch_code: str,
    snap_folder: str = PATCH_SCRIPTS_FOLDER,
) -> Path | None:
    """The newest earlier patch folder for this code that holds moved scripts.

    Matched on the folder name's own `<yymmdd>-<seq>-<CODE>` code segment rather
    than through `discover_patch_folders`, which parses every folder's install
    script, this needs one name comparison, not a full inventory read.
    """
    patch_root = folder.parent
    if not patch_root.is_dir():
        return None
    candidates = [
        candidate / snap_folder
        for candidate in sorted(patch_root.iterdir(), reverse=True)
        if candidate.is_dir()
        and candidate != folder
        and candidate.name.partition("-")[2].partition("-")[2] == patch_code
    ]
    return next((path for path in candidates if path.is_dir()), None)


def _prune_empty(source_root: Path, root: Path) -> None:
    """Drop the emptied `<CODE>/` folder and any slot folders under it.

    Git cannot track an empty directory, so one left behind is residue that shows
    up as an untracked nothing in every later `git status`. Stops at `root` and at
    the first non-empty directory, so a slot still holding an ignored or unknown
    script survives untouched.
    """
    for path in sorted(source_root.glob("**/"), key=lambda item: len(item.parts), reverse=True):
        # ``Path.glob('**/')`` includes ``source_root`` itself. Leave that one
        # to the explicit final check below so the cleanup rule has one owner.
        if path.is_dir() and path not in {root, source_root} and not any(path.iterdir()):
            path.rmdir()
    if source_root != root and source_root.is_dir() and not any(source_root.iterdir()):
        source_root.rmdir()

__all__ = [
    "ALTER_HELPER_SLOT",
    "Any",
    "CommitRecord",
    "DROP_HELPER_SLOT",
    "PATCH_SCRIPTS_FOLDER",
    "PatchScripts",
    "Path",
    "annotations",
    "collect_patch_scripts",
    "harden_patch_script",
    "is_alter_helper_filename",
    "is_drop_helper_filename",
    "reset_patch_scripts",
    "settings",
    "shutil",
    "text_files",
]
