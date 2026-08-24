"""What one `export_db` run was asked for.

Split out of `runner.py` when that module reached the 20 KB context guard
(`tests/contracts/test_context_file_size.py`), the same call `#269` made for
`cli.py` and `#273` for `patch_deploy_render.py`: a module that crosses the
guard is split, never registered as debt. `runner` re-exports it, so every
existing import path is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_db.groups import GroupRules
from adt_ai.export_db.render import ExportDbReporter
from adt_ai.shared.recent_state import recent_days


@dataclass(frozen=True)
class ExportDbRequest:
    root         : Path
    schemas      : list[str]
    config       : dict[str, Any]
    schema_export: dict[str, dict[str, Any]] | None = None
    object_types : list[str] | None = None
    names        : list[str] | None = None
    prefix       : str | None = None
    ignore       : list[str] | None = None
    # None (full export), an int day window, or BARE_RECENT (since last export).
    recent       : int | float | object | None = None
    environment  : str | None = None
    clean        : bool = False
    reporter     : ExportDbReporter | None = None
    group_rules  : GroupRules | None = None
    changed_by   : str | None = None
    my_changes   : bool = False
    authors      : list[str] | None = None
    #: `-baseline` (`#452`): hash each object at the path it would have been
    #: written to instead of writing it. Why, and the promises that go with it:
    #: `cli/export_db_baseline.py`.
    baseline     : bool = False

    @property
    def recent_days(self) -> int | float | None:
        """The day window, or ``None`` for a full export and for watermark mode.

        A float when the window is shorter than a day (`-recent 1/24`).
        """
        return recent_days(self.recent)
