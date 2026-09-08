"""Keeping the application a `-app` deploy overwrites, and putting it back.

The gap this closes (ADT #727). `#676` gave the deploy a verdict on what it had
just landed and `#701` made that verdict honest, so a patch that imports an
application whose region queries no longer compile now ends on `ERROR`. It ends
there with the broken application still installed: the import is a whole-app
replacement, the scan runs after it, and nothing between those two facts kept a
copy of what was there a minute earlier. The reader is told the target is broken
and handed no way back to the state that worked.

**The revert is another import, so the backup is an export in the import's own
format.** `apex import -input <tree>` is an id-based replacement, which is what
makes it reversible at all: run it a second time against the tree the target
held before, and the target is what it was. So the backup is an `APEXLANG`
export of the LIVE target, taken through the deploy's own connection, written
into `logs_<TARGET_ENV>/<timestamp>_apex_backup_<app>/`, and re-imported by the
same `build_import_script` the deploy uses. No second install path, no
`f<id>.sql` carrier, and the artifact on disk is exactly the thing that reads it
back.

**Static-file payloads are kept here and dropped by `export_apex`, and the
difference is not an inconsistency.** `-apexlang` drops them so `-files` stays
the repository's single static-file channel and the repo never holds two copies;
a backup is not a repository, it is one throwaway snapshot of a live
application, and an application restored without its images and stylesheets is
not the application that stood there before the import.

**Nothing here raises.** A backup that could not be taken and a revert that
could not run are both reported as an outcome carrying its reason, the rule
`apex_scan` and `apex_deploy.run_apex_imports` already follow beside it: a
safety net that throws would take the record of the deploy down with it, and the
deploy has already failed on its own terms by the time a revert is considered.

**A revert that ran and did not restore the target has FAILED, and what proves
it is content rather than APEX's checksum.** The backup tree is hashed as it is
written, the application is exported again after the revert import and hashed
the same way, and the two must agree.

`CHECKSUM-SH256` was the first shape here and it cannot answer this question.
Measured against APEX 26.1.0 on SANDBOX, 2026-09-07: an application reverted
from its own backup exported byte-identical to that backup, all 13 members, and
still read a different checksum; importing those same bytes twice more answered
two further values. The one application attribute that moved across such an
import, out of all 196 in `APEX_APPLICATIONS`, was `FILES_VERSION`, APEX's cache
token for `#APP_FILES#` URLs, which an import bumps by design. The checksum is
perfectly stable to READ (two reads with no import between them agree) and
unstable across an import of identical bytes, so equality there would have been
unreachable rather than strict, and every revert would have reported FAILED.

`apex_signature.tree_signature` is what answers it instead: the same hash, over
the same canonical form, that the deploy's own `DEPLOYING` row already carries.
Reporting a revert as done because the SQLcl transcript said `Import successful`
is the fail-open shape `#701` took out of the scan, and it is not put back here.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.apex_import import ApexTarget, build_import_script
from adt_ai.patch.apex_signature import tree_signature
from adt_ai.shared import text_files
from adt_ai.shared.row_values import row_value
from adt_ai.validate.report import parse_import_output

#: The backup was imported back and the target exports the application it held
#: before the deploy. The only outcome that claims the target was restored.
REVERT_RESTORED = "RESTORED"
#: The revert was attempted and did not restore the target: the import refused,
#: the connection did, or the application afterwards is not the one from before.
REVERT_FAILED = "FAILED"
#: There was nothing to put back. A target holding no application before the
#: import is the ordinary case (a fresh sandbox id), and an export that could
#: not be taken is the other.
REVERT_SKIPPED = "SKIPPED"

#: The column every row in this block lines up on, shared with the signature
#: rows `apex_signature.signature_lines` writes into the same log so the two
#: read as one table rather than as two blocks that nearly agree.
_ROW_WIDTH = 16

#: What the `BACKUP` row says when the deploy kept nothing. Never blank: a row
#: with an empty value reads as a path the log failed to record, and the whole
#: point of the row is that a reader can tell "no copy exists" from "the copy is
#: over there".
_NO_BACKUP = "(nothing kept)"


@dataclass(frozen=True)
class ApexBackup:
    """The copy of one target taken before the import that overwrote it."""

    app_id   : int
    schema   : str = ""
    #: The target's live APEX checksum, read before the import. It says whether
    #: an application was there at all, and it is empty when none was; it is not
    #: what the revert reproduces (see the module docstring on `FILES_VERSION`).
    checksum : str = ""
    #: A content hash of the tree that was kept, which IS what the revert has to
    #: reproduce. Empty when nothing was written.
    content  : str = ""
    #: Where the tree landed, or ``None`` when nothing was kept.
    path     : Path | None = None
    files    : int = 0
    #: Why there is nothing to put back, for every case where `path` is None.
    reason   : str = ""

    @property
    def restorable(self) -> bool:
        return self.path is not None


@dataclass(frozen=True)
class ApexRevert:
    """What putting one target back achieved, as the log and the run read it."""

    app_id   : int
    outcome  : str = REVERT_SKIPPED
    #: A content hash of the target as it stands AFTER the revert, which is the
    #: value the `REVERTED` row carries. Empty when no import ran.
    content  : str = ""
    reason   : str = ""
    log_path : str = ""

    @property
    def restored(self) -> bool:
        return self.outcome == REVERT_RESTORED


def back_up_application(
    gateway  : Any,
    app_id   : int,
    folder   : Path,
    *,
    checksum : str,
    schema   : str = "",
) -> ApexBackup:
    """Export the live ``app_id`` into ``folder``, before anything writes over it.

    ``checksum`` is the target's signature, already read by `collect_signatures`
    for the drift gate; it is passed in rather than read again because the deploy
    has it and a second round trip could answer a different value.

    An empty ``checksum`` is the target holding no application yet, which is what
    a fresh sandbox id looks like. There is nothing to keep, and nothing a revert
    could put back either: removing an application this run created is a drop,
    which is `patch -drop`'s job and its ownership rail, not a side effect of a
    failed scan.
    """
    if not checksum:
        return ApexBackup(
            app_id = app_id,
            schema = schema,
            reason = "the target held no application before the import, so there "
            "is nothing to put back",
        )
    try:
        written = _write_tree(gateway, app_id, folder)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return ApexBackup(
            app_id   = app_id,
            schema   = schema,
            checksum = checksum,
            reason   = f"the export did not complete, so no copy was kept: {error}",
        )
    if not written:
        return ApexBackup(
            app_id   = app_id,
            schema   = schema,
            checksum = checksum,
            reason   = "the export returned no file, so there is no tree to import back",
        )
    return ApexBackup(
        app_id   = app_id,
        schema   = schema,
        checksum = checksum,
        content  = tree_signature(folder),
        path     = folder,
        files    = written,
    )


def back_up_targets(
    items           : Sequence[Any],
    gateway_factory : Callable[[str], Any],
    *,
    log_folder      : Path,
    config          : dict[str, Any],
    moment          : datetime | None = None,
) -> dict[int, ApexBackup]:
    """One backup per application this run is about to import, keyed by target id.

    ``items`` are the prepared `apex_deploy.ApexImportItem`s, which already carry
    the live checksum in `signatures.on_target`: the drift gate read it off the
    target before a single install script ran, so the backup and the gate are
    judging the same state rather than two reads a deploy apart.
    """
    stamp = moment or datetime.now()
    backups: dict[int, ApexBackup] = {}
    for item in items:
        folder = log_folder / settings.apex_backup_folder_name(
            config, moment=stamp, app_id=item.target_id
        )
        backups[item.target_id] = back_up_application(
            gateway_factory(item.schema),
            item.target_id,
            folder,
            checksum = item.signatures.on_target,
            schema   = item.schema,
        )
    return backups


def restore_application(gateway: Any, backup: ApexBackup, root: Path) -> ApexRevert:
    """Import ``backup`` back onto its own id and prove the target came back.

    No `-id` and no `-alias`: the tree was exported FROM this application, so it
    already names it in `deployments/default.json`, and passing the id again
    would ask `build_import_script` for the alias rename a retarget needs and
    this is not one.

    The proof is the application exported again and hashed against the tree that
    was kept. An empty read-back never passes: an id that exports nothing is an
    application that is gone rather than one that is back.
    """
    if backup.path is None:
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_SKIPPED,
            reason  = backup.reason or "no copy of the target was kept before the import",
        )
    script = build_import_script(
        backup.path, ApexTarget(selected=True, target_id=None, full_app_ids=[])
    )
    try:
        output = gateway.sqlcl_request(script, root)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, like a script
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            reason  = f"the import of the backup did not run: {error}",
        )
    if parse_import_output(output).failed:
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            reason  = "the import of the backup reported an error, so the target is "
            "whatever the failed deploy left",
        )
    try:
        restored = _live_content(gateway, backup.app_id)
    except Exception as error:  # noqa: BLE001 - an unread target is an unproven revert
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            reason  = f"the import ran and the target could not be exported to check "
            f"it, so nothing is proven: {error}",
        )
    if not restored or restored != backup.content:
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            content = restored,
            reason  = "the import ran and the target does not export the application "
            f"it held before the deploy ({backup.content})",
        )
    return ApexRevert(
        app_id  = backup.app_id,
        outcome = REVERT_RESTORED,
        content = restored,
    )


def revert_failed_scans(
    reports         : Sequence[Any],
    backups         : dict[int, ApexBackup],
    gateway_for_app : Callable[[int], Any],
    *,
    root            : Path,
    log_folder      : Path,
    config          : dict[str, Any],
    moment          : datetime | None = None,
) -> list[ApexRevert]:
    """Put back every application this run imported and the scan then failed.

    Only the applications this run IMPORTED, which is what `backups` holds: the
    scan also covers applications a per-app install script landed, and there is
    no copy of those, because the tree-level backup is what `-app` buys and an
    install script is the patch's own SQL running against the target.

    An application whose scan passed is left exactly as the deploy landed it, and
    the backup folder stays on disk either way: a successful deploy still leaves
    the reader the state it replaced.
    """
    stamp = moment or datetime.now()
    reverts: list[ApexRevert] = []
    for report in reports:
        backup = backups.get(report.app_id)
        if backup is None or not report.failed:
            continue
        reverts.append(
            _written(
                restore_application(gateway_for_app(report.app_id), backup, root),
                backup     = backup,
                root       = root,
                log_folder = log_folder,
                config     = config,
                moment     = stamp,
            )
        )
    return reverts


def backup_line(backup: ApexBackup | None, root: Path) -> str:
    """The import log's `BACKUP` row: where the state it overwrote was kept.

    Written for every `-app` import while the key is on, including the runs that
    kept nothing, because a reader looking for the way back needs to be told
    there is not one rather than left to notice a missing row.
    """
    return _row("BACKUP", _backup_value(backup, root))


def revert_log_text(revert: ApexRevert, backup: ApexBackup, root: Path) -> str:
    """The revert's own report, in the block shape the deploy logs already use."""
    lines = [
        f"-- APEX application {revert.app_id} was put back after the post-deploy scan "
        "failed.",
        "-- The tree this patch imported has been replaced by the application as it",
        "-- stood before the import. The deploy itself stays failed: reverting undoes",
        "-- the write, it does not make the patch correct.",
        "--",
        "-- PRE-DEPLOY and REVERTED are content hashes of the application's APEXlang",
        "-- export, not APEX's own CHECKSUM-SH256. An import bumps FILES_VERSION and",
        "-- the checksum moves with it, so two imports of identical bytes answer two",
        "-- different checksums and equality there could never mean the target is back.",
        "",
        _row("APPLICATION", str(revert.app_id)),
        _row("STATUS", revert.outcome),
        _row("BACKUP", _backup_value(backup, root)),
        _row("PRE-DEPLOY", backup.content or "(no application)"),
        _row("REVERTED", revert.content or "(not read)"),
    ]
    if revert.reason:
        lines.append(f"{revert.outcome}: {revert.reason}")
    return "\n".join(lines) + "\n"


