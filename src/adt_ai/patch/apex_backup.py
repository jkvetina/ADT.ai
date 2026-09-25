"""Keeping the application a `-app` deploy overwrites, and putting it back.

The gap this closes (ADT #727). `#676` gave the deploy a verdict on what it had
just landed and `#701` made that verdict honest, so a patch that imports an
application whose region queries no longer compile ends on `ERROR`. It ends
there with the broken application still installed: the import is a whole-app
replacement, the scan runs after it, and nothing between those two facts kept a
copy of what was there a minute earlier. The reader is told the target is broken
and handed no way back to the state that worked.

**The backup is one full SQL export, and the revert runs it** (ADT #963). Jan,
2026-09-25: *"FULL is faster and contains all we need, thats the best choice for
backup/restore"*. `APEX_EXPORT.GET_APPLICATION` with `p_split => FALSE` is
`export_apex -full`'s format, a single `f<id>.sql` that removes the application
and writes it again under its own id, workspace and component ids. The deploy
already installs that file shape with an `@` line, so the revert is that same
install -- the session directives, the configured `patch_file_link`, the same
transcript verdict -- and never a second install path. Static files ride inside
the file, so an application restored without its stylesheets cannot happen.

**Where it lives, and which one is kept.** `patch/<code>/backup_<ENV>/f<app>.sql`,
the patch's own folder rather than its logs, because it is the state the target
goes back to rather than a record of a run. Jan: *"take a backup of the app in
the first place, if we dont have any"*: a file already there is the state before
THIS patch first touched that environment, so it is kept and never overwritten,
and a second deploy of the same patch spends no export on it.

**Nothing here raises.** A backup that could not be taken and a revert that
could not run are both reported as an outcome carrying its reason, the rule
`apex_scan` and `apex_deploy.run_apex_imports` already follow beside it: a
safety net that throws would take the record of the deploy down with it, and the
deploy has already failed on its own terms by the time a revert is considered.

**A revert is RESTORED only when the application exports what the backup
holds.** A transcript ending on `SUCCESS` says the script ran, never which
application it left, which is the fail-open shape `#701` took out of the scan.
So the application is exported again after the revert, the same way, and the
two texts are compared by `export_signature`.

Nothing is masked in that comparison, and that is measured rather than hoped.
On SANDBOX, APEX 26.1.0, 2026-09-25 (`tests/tools/full_export_revert_probe`):
with `p_with_date => FALSE` two exports of one application with no import
between them are byte-identical, and so are the export taken before an
APEXlang import and the export taken after that first export was run back over
it -- no line differing, on two runs of 861 and 827 lines. The APEXlang import
in between moved from 6 to over 30 lines, among them `p_files_version` (the
`FILES_VERSION` that made APEX's own checksum useless here, `#727`),
`p_default_id_offset` and component ids; the SQL install writes every one of
them back from the file. The one volatile line an export can carry is its
`--   Date and Time:` comment, and the export is taken without it. APEX's
`CHECKSUM-SH256` is still read, for the drift gate, and still settles nothing
here.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.deploy import (
    _ERROR_CODE_RE,
    _deployment_error_excerpt,
    _deployment_succeeded,
)
from adt_ai.patch.files import _install_file_link
from adt_ai.patch.install_links import _file_link_rows
from adt_ai.shared import text_files
from adt_ai.shared.git_exclude import exclude_from_git, git_exclude_file

#: The backup was run back and the target exports the application it held
#: before the deploy. The only outcome that claims the target was restored.
REVERT_RESTORED = "RESTORED"
#: The revert was attempted and did not restore the target: the install failed,
#: the connection did, or the application afterwards is not the one from before.
REVERT_FAILED = "FAILED"
#: There was nothing to put back. A target holding no application before the
#: import is the ordinary case (a fresh sandbox id), and an export that could
#: not be taken is the other.
REVERT_SKIPPED = "SKIPPED"

#: What `export_signature` announces itself as, so no reader lines it up against
#: the `SH256:` rows APEX's own checksum writes into the same logs.
EXPORT_PREFIX = "EXPORT:"

#: The column every row in this block lines up on, shared with the signature
#: rows `apex_signature.signature_lines` writes into the same log so the two
#: read as one table rather than as two blocks that nearly agree.
_ROW_WIDTH = 16

#: What the `BACKUP` row says when the deploy kept nothing. Never blank: a row
#: with an empty value reads as a path the log failed to record, and the whole
#: point of the row is that a reader can tell "no copy exists" from "the copy is
#: over there".
_NO_BACKUP = "(nothing kept)"

#: What the `BACKUP` row adds when the file came from an earlier deploy of the
#: same patch, so a reader does not take it for a copy of what this run replaced.
_KEPT = "(kept from the first deploy of this patch)"


@dataclass(frozen=True)
class ApexBackup:
    """The copy of one target taken before the import that overwrote it."""

    app_id   : int
    schema   : str = ""
    #: The target's live APEX checksum, read before the import. It says whether
    #: an application was there at all, and it is empty when none was; it is not
    #: what the revert reproduces.
    checksum : str = ""
    #: `export_signature` of the file, which IS what the revert has to
    #: reproduce. Empty when nothing was kept.
    content  : str = ""
    #: The `f<app>.sql` file, or ``None`` when nothing was kept.
    path     : Path | None = None
    #: Where this application's backup lives or would live, set whether or not
    #: one was kept, so the console can name a file only when it is really there.
    location : Path | None = None
    #: True when the file was already there, from an earlier deploy of this
    #: patch to this environment, and was kept rather than taken now.
    reused   : bool = False
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
    #: `export_signature` of the target as it stands AFTER the revert, which is
    #: the value the `REVERTED` row carries. Empty when nothing was read.
    content  : str = ""
    reason   : str = ""
    log_path : str = ""

    @property
    def restored(self) -> bool:
        return self.outcome == REVERT_RESTORED


def export_signature(text: str) -> str:
    """A hash of one full export, exactly as APEX wrote it; ``""`` for no export.

    No line is masked: the module docstring carries the measurement that found
    nothing to mask. Not even line endings are folded, because the file is
    written and read back byte for byte and a carriage return inside a stored
    string is the application's own content.
    """
    if not text:
        return ""
    return f"{EXPORT_PREFIX}{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def back_up_application(
    gateway  : Any,
    app_id   : int,
    path     : Path,
    *,
    checksum : str,
    schema   : str = "",
) -> ApexBackup:
    """Keep the live ``app_id`` in ``path`` before anything writes over it.

    ``checksum`` is the target's signature, already read by `collect_signatures`
    for the drift gate; it is passed in rather than read again because the deploy
    has it and a second round trip could answer a different value.

    An empty ``checksum`` is the target holding no application yet, which is what
    a fresh sandbox id looks like. There is nothing to keep, and nothing a revert
    could put back either: removing an application this run created is a drop,
    which is `patch -drop`'s job and its ownership rail, not a side effect of a
    failed scan.

    A file already at ``path`` is kept as it is: it is the state before this
    patch first reached the environment, and exporting now would copy whatever
    that first deploy left.
    """
    base = ApexBackup(app_id=app_id, schema=schema, checksum=checksum, location=path)
    if not checksum:
        return replace(
            base,
            reason = "the target held no application before the import, so there "
            "is nothing to put back",
        )
    reused = path.is_file()
    try:
        # Bytes, never text mode: reading text folds a CRLF the export carries.
        text = (
            path.read_bytes().decode("utf-8")
            if reused
            else gateway.fetch_clob(queries.APEX_FULL_EXPORT_BLOCK, {"app_id": app_id})
        )
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        verb = "read" if reused else "taken"
        return replace(base, reason=f"the backup could not be {verb}: {error}")
    if not text.strip():
        return replace(
            base, reason=f"the export returned nothing, so there is nothing to run back ({path})",
        )
    if not reused:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Byte for byte: the file is the application, and the comparison
            # after a revert is against exactly these bytes.
            text_files.write_bytes(path, text.encode("utf-8"))
        except OSError as error:
            return replace(base, reason=f"the export could not be written: {error}")
        _exclude_from_git(path)
    return replace(base, content=export_signature(text), path=path, reused=reused)


def _exclude_from_git(path: Path) -> None:
    """Every patch's `backup_*/` out of the user's commits (Jan, 2026-09-25).

    One anchored pattern for the whole patch root, `/<patch root>/*/backup_*/`,
    through `.git/info/exclude`, the file `validate` already keeps its linked
    payloads out of git with: a backup is a full export of a target
    application, and the tracked `.gitignore` is the user's, not ADT's.
    """
    found = git_exclude_file(path)
    patches = path.parent.parent.parent
    # A repository rooted inside one patch folder has no patch root to anchor
    # on, and a guessed pattern could hide the user's own files.
    if found is None or not patches.is_relative_to(found[0]):
        return
    relative = patches.relative_to(found[0]).as_posix()
    exclude_from_git(
        path, f"{PurePosixPath('/', relative, '*', settings.APEX_BACKUP_GLOB)}/",
    )


def back_up_targets(
    items           : Sequence[Any],
    gateway_factory : Callable[[str], Any],
    *,
    folder          : Path,
    target_env      : str,
) -> dict[int, ApexBackup]:
    """One backup per application this run is about to import, keyed by target id.

    ``folder`` is the patch folder, and each backup lands in its
    `backup_<ENV>/f<app>.sql` under the id the tree lands ON: that is the
    application being overwritten, whichever one the tree came from.

    ``items`` are the prepared `apex_deploy.ApexImportItem`s, which already carry
    the live checksum in `signatures.on_target`: the drift gate read it off the
    target before a single install script ran, so the backup and the gate are
    judging the same state rather than two reads a deploy apart.
    """
    return {
        item.target_id: back_up_application(
            gateway_factory(item.schema),
            item.target_id,
            settings.apex_backup_path(folder, target_env, item.target_id),
            checksum = item.signatures.on_target,
            schema   = item.schema,
        )
        for item in items
    }


def restore_application(
    gateway : Any,
    backup  : ApexBackup,
    root    : Path,
    *,
    config  : dict[str, Any] | None = None,
) -> ApexRevert:
    """Run ``backup`` back onto the target and prove the target came back.

    The script is the one a deploy runs for a full `f<id>.sql` export: the
    session directives the deploy prepends, the file's `PROMPT -- FILE:` row
    and its `@` line through the configured `patch_file_link`, run from the
    backup's own folder so the link is the file's name. The export names its
    own application id and workspace, so nothing is passed to retarget it.

    The proof is the application exported again and compared with the file. An
    empty read-back never passes: an id that exports nothing is an application
    that is gone rather than one that is back.
    """
    if backup.path is None:
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_SKIPPED,
            reason  = backup.reason or "no copy of the target was kept before the import",
        )
    script = "\n".join([
        queries.SQLERROR_EXIT_ROLLBACK_DIRECTIVE,
        *queries.SESSION_DEFAULT_DIRECTIVES,
        *_file_link_rows(
            _named(backup.path, root), _install_file_link(backup.path.name, config or {}),
        ),
        "PROMPT SUCCESS",
    ])
    try:
        output = gateway.sqlcl_request(script, backup.path.parent)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, like a script
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            reason  = f"the backup's install did not complete: {error}",
        )
    if not _deployment_succeeded(output):
        # The deploy's own excerpt, narrowed to the line carrying the error code
        # when there is one; a transcript with none is read by its tail.
        excerpt = _deployment_error_excerpt(output) or [output.strip()]
        cause = next((line for line in excerpt if _ERROR_CODE_RE.search(line)), excerpt[-1])
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            reason  = "the backup's install reported an error, so the target is "
            f"whatever the failed deploy left: {cause.strip()}",
        )
    try:
        restored = export_signature(
            gateway.fetch_clob(queries.APEX_FULL_EXPORT_BLOCK, {"app_id": backup.app_id})
        )
    except Exception as error:  # noqa: BLE001 - an unread target is an unproven revert
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            reason  = f"the backup ran and the target could not be exported to check "
            f"it, so nothing is proven: {error}",
        )
    if not restored or restored != backup.content:
        return ApexRevert(
            app_id  = backup.app_id,
            outcome = REVERT_FAILED,
            content = restored,
            reason  = "the backup ran and the target does not export the application "
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
    no copy of those, because the backup is what `-app` buys and an install
    script is the patch's own SQL running against the target.

    An application whose scan passed is left exactly as the deploy landed it, and
    the backup stays on disk either way: a successful deploy still leaves the
    reader the state it replaced.
    """
    stamp = moment or datetime.now()
    reverts: list[ApexRevert] = []
    for report in reports:
        backup = backups.get(report.app_id)
        if backup is None or not report.failed:
            continue
        reverts.append(
            _written(
                restore_application(
                    gateway_for_app(report.app_id), backup, root, config=config,
                ),
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
        "-- The backup, a full SQL export of the application as it stood before this",
        "-- patch first reached the target, was run back over it. The deploy itself",
        "-- stays failed: reverting undoes the write, it does not make the patch correct.",
        "--",
        "-- PRE-DEPLOY and REVERTED are hashes of the application's full SQL export,",
        "-- the backup's and a fresh one taken after the revert, not APEX's own",
        "-- CHECKSUM-SH256. RESTORED means the two exports are identical.",
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


def _backup_value(backup: ApexBackup | None, root: Path) -> str:
    """The path a `BACKUP` row names, project-relative wherever it can be.

    Repo-relative when the file sits under the project root, which is every
    ordinary run, and absolute otherwise rather than guessed at with `..` -- the
    same rule `apex_deploy._source_line` applies to the row above it.
    """
    if backup is None or backup.path is None:
        reason = backup.reason if backup is not None else ""
        return f"{_NO_BACKUP} {reason}".strip()
    value = _named(backup.path, root)
    return f"{value} {_KEPT}" if backup.reused else value


def _named(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _row(name: str, value: str) -> str:
    return f"--   {name.ljust(_ROW_WIDTH)} | {value}"


__all__ = [
    "EXPORT_PREFIX",
    "REVERT_FAILED",
    "REVERT_RESTORED",
    "REVERT_SKIPPED",
    "ApexBackup",
    "ApexRevert",
    "back_up_application",
    "back_up_targets",
    "backup_line",
    "export_signature",
    "restore_application",
    "revert_failed_scans",
    "revert_log_text",
]
