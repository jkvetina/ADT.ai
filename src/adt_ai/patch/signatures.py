"""The objects a patch overwrites, and the guard it runs before overwriting them.

The harm this closes is one sentence of Jan's, 2026-09-02: *"I have a list of 10
objects to deploy, at the end we have object ABC. What if another developer come
in the mean time and change this ABC object? His changes will be lost."* A patch
is built at one moment and deployed at another, and between the two a colleague
compiles into the same shared DEV schema. Nothing in ADT saw that: `-create`'s
`WARNING - OBJECTS CHANGED:` (`patch/staleness.py`) compares the repo against the
dependency mirror at BUILD time and says nothing about the window after it.

**This is not `-hash` mode and does not touch it.** Jan: *"Dont confuse this with
the -hash mode!"* That mode selects WHICH files a patch carries; this is a
deploy-time safety check that rides whatever patch was built.

## What this module writes, and what it deliberately does not

One `NAME:TYPE` row per guarded object the patch overwrites, the moment the patch
was built, and the links to the shared scripts under
`<patch_template_dir>/locks/` that compare them (ADT #850,
`queries/signatures.py`). `-create` still opens no connection, and no hash is
computed here or in the patch: CORE_LOCKS owns source hashing, and
`core_lock.create_lock` runs that comparison on every lock it takes.

The lock scripts are owned by `patch_core_locks` and `patch_signatures`, not by
`patch_add_templates`: that switch turns off the project's own slot templates,
and turning it off must not quietly turn off the guard too.

## Which objects are guarded

Only what the database stores a source for and a patch OVERWRITES. Jan: *"objects
which are supported, tables for example are not"*. A TABLE reaches a target
through a generated `tables_after/` ALTER rather than a replace.

## And the two kinds that are not objects at all

`-rest` and `-files_ws` export artifacts belonging to a SCHEMA rather than to an
application, so neither `user_objects` nor `#592`'s application checksum covers
them (ADT #724). They get the same drift comparison over their own dictionaries,
keyed on `updated_on`, and no lock half. An APPLICATION static file is left out,
its application's checksum covering it already.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adt_ai.patch.layout import apex_head_for
from adt_ai.patch.models import PatchError
from adt_ai.patch.object_identity import _object_identity
from adt_ai.patch.queries.signatures import (
    BUILT_AT_BIND,
    BUILT_AT_BIND_TYPE,
    CHECK_OBJECTS,
    CHECK_OBJECTS_ALL,
    LIST_BIND_BYTES,
    LIST_BIND_TYPE,
    LIST_SEPARATOR,
    LOCK_HEADING,
    LOCK_OBJECTS,
    LOCKS_FOLDER,
    MISSING_LOCK_FILE,
    OBJECTS_BIND,
    UNLOCK_HEADING,
    UNLOCK_OBJECTS,
    WORKSPACE_GUARDS,
)
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.patch.templates import linked_file_rows
from adt_ai.shared.apex_paths import REST_SCHEMA_DEFINITION
from adt_ai.shared.commit_discovery import CommitRecord

# The types the dictionary stores a source for AND a patch overwrites in place.
SIGNED_TYPES = (
    "FUNCTION",
    "PACKAGE",
    "PACKAGE BODY",
    "PROCEDURE",
    "TRIGGER",
    "TYPE",
    "TYPE BODY",
    "VIEW",
)

# How Oracle reads the `:built_at` bind. It is a UTC instant and carries no
# offset, because the check converts the database's own reading to UTC first.
BUILT_AT_FORMAT = "%Y-%m-%d %H:%M:%S"

# There is deliberately no `signatures.log` sidecar. The install script carries
# every value inline, in the block that sets it, so a second file would only be a
# copy that can disagree; `tests/patch/test_install_script_parity` pins the patch
# folder's contents exactly and is right to.


@dataclass(frozen=True)
class PatchObject:
    """One object the patch overwrites, as the `:objects` bind names it.

    **The verdict itself lives in the SQL and nowhere else.** A Python twin of
    that rule would be a second reader of one rule, and one nothing runs: the
    comparison happens on the target, with no ADT present.
    """

    schema: str
    object_type: str
    object_name: str
    file: str


def collect_signatures(
    root: Path,
    files: list[str],
    config: dict[str, Any],
    *,
    present_files: Mapping[str, bool] | None = None,
) -> list[PatchObject]:
    """One row per guarded object the patch overwrites, read out of paths only.

    Identity comes from paths; source presence decides which paths the patch
    actually carries, so a locally absent committed file keeps its guard.
    """
    objects: list[PatchObject] = []
    for relative in sorted(files):
        identity = _object_identity(relative, config)
        if identity is None:
            continue
        schema, object_type, object_name = identity
        if object_type not in SIGNED_TYPES:
            continue
        present = (
            present_files[relative] if present_files is not None
            else (root / relative).is_file()
        )
        if not present:
            continue
        objects.append(
            PatchObject(
                schema      = schema,
                object_type = object_type,
                object_name = object_name,
                file        = relative,
            )
        )
    return objects


def built_at(records: list[CommitRecord]) -> str:
    """The moment the patch was built, as the drift checks compare against it.

    The NEWEST commit in the window rather than the oldest: everything this patch
    ships was committed by then, so an object the target compiled after it is a
    change the patch never saw.

    **In UTC, converted rather than truncated (ADT #700).** A commit stamp is the
    AUTHOR's instant with the AUTHOR's offset on it, and `strftime` does not
    convert such a value: a build committed at 10:00 +02:00 was written as 10:00
    when the instant it names is 08:00 UTC. The check resolves the database's own
    reading to UTC on the target, so this side answers in UTC too.

    A stamp carrying no offset is read on THIS host's clock, which is the only
    meaning it has here, and converting rather than skipping it is also what keeps
    `max()` from raising on a window mixing the two spellings (ADT #670).
    """
    stamps = [record.date for record in records if record.date]
    if not stamps:
        return datetime.now(UTC).strftime(BUILT_AT_FORMAT)
    newest = max(_utc(datetime.fromisoformat(stamp)) for stamp in stamps)
    return newest.strftime(BUILT_AT_FORMAT)


def _utc(moment: datetime) -> datetime:
    """One instant as a naive UTC reading, whichever spelling it arrived in."""
    return moment.astimezone(UTC).replace(tzinfo=None)


def _heading(title: str) -> list[str]:
    return ["", "PROMPT --;", f"PROMPT -- {title}", "PROMPT --;"]


def _listed(bind: str, items: list[str]) -> list[str]:
    """The items a list bind can carry, or a build-time refusal naming why not.

    A comma cannot be split back out of the list, and a list past the bind's
    width would be cut short on the target, guarding less than it says.
    """
    odd = [item for item in items if LIST_SEPARATOR in item]
    if odd:
        raise PatchError(
            f"cannot guard {odd[0]!r}: a name carrying a comma cannot be listed in :{bind}"
        )
    size = sum(len(item.encode("utf-8")) + len(LIST_SEPARATOR) for item in items)
    if size > LIST_BIND_BYTES:
        raise PatchError(
            f":{bind} would hold {size} bytes, over the {LIST_BIND_BYTES} a VARCHAR2 bind "
            "holds - split the patch into smaller ones"
        )
    return items


def _bind_block(title: str, bind: str, items: list[str], stamp: str | None) -> list[str]:
    """The heading, the `VARIABLE` declarations and the one block that sets them.

    One row per item, NAME first, each ending on the separator, so the last row
    is shaped like every other and only its `;` closes the statement.
    """
    declared = {bind: LIST_BIND_TYPE}
    if stamp is not None:
        declared[BUILT_AT_BIND] = BUILT_AT_BIND_TYPE
    width = max(len(name) for name in declared)
    rows = [
        f"        '{escape_literal(item)}{LIST_SEPARATOR}'" for item in _listed(bind, items)
    ]
    lines = [
        *_heading(title),
        *(f"VARIABLE {name.ljust(width)} {kind}" for name, kind in declared.items()),
        "BEGIN",
        f"    :{bind} :=",
        *(f"{row} ||" for row in rows[:-1]),
        f"{rows[-1]};",
    ]
    if stamp is not None:
        lines.append(f"    :{BUILT_AT_BIND} := '{stamp}';")
    return [*lines, "END;", "/"]


def _lock_link(root: Path, folder: Path, config: dict[str, Any], script: str) -> list[str]:
    """Link one shared lock script, or say which one the project does not have."""
    template_dir = str(config.get("patch_template_dir") or "config/patch_template")
    path = root / template_dir / LOCKS_FOLDER / script
    if not path.is_file():
        return ["", MISSING_LOCK_FILE.format(path=path.relative_to(root).as_posix())]
    return linked_file_rows(root, folder, path, config)


def lock_payload(
    root: Path,
    folder: Path,
    objects: list[PatchObject],
    config: dict[str, Any],
    *,
    records: list[CommitRecord] | None = None,
) -> list[str]:
    """The guard, at the top, before the first object is written.

    `patch_core_locks` links `lock_objects.sql`, `patch_signatures` links the
    `last_ddl_time` check and sets the `:built_at` it reads. Both off and there is
    no block at all.

    Which check is Jan's rule (2026-09-14): CORE_LOCKS guards a schema only where
    it is installed AND `patch_core_locks` is on, everywhere else the drift check
    does. So with locking on, `check_objects.sql` steps aside for a CORE_LOCKS
    schema; with it off, `check_objects_all.sql` checks every schema.
    """
    locking = bool(config.get("patch_core_locks", True))
    drifting = bool(config.get("patch_signatures", True))
    if not objects or not (locking or drifting):
        return []
    lines = _bind_block(
        LOCK_HEADING,
        OBJECTS_BIND,
        [f"{item.object_name}:{item.object_type}" for item in objects],
        built_at(records or []) if drifting else None,
    )
    if locking:
        lines.extend(_lock_link(root, folder, config, LOCK_OBJECTS))
    if drifting:
        lines.extend(
            _lock_link(root, folder, config, CHECK_OBJECTS if locking else CHECK_OBJECTS_ALL)
        )
    return lines


def unlock_payload(
    root: Path,
    folder: Path,
    objects: list[PatchObject],
    config: dict[str, Any],
) -> list[str]:
    """Its pair, at the bottom, reading the `:objects` the top of the file set."""
    if not objects or not config.get("patch_core_locks", True):
        return []
    return [*_heading(UNLOCK_HEADING), *_lock_link(root, folder, config, UNLOCK_OBJECTS)]


@dataclass(frozen=True)
class WorkspaceArtifact:
    """One schema-level export artifact the patch overwrites (ADT #724).

    ``kind`` is the export action that wrote it (`rest`, `files_ws`), which is
    also the key into `WORKSPACE_GUARDS`. ``name`` is what the TARGET calls the
    row, never upper-cased: `user_ords_modules.name` and `wwv_flow_files.filename`
    both store whatever was typed.
    """

    kind: str
    name: str
    file: str


def _workspace_identity(path: str, config: dict[str, Any]) -> tuple[str, str] | None:
    """``(kind, name)`` for a schema-level artifact, or None for anything else.

    Inverts the two writers in `export_apex/files.py`. Both remainders are read
    whole rather than by base name, because both names carry separators: the
    REST fixture publishes `adt_fixture/status`, two folders deep.
    """
    head = apex_head_for(path, config)
    if head is None:
        return None
    parts = Path(path).parts[len(head):]
    rest_root = _folder_parts(config.get("apex_path_rest"), "workspace/rest/")
    if _under(parts, rest_root):
        name = "/".join(parts[len(rest_root):])
        if not name.endswith(".sql"):
            return None
        name = name[: -len(".sql")]
        return ("rest", name) if name and name != REST_SCHEMA_DEFINITION else None
    files_root = (
        *_folder_parts(config.get("apex_workspace_dir"), "workspace/"),
        *_folder_parts(config.get("apex_path_files"), "files/"),
    )
    if _under(parts, files_root):
        name = "/".join(parts[len(files_root):])
        return ("files_ws", name) if name else None
    return None


def _folder_parts(configured: Any, default: str) -> tuple[str, ...]:
    return Path(str(configured or default).strip("/")).parts


def _under(parts: tuple[str, ...], root: tuple[str, ...]) -> bool:
    return len(parts) > len(root) and parts[: len(root)] == root


def collect_workspace_signatures(
    root: Path,
    files: list[str],
    config: dict[str, Any],
    *,
    present_files: Mapping[str, bool] | None = None,
) -> list[WorkspaceArtifact]:
    """One row per schema-level artifact the patch overwrites, read out of paths."""
    artifacts: list[WorkspaceArtifact] = []
    for relative in sorted(files):
        identity = _workspace_identity(relative, config)
        if identity is None:
            continue
        present = (
            present_files[relative] if present_files is not None
            else (root / relative).is_file()
        )
        if not present:
            continue
        artifacts.append(
            WorkspaceArtifact(kind=identity[0], name=identity[1], file=relative)
        )
    return artifacts


def workspace_lock_payload(
    root: Path,
    folder: Path,
    artifacts: list[WorkspaceArtifact],
    config: dict[str, Any],
    *,
    records: list[CommitRecord] | None = None,
) -> list[str]:
    """One block per artifact kind the patch carries, beside the object guard.

    `patch_signatures` owns it, being the same comparison over a different
    table; `patch_core_locks` is not read at all.
    """
    if not artifacts or not config.get("patch_signatures", True):
        return []
    stamp = built_at(records or [])
    lines: list[str] = []
    for kind, guard in WORKSPACE_GUARDS.items():
        names = [item.name for item in artifacts if item.kind == kind]
        if not names:
            continue
        lines.extend(_bind_block(guard["heading"], guard["bind"], names, stamp))
        lines.extend(_lock_link(root, folder, config, guard["script"]))
    return lines


__all__ = [name for name in globals() if not name.startswith("_")]
