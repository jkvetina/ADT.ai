"""The one zip-slip guard, shared by every zip extraction site (#670).

`doctor/upgrade.py::DoctorUpgradeMixin._extract_archive` (the downloaded SQLcl
release) and `shared/sqlcl_connect.py::_extract_wallet_zip` (a wallet archive)
each carried their own containment loop before this module existed, checking a
member's resolved destination against the extraction root before writing it --
the same guard, spelled twice, one drift away from a third spelling that
forgets it. One function now does the check and the extraction, so a crafted
``../`` (or absolute-path) member is refused identically at both call sites.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def safe_extractall(archive: zipfile.ZipFile, target: Path, *, what: str) -> None:
    """Extract every member of `archive` into `target`, refusing a zip-slip escape.

    Every member's resolved destination is checked against `target` BEFORE
    `ZipFile.extractall` writes anything: a member path built from `../` or an
    absolute path could otherwise land outside `target` (zip-slip,
    CVE-2007-4559-shaped). `what` names the archive kind for the caller (e.g.
    "SQLcl zip", "wallet zip"), so the error reads specifically rather than
    generically at every site that raises it.
    """
    resolved_target = target.resolve()
    for member in archive.infolist():
        member_path = (target / member.filename).resolve()
        if resolved_target != member_path and resolved_target not in member_path.parents:
            raise RuntimeError(f"Unsafe {what} entry: {member.filename}")
    archive.extractall(target)
