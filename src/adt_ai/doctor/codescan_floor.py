"""The SQLcl version a project gating on codescan has to clear (ADT #1026, #1024).

The same shape as the APEXlang floor beside it (`apexlang_floor.py`): a failure
of the setup rather than an upgrade offer, both numbers named, a non-zero exit.
26.3 is the release `codescan` was measured on, and every fact the parser
relies on, the exit code that never moves, the empty report for a clean tree
and the 0-based positions, is a fact about that release.

**Held only where it bites.** A project is held to it once its
`config/internal/codescan.db` shows it has actually run codescan, and only
while `codescan_fail_on` makes the scan a gate, or as soon as `patch_codescan`
is `warn` or `block`, since every build then runs codescan itself. `doctor`
diagnoses machines long before a project exists, and a project that never ran
codescan, or reads it as a report only, has no reason to hear about a floor.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.doctor._base import SQLCL_UPGRADE_ACTION, _version_key
from adt_ai.shared.codescan_settings import (
    CODESCAN_STORE,
    FAIL_ON_NONE,
    PATCH_CODESCAN_OFF,
    CodescanSettingError,
    codescan_fail_on,
    patch_codescan,
)
from adt_ai.shared.internal_paths import internal_path

#: The oldest SQLcl a codescan gate may run on.
CODESCAN_SQLCL_FLOOR = "26.3"


def codescan_sqlcl_shortfall(
    root     : Path | None,
    config   : Mapping[str, Any] | None,
    installed: str,
) -> str:
    """The installed SQLcl version when a codescan gate sits below the floor, else ``""``.

    A `codescan_fail_on` this cannot read is left to `validate -codescan`, which
    refuses it with its own screen; saying so here as well would be a second,
    worse telling of the same thing.
    """
    if root is None:
        return ""
    version = str(installed or "").strip()
    key = _version_key(version)
    if not key or key >= _version_key(CODESCAN_SQLCL_FLOOR):
        return ""
    if _patch_gates(config):
        return version
    if not internal_path(Path(root), CODESCAN_STORE).is_file():
        return ""
    try:
        if codescan_fail_on(config) == FAIL_ON_NONE:
            return ""
    except CodescanSettingError:
        return ""
    return version


def _patch_gates(config: Mapping[str, Any] | None) -> bool:
    """Whether `patch -create` runs codescan itself (ADT #1024).

    Under `warn` or `block` every build starts SQLcl's codescan, whether or not
    `validate -codescan` ever ran, so the floor binds from the setting alone.
    A value this cannot read is `patch`'s to refuse, as above.
    """
    try:
        return patch_codescan(config) != PATCH_CODESCAN_OFF
    except CodescanSettingError:
        return False


def codescan_floor_lines(installed: str) -> list[str]:
    """The `ACTIONS:` rows for a codescan gate sitting below the floor.

    Named for codescan rather than one command, since `validate -codescan` and
    the `patch_codescan` gate each hold a project to it.
    """
    return [
        f"  codescan needs SQLcl {CODESCAN_SQLCL_FLOOR} or newer, this is {installed}.",
        SQLCL_UPGRADE_ACTION,
    ]


__all__ = ["CODESCAN_SQLCL_FLOOR", "codescan_floor_lines", "codescan_sqlcl_shortfall"]
