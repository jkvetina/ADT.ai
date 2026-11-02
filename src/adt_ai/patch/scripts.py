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

import re
import shutil
from pathlib import Path
from typing import Any

from adt_ai.patch import settings
from adt_ai.patch.content import decode_repo_text
from adt_ai.patch.files import _patch_map, _patch_scripts_folder
from adt_ai.patch.generated_helpers import (
    alter_helper_slot,
    drop_helper_slot,
    is_alter_helper_filename,
    is_drop_helper_filename,
    legacy_alter_helper_slot,
)
from adt_ai.patch.harden import _SOURCE_HEADER, harden_patch_script
from adt_ai.patch.models import PatchScripts
from adt_ai.patch.user_alter_scripts import scan_user_scripts
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.safe_paths import under_root

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
    """Hand the patch folder's hand-authored scripts back to the source tree, ADT #508.

    The first half of `-create -force`; `clear_patch_scripts` is the second.
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

    The two halves run apart since ADT #1094: this one ahead of the generated
    scripts, whose claim scan reads the source copies, and the `rmtree` after
    them, beside `build._reset_generated_artifacts`. Together at the top of the
    build they ran ahead of the refusals, so a `-force` the codescan gate
    stopped had already emptied the folder and left the old install scripts
    `@`-linking files that were gone.
    """
    if not config.get("patch_add_scripts", True):
        return
    destination = _destination(folder, config)
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


def clear_patch_scripts(folder: Path, config: dict[str, Any]) -> None:
    """The `rmtree` half of `-create -force` (ADT #1094), run after generation.

    Everything hand-authored has already gone back to the source tree
    (`reset_patch_scripts`), so what is left here is this patch's previous
    build: generated helpers and the hardened copies. It is cleared past the
    one step that connects and past every gate, for the reason `#923` put
    `_reset_generated_artifacts` there: a refusal leaves the folder as it was.
    """
    if not config.get("patch_add_scripts", True):
        return
    destination = _destination(folder, config)
    if destination.is_dir():
        shutil.rmtree(destination)


def _destination(folder: Path, config: dict[str, Any]) -> Path:
    """`folder / patch_scripts_snap`, refused unless it resolves inside the patch folder.

    `settings.scripts_snap_folder` refuses `.`, `..` and `/` (ADT #1094); this
    is the symlink half, the same `under_root` `_patch_scripts_folder` applies,
    because `clear_patch_scripts` runs `rmtree` on the answer.
    """
    return under_root(
        folder, folder / settings.scripts_snap_folder(config), role="patch_scripts_snap"
    )


def _is_generated_helper(relative: Path, config: dict[str, Any]) -> bool:
    """Is this patch-folder path one `patch/helpers.py` wrote? (ADT #508)

    Filename AND slot, never either alone. The filename shapes are the
    generator's (`drop.<type>.<name>.sql`, `<stem>.<number>.sql`,
    `<stem>.hash.sql`) and the slots are the two it writes into, as this
    project's config names them (ADT #923), so a hand-written `02.sql` in a
    `_before` slot is an author's file whatever its name looks like. Getting
    this wrong deletes the only copy of somebody's DDL, which is why it is the
    narrow test rather than the clever one.
    """
    if len(relative.parts) < 2:
        return False
    slot = relative.parts[0]
    if slot == drop_helper_slot(config):
        return is_drop_helper_filename(relative.name, config)
    # The pre-#990 `after` slot too: a patch built before the move holds its
    # helpers there, and carried forward it would claim its table (ADT #990).
    if slot in (alter_helper_slot(config), legacy_alter_helper_slot(config)):
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
    destination = _destination(folder, config)
    recovered = _recover_previous_scripts(root, folder, destination, patch_code, config, snap)
    source_root = _patch_scripts_folder(root, config, patch_code)
    slots = _known_slots(config)
    result = PatchScripts(recovered=recovered)
    generated_paths = set(generated)
    committed = {path for record in records for path in record.usable_files}
    _drop_stale_helpers(destination, source_root, root, config, generated_paths)
    for source in sorted(source_root.glob("**/*.sql")):
        relative = source.relative_to(source_root)
        repo_path = source.relative_to(root).as_posix()
        if not in_linked_slot(relative, slots):
            # No slot, a slot no group can produce, or a folder below the slot:
            # nothing would ever link it.
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
        payload = decode_repo_text(source.read_bytes(), repo_path, config)
        if settings.harden_scripts(config):
            payload = harden_patch_script(payload, config, source=repo_path)
        text_files.write_text(target, payload)
        source.unlink()
        result.moved.append(repo_path)
    _prune_empty(source_root, root)
    return result