def _written(
    revert     : ApexRevert,
    *,
    backup     : ApexBackup,
    root       : Path,
    log_folder : Path,
    config     : dict[str, Any],
    moment     : datetime,
) -> ApexRevert:
    """Write the report beside the scan that asked for it, and record where.

    An unwritable folder never changes the outcome, the rule `apex_scan._written`
    already applies to its own log: whether the target came back is a fact about
    the target, and folding a failed write into it would report a restored
    application as a failed revert.
    """
    try:
        log_folder.mkdir(parents=True, exist_ok=True)
        path = log_folder / settings.apex_revert_log_name(
            config, moment=moment, app_id=revert.app_id
        )
        text_files.write_text(path, revert_log_text(revert, backup, root))
    except OSError as error:
        lost = f"the revert log could not be written: {error}"
        return ApexRevert(
            app_id  = revert.app_id,
            outcome = revert.outcome,
            content = revert.content,
            reason  = f"{revert.reason}; {lost}" if revert.reason else lost,
        )
    return ApexRevert(
        app_id   = revert.app_id,
        outcome  = revert.outcome,
        content  = revert.content,
        reason   = revert.reason,
        log_path = str(path),
    )


def _live_content(gateway: Any, app_id: int) -> str:
    """A content hash of ``app_id`` as it stands right now, exported to be read.

    The export is thrown away and only its hash is kept. A second folder beside
    the backup would be a copy of a state nobody asked to keep: the backup is the
    tree a reader can act on, and this one exists for the length of one
    comparison. It is taken with the same query the backup was, so the two hashes
    are answers to the same question rather than to two shapes of it.
    """
    with tempfile.TemporaryDirectory(prefix="adt_revert_") as folder:
        target = Path(folder)
        _write_tree(gateway, app_id, target)
        return tree_signature(target)


