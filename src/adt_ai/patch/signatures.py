"""The objects a patch overwrites, and the guard it runs before overwriting them.

The harm this closes is one sentence of Jan's, 2026-09-02: *"I have a list of 10
objects to deploy, at the end we have object ABC. What if another developer come
in the mean time and change this ABC object? His changes will be lost."* A patch
is built at one moment and deployed at another, and between the two a colleague
compiles into the same shared DEV schema. Nothing in ADT saw that: `-create`'s
`WARNING - OBJECTS CHANGED:` (`patch/staleness.py`) compares the repo against the
dependency mirror at BUILD time and says nothing about the window after it.

**This is not `-hash` mode and does not touch it.** Jan: *"Dont confuse this with
the -hash mode!"* That mode selects WHICH files a patch carries by comparing the
working tree against `patch_hashes/baseline.<ENV>.log`, never asks a database, and
is opt-in per run. This is a deploy-time safety check that rides whatever patch
was built, however it was built.

## What this module records, and what it deliberately does not

One row per guarded object the patch overwrites (its type and its name), plus
the moment the patch was built. That is the whole payload of both generated
blocks, and `-create` still opens no connection.

**No hash is computed here and none is computed in the patch.** Jan, 2026-09-02:
*"If you have core_locks, you use that to calculate the hash. If you dont have the
core_locks, you calculate the hash from user_objects view or any other oracle
views so it does not cost much."* CORE_LOCKS already owns source hashing, down to
the normalization that makes two clients' compiles of one file agree, and
`core_lock.create_lock` runs that comparison on every lock it takes. A second
hash in the install script was 224 lines re-deriving an answer the database was
already able to give; the rule and the arithmetic now live in exactly one place
(`queries/signatures.py` §Where the hashing went).

## Which objects are guarded

Only what the database stores a source for and a patch OVERWRITES. Jan: *"objects
which are supported, tables for example are not"*. A TABLE reaches a target
through a generated `tables_after/` ALTER rather than a replace.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adt_ai.patch.object_identity import _object_identity
from adt_ai.patch.queries.signatures import (
    CLOCK_COLUMN,
    CORE_LOCKS_COLUMN,
    DRIFT_BRANCH,
    LOCK_BLOCK,
    LOCK_BRANCH,
    OBJECT_ROW,
    UNLOCK_BLOCK,
)
from adt_ai.patch.sql_literal import escape_literal
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

# How Oracle reads the timestamp the drift branch compares against. It is a UTC
# instant and carries no offset, because the branch converts the database's own
# reading to UTC before comparing (`queries/signatures.py` §The two clocks).
BUILT_AT_FORMAT = "%Y-%m-%d %H:%M:%S"

# There is deliberately no `signatures.log` sidecar. The install script carries
# every value inline, in the block that asserts it, so a second file would only be
# a copy that can disagree; `tests/patch/test_install_script_parity` pins the patch
# folder's contents exactly and is right to.


@dataclass(frozen=True)
class PatchObject:
    """One object the patch overwrites, as both generated blocks name it.

    **The verdict itself lives in the SQL and nowhere else.** A Python twin of
    that rule would be a second reader of one rule (`tests/contracts/shared_readers.txt`
    is the standing objection), and worse, one nothing runs: the comparison
    happens on the target, inside the block, with no ADT present.
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
    """The moment the patch was built, as the drift branch compares against it.

    The NEWEST commit in the window rather than the oldest: everything this patch
    ships was committed by then, so an object the target compiled after it is a
    change the patch never saw. Anchoring on the base commit instead would flag
    every object the deploying developer compiled while building the patch, which
    on a shared DEV schema is most of them.

    **In UTC, converted rather than truncated (ADT #700).** A commit stamp is
    `git log --format=%aI`, the AUTHOR's instant with the AUTHOR's offset on it
    (`shared/commit_store.authored_stamp`), and `strftime` does not convert such a
    value: it prints the digits and discards the offset, so a build committed at
    10:00 +02:00 was written into the patch as 10:00 when the instant it names is
    08:00 UTC. The drift branch resolves the database's own reading to UTC on the
    target, so this side answers in UTC too and the two are comparable at all;
    where a repository already commits at +00:00 the conversion is the identity
    and nothing about its patches moves.

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
    """One instant as a naive UTC reading, whichever spelling it arrived in.

    Naive goes through `astimezone()` with no argument, which reads it on this
    host's zone exactly as `dates._on_the_client_clock` does for the same case.
    """
    return moment.astimezone(UTC).replace(tzinfo=None)


def object_rows(objects: list[PatchObject]) -> str:
    """The `('TYPE', 'NAME')` rows of the cursor's own IN list.

    ``object_type`` and ``object_name`` are escaped before they land in the
    single-quoted literal: an object called e.g. `IT'S_PKG` would otherwise end
    that literal one character early and take the rest of the block with it
    (`#670`, the same defect `harden.py::_literal` closed for the hardening
    templates on `#554`).
    """
    return ",\n".join(
        OBJECT_ROW.format(
            object_type = escape_literal(item.object_type),
            object_name = escape_literal(item.object_name),
        )
        for item in objects
    )


def lock_payload(
    objects: list[PatchObject],
    config: dict[str, Any],
    *,
    records: list[CommitRecord] | None = None,
) -> list[str]:
    """The guard, at the top, before the first object is written.

    Two config keys, two branches, either of which can be off on its own:
    `patch_core_locks` owns the lock CORE_LOCKS takes (and the signature check it
    runs on the way), `patch_signatures` owns the `last_ddl_time` fallback for a
    schema that has no CORE_LOCKS to ask. Both off and there is no block at all.
    """
    locking = bool(config.get("patch_core_locks", True))
    drifting = bool(config.get("patch_signatures", True))
    if not objects or not (locking or drifting):
        return []
    branches = []
    if locking:
        branches.append(LOCK_BRANCH)
    if drifting:
        branches.append(
            DRIFT_BRANCH.format(
                keyword  = "ELSIF" if locking else "IF",
                built_at = built_at(records or []),
            )
        )
    block = LOCK_BLOCK.format(
        clock   = CLOCK_COLUMN if drifting else "",
        columns = CORE_LOCKS_COLUMN if locking else "",
        rows    = object_rows(objects),
        guard   = "\n            --\n".join(branches),
    )
    return ["", *block.splitlines()]


def unlock_payload(
    objects: list[PatchObject],
    config: dict[str, Any],
) -> list[str]:
    """Its pair, at the bottom, releasing exactly what the top of the file took."""
    if not objects or not config.get("patch_core_locks", True):
        return []
    block = UNLOCK_BLOCK.format(rows=object_rows(objects))
    return ["", *block.splitlines()]


__all__ = [name for name in globals() if not name.startswith("_")]
