from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from adt_ai.export_apex.filters import ApexComponentFilter, ApexPageSelection
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.progress import ApexProgressReporter
from adt_ai.shared.recent_state import recent_days


@dataclass(frozen=True)
class ApexExportRequest:
    root        : Path
    schemas     : list[str]
    applications: dict[str, list[ApexApplication]]
    actions     : Mapping[str, bool]
    config      : Mapping[str, object]
    release     : str | None = None
    # None (no recent filter), an int day window, or BARE_RECENT (since the last
    # export of this app+format).
    recent      : int | object | None = None
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
    reporter    : ApexProgressReporter | None = None
    timers_file : Path | None = None

    @property
    def recent_days(self) -> int | None:
        """The N-day window, or ``None`` for no filter and for watermark mode."""
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