def _write_tree(gateway: Any, app_id: int, folder: Path) -> int:
    """Every member of one APEXLANG export, written under ``folder``.

    Text and bytes in one pass off one query. A member carrying a BLOB is a
    static-file payload and is written as bytes; everything else is the `.apx`
    and `.json` source and is written as text.
    """
    written = 0
    for row in gateway.fetch_all(queries.APEX_BACKUP_QUERY, {"app_id": app_id}):
        name = str(row_value(row, "NAME") or "").strip()
        if not name:
            continue
        target = _member_path(folder, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = row_value(row, "CONTENTS_BLOB")
        if payload is None:
            text_files.write_text(target, str(row_value(row, "CONTENTS") or ""))
        else:
            text_files.write_bytes(target, _blob_bytes(payload))
        written += 1
    return written


def _member_path(folder: Path, name: str) -> Path:
    """One member's file, refused when the name would climb out of the backup.

    APEXlang member names arrive relative and well formed, and this is the
    reader that must not trust that: the backup folder sits inside the patch,
    which sits inside the repository, so a `..` in a name APEX returned would
    write over a source file rather than over a backup member.
    """
    text = name.replace("\\", "/").strip("/")
    parts = [part for part in text.split("/") if part and part != "."]
    if any(part == ".." for part in parts):
        raise ValueError(f"backup member must stay under the backup folder: {name}")
    return folder.joinpath(*parts)


def _blob_bytes(value: Any) -> bytes:
    """A driver's BLOB, as bytes, whichever of the three shapes it hands back."""
    if isinstance(value, bytes):
        return value
    if hasattr(value, "read"):
        # A LOB handle, whose `read()` the driver types as `Any`.
        payload: bytes = value.read()
        return payload
    return bytes(value)


def _backup_value(backup: ApexBackup | None, root: Path) -> str:
    """The path a `BACKUP` row names, project-relative wherever it can be.

    Repo-relative when the folder sits under the project root, which is every
    ordinary run, and absolute otherwise rather than guessed at with `..` -- the
    same rule `apex_deploy._source_line` applies to the row above it.
    """
    if backup is None or backup.path is None:
        reason = backup.reason if backup is not None else ""
        return f"{_NO_BACKUP} {reason}".strip()
    try:
        return backup.path.relative_to(root).as_posix()
    except ValueError:
        return str(backup.path)


def _row(name: str, value: str) -> str:
    return f"--   {name.ljust(_ROW_WIDTH)} | {value}"


__all__ = [
    "REVERT_FAILED",
    "REVERT_RESTORED",
    "REVERT_SKIPPED",
    "ApexBackup",
    "ApexRevert",
    "back_up_application",
    "back_up_targets",
    "backup_line",
    "restore_application",
    "revert_failed_scans",
    "revert_log_text",
]
