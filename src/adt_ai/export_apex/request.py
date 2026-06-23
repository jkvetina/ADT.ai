from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from adt_ai.export_apex.filters import ApexComponentFilter, ApexPageSelection
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.progress import ApexProgressReporter


@dataclass(frozen=True)
class ApexExportRequest:
    root        : Path
    schemas     : list[str]
    applications: dict[str, list[ApexApplication]]
    actions     : Mapping[str, bool]
    config      : Mapping[str, object]
    release     : str | None = None
    recent_days : int | None = None
    changed_by  : str | None = None
    my_changes  : bool = False
    my_name     : str | None = None
    my_email    : str | None = None
    recent_report_only: bool = False
    page_selection: ApexPageSelection | None = None
    component_filters: tuple[ApexComponentFilter, ...] = ()
    deep        : bool = False
    reporter    : ApexProgressReporter | None = None
    timers_file : Path | None = None
