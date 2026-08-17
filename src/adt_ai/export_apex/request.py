from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from adt_ai.export_apex.filters import ApexComponentFilter, ApexPageSelection
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.progress import ApexProgressReporter
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.recent_state import recent_days


@dataclass(frozen=True)
class ApexExportRequest:
    root        : Path
    schemas     : list[str]
    applications: dict[str, list[ApexApplication]]
    actions     : Mapping[str, bool]
    config      : Mapping[str, object]
    # The formats the user named by their own flag, empty under `-all`.
    # `actions` says what to export; this says what was asked for *by name*,
    # which is what entitles a format to explain itself on the console (ADT
    # #235). Defaults to "nothing was named", so the quiet path is the default.
    explicit_actions: frozenset[str] = frozenset()
    release     : str | None = None
    # None (no recent filter), a day window (an int, or a float below a day), or
    # BARE_RECENT (since the last export of this app+format).
    recent      : int | float | object | None = None
    environment : str | None = None
    changed_by  : str | None = None
    my_changes  : bool = False
    my_name     : str | None = None
    my_email    : str | None = None
    recent_report_only: bool = False
    page_selection: ApexPageSelection | None = None
    component_filters: tuple[ApexComponentFilter, ...] = ()
    deep        : bool = False
    # The instance's APEX release, as probed by the connection block. Drives the
    # 26.1 format gates (`apexlang` needs it, `readable` is gone by then);
    # ``None`` means the probe missed and nothing is gated.
    apex_version: str | None = None
    # `-compact`: replace the per-application blocks and their action rows with
    # one bar for the whole schema segment. The per-action rows are old-ADT
    # parity output and stay the default, so the flag is the bar (`#376`, the
    # polarity `export_db -compact` settled in `#373`).
    compact     : bool = False
    reporter    : ApexProgressReporter | None = None
    # `timers_file` lived here until `#369`. The rolling ETA it pointed at is a
    # table in `config/internal/apex.db` now, resolved from `root` like every
    # other fact this run caches, so a caller had nothing left to override.
    apex_store  : ApexStore | None = None

    @property
    def recent_days(self) -> int | float | None:
        """The day window, or ``None`` for no filter and for watermark mode.

        A float when the window is shorter than a day (`-recent 1/24`).
        """
        return recent_days(self.recent)

    @property
    def narrowed(self) -> bool:
        """Whether the run exported a slice of the app rather than all of it.

        A narrowed export must never advance a watermark: stamping a one-author
        or single-page run would mark every component it skipped as current.
        """
        return any(
            (
                self.changed_by is not None,
                self.my_changes,
                self.page_selection is not None,
                bool(self.component_filters),
            )
        )