def in_linked_slot(relative: Path, slots: set[str]) -> bool:
    """Would `templates._script_payload` link a script at this slot-relative path? (ADT #1094)

    Exactly two parts: a slot the install script opens, then the filename. The
    linker globs `<slot>/*.sql` one level deep; the mover accepted any depth
    whose first part was a slot, so `tables_before/rename/01.sql` was hardened,
    moved and unlinked from the source tree, then linked by nothing, with the
    deploy green and the only copy in an unlinked folder. UNKNOWN is the honest
    report, the one old ADT gave a script in a slot no group produces. The claim
    scan (`helpers._shippable_user_scripts`) reads this same predicate, so a
    script that will not ship cannot claim a table either.
    """
    return len(relative.parts) == 2 and relative.parts[0] in slots


def _drop_stale_helpers(
    destination: Path,
    source_root: Path,
    root: Path,
    config: dict[str, Any],
    generated: set[str],
) -> None:
    """Delete the destination's generated helpers THIS run did not write (ADT #1094).

    Only `-force` cleared the folder, and the linker takes every `*.sql` in a
    slot, so a re-create under a narrower window without it kept a stale
    `drop.view.v_old.sql` linked and `DROP VIEW V_OLD` ran. A helper is derived
    from the run's own window and this run writes its own, so one it did not
    write answers an earlier window: compared by the repo path the generator
    reports, a helper regenerated under the same name stays until the move
    overwrites it. A hand-authored file is never touched (`_is_generated_helper`).
    """
    if not destination.is_dir():
        return
    for path in sorted(destination.rglob("*.sql")):
        relative = path.relative_to(destination)
        if not path.is_file() or not _is_generated_helper(relative, config):
            continue
        if (source_root / relative).relative_to(root).as_posix() in generated:
            continue
        path.unlink()


def carried_user_scripts(
    root: Path,
    folder: Path,
    config: dict[str, Any],
    *,
    patch_code: str,
) -> list[tuple[str, str]]:
    """Hand-authored scripts the patch folder holds, or recovery is about to carry in (ADT #1094).

    `(text, repo_path)` pairs like `scan_user_scripts`, for the claim scan in
    `helpers._write_generated_patch_scripts`. That scan read the SOURCE folder
    alone, and the first `-create` moves every script into the patch and unlinks
    the original, so a re-create without `-force` (allowed while the folder has
    no deploy log) found the source empty, asked Oracle, and wrote a `DROP
    COLUMN a` + `ADD b` helper that links ahead of the author's `t_rename.sql`:
    the data in `a` was gone.

    Two reads, in the order the move itself gives them: the destination first,
    then the newest earlier folder for this code, whose scripts
    `_recover_previous_scripts` copies in only where the destination has none.
    A path the source folder holds is left to the source scan, which wins the
    move too; a generated helper is this run's to rewrite and claims nothing; a
    path the linker would not link cannot claim what it will not ship.

    A moved copy is HARDENED, so its `ALTER TABLE` sits inside the quoted
    literal an `EXECUTE IMMEDIATE` runs, and the claim scan blanks literals (a
    mention is not an ALTER). ADT's own output is read back as the code it is:
    the statements inside those literals are appended to the scanned text.
    """
    snap = settings.scripts_snap_folder(config)
    source_root = _patch_scripts_folder(root, config, patch_code)
    slots = _known_slots(config)
    seen: set[Path] = set()
    carried: list[tuple[str, str]] = []
    bases = (_destination(folder, config), _previous_scripts_folder(root, folder, patch_code, snap))
    for base in bases:
        if base is None:
            continue
        for text, repo_path in scan_user_scripts(base, root):
            relative = (root / repo_path).relative_to(base)
            if relative in seen:
                continue
            seen.add(relative)
            if (
                (source_root / relative).exists()
                or _is_generated_helper(relative, config)
                or not in_linked_slot(relative, slots)
            ):
                continue
            carried.append((f"{text}\n{_hardened_statements(root / repo_path)}", repo_path))
    return carried


#: The one shape every `queries/hardening.py` template runs its statement in.
_HARDENED_STATEMENT_RE = re.compile(r"EXECUTE\s+IMMEDIATE\s*'((?:[^']|'')*)'", re.IGNORECASE)


def _hardened_statements(path: Path) -> str:
    """The statements a hardened copy runs, read back out of its literals (ADT #1094).

    Only for a file `harden_patch_script` wrote, recognised by its own header:
    a person's dynamic SQL in a verbatim script is left to the claim scan's
    reading of it.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    if not raw.lstrip().startswith(_SOURCE_HEADER):
        return ""
    return "\n".join(
        match.group(1).replace("''", "'") for match in _HARDENED_STATEMENT_RE.finditer(raw)
    )


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
    "Any",
    "CommitRecord",
    "PATCH_SCRIPTS_FOLDER",
    "PatchScripts",
    "Path",
    "alter_helper_slot",
    "annotations",
    "carried_user_scripts",
    "clear_patch_scripts",
    "collect_patch_scripts",
    "drop_helper_slot",
    "in_linked_slot",
    "harden_patch_script",
    "is_alter_helper_filename",
    "is_drop_helper_filename",
    "reset_patch_scripts",
    "settings",
    "shutil",
    "text_files",
]
