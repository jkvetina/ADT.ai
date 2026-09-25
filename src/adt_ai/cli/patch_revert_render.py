"""What `patch -deploy` says about putting an application back (`#963`).

Printed right under `ERROR - VERIFICATION FAILED:`, one section per application
that `-app` imported and whose scan then failed. The verification section above
stays header, table and log; what the run did about the failure is this one.
An application an install script landed has no backup and prints nothing here.

The four layouts are Jan's, 2026-09-25, word for word:

* the revert restored the application: `APP <id> REVERTED:`;
* it ran and failed: `ERROR - APP <id> NOT REVERTED:`, `REVERT FAILED`;
* there was nothing to restore: the same header, `NOTHING TO RESTORE`;
* `-continue` asked for no revert: `WARNING - APP <id> NOT REVERTED:`.

The BACKUP line names the file relative to the patch folder, and only when the
file is really on disk: a line naming a backup that does not exist sends the
reader after a way back there is not. LOG is the basename, the rule every log
row under `DEPLOYING PATCH:` follows.

Split out of `patch_deploy_render.py`, which sits at the context guard.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import print_adt_header
from adt_ai.patch.apex_backup import REVERT_FAILED, REVERT_RESTORED


def print_apex_reverts(
    scans   : Sequence[Any],
    backups : Mapping[int, Any],
    reverts : Sequence[Any],
) -> None:
    """One section per failed scan of an application this run kept a backup of.

    A failed scan with a backup and no revert is a run under `-continue`: the
    deploy reverts every such application otherwise, SKIPPED included.
    """
    by_app = {revert.app_id: revert for revert in reverts}
    for scan in scans:
        backup = backups.get(scan.app_id)
        if backup is None or not scan.failed:
            continue
        _print_one(scan.app_id, backup, by_app.get(scan.app_id))


def _print_one(app_id: int, backup: Any, revert: Any) -> None:
    kept = _kept(backup)
    log = Path(revert.log_path).name if revert is not None and revert.log_path else ""
    if revert is None:
        print_adt_header(f"WARNING - APP {app_id} NOT REVERTED:")
        print("  APP NOT REVERTED ON DEMAND, -continue USED")
        _print_backup(kept)
    elif revert.outcome == REVERT_RESTORED:
        print_adt_header(f"APP {app_id} REVERTED:")
        print(f"  RESTORED FROM BACKUP: {kept}")
        if log:
            print()
            print(f"  LOG: {log}")
    elif revert.outcome == REVERT_FAILED:
        print_adt_header(f"ERROR - APP {app_id} NOT REVERTED:")
        print("  BROKEN APP INSTALLED, REVERT FAILED")
        _print_backup(kept)
        if log:
            print(f"  LOG: {log}")
    else:
        print_adt_header(f"ERROR - APP {app_id} NOT REVERTED:")
        print("  BROKEN APP INSTALLED, NOTHING TO RESTORE")
        _print_backup(kept)
    print()


def _print_backup(kept: str) -> None:
    if kept:
        print(f"  BACKUP: {kept}")


def _kept(backup: Any) -> str:
    """`backup_<ENV>/f<id>.sql` when that file is on disk, else ``""``."""
    location = backup.location
    if location is None or not location.is_file():
        return ""
    return f"{location.parent.name}/{location.name}"


__all__ = [
    "print_apex_reverts",
]
