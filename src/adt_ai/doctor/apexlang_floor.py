"""The SQLcl version an APEXlang project has to clear (ADT #723).

SQLcl 26.2.2 fixed the overwrite default and the static-file corruption, and
both of those fail QUIETLY: an older SQLcl leaves files the export meant to
replace sitting there at their previous content, and hands back static files
that are damaged rather than absent. Nothing downstream can tell -- the export
reports success, `validate` compiles what it was given, and the first honest
signal is a deployed application behaving like an older one.

That is why this is a failure of the setup rather than another `ACTIONS:` offer.
Everything else `doctor` reports is either working or visibly missing; this one
produces wrong bytes and says nothing, so the run exits non-zero and names both
numbers, the floor and what is installed.

Only a repo that already holds `apexlang/` exports is held to it. `doctor`
diagnoses machines long before a project exists, and a project that never
exports APEXlang has no reason to care which SQLcl it has. The verdict is local
throughout, so `-offline` reports it exactly as a plain run does.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.doctor._base import SQLCL_UPGRADE_ACTION, _version_key
from adt_ai.shared.apex_paths import apexlang_folders

#: The oldest SQLcl an APEXlang export may be taken with.
APEXLANG_SQLCL_FLOOR = "26.2.2"


def apexlang_sqlcl_shortfall(
    root     : Path | None,
    config   : Mapping[str, Any] | None,
    installed: str,
) -> str:
    """The installed SQLcl version when it is below the floor, else ``""``.

    Returning the version rather than a boolean is what lets the caller print
    both numbers without reading the environment a second time.

    Three ways to not be held to the floor, in the order they are cheapest to
    answer: no project root to look at, a version that does not compare (SQLcl
    missing, or a build string no release number can be read out of, where the
    `CURRENT VERSIONS:` row already says what is wrong), and a repo that exports
    no APEXlang. The filesystem walk is last on purpose: it is the only one of
    the three that touches disk.
    """
    if root is None:
        return ""
    version = str(installed or "").strip()
    key = _version_key(version)
    if not key or key >= _version_key(APEXLANG_SQLCL_FLOOR):
        return ""
    if not apexlang_folders(Path(root), config):
        return ""
    return version


def apexlang_floor_lines(installed: str) -> list[str]:
    """The `ACTIONS:` rows for a project sitting below the floor.

    The reason gets a row of its own because the remedy alone reads as one more
    upgrade nag, and this one is not optional: the versions between are not
    "older", they are wrong in a way the export cannot report.
    """
    return [
        f"  APEXlang exports need SQLcl {APEXLANG_SQLCL_FLOOR} or newer, this is {installed}.",
        f"  {APEXLANG_SQLCL_FLOOR} fixed the overwrite default and the static-file corruption.",
        SQLCL_UPGRADE_ACTION,
    ]


__all__ = ["APEXLANG_SQLCL_FLOOR", "apexlang_floor_lines", "apexlang_sqlcl_shortfall"]
