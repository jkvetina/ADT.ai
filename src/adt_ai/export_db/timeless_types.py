"""Discovery for the two object types the data dictionary cannot date.

Split out of `inventory.py` by ADT #414, which pushed that file past the repo's
20 KB context guard, along the seam the card itself draws: every other type
answers "what changed since" with a `LAST_DDL_TIME` bind inside its own query,
while `JOB` and `MVIEW LOG` each need their own answer.

`MVIEW LOG` turns out to need almost nothing: its `LOG_TABLE` is an ordinary TABLE
in `user_objects`, so the window binds normally and the work here is just the
filter. `JOB` is the real subject: no dictionary column dates it, so the window is
answered by comparing a content signature against the last one exported.

These return plain object NAMES rather than `DatabaseObject` values on purpose.
`DatabaseObject` lives in `inventory.py`, which imports this module, so returning
it here would close an import cycle for no gain; the caller wraps them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from adt_ai.export_db.job_signatures import changed_jobs

Matcher = Callable[[str, str], bool]


def discover_job_names(
    rows: list[dict[str, Any]],
    matches: Matcher,
    windowed: bool,
    known: Mapping[str, str] | None,
) -> tuple[list[str], dict[str, str]]:
    """Pick the jobs to export and report every chosen job's fresh signature.

    The signature map covers every job the filters SELECTED, including the ones the
    window then skipped, because it describes the schema rather than this run's
    output: a baseline that only remembered what was exported would re-offer every
    unchanged job forever.

    `windowed` is the whole gate. A run carrying no `-recent` means the caller asked
    for the entire set, so the comparison never runs (Jan, 2026-08-20): answering an
    explicit `-type JOB` with a subset is the defect ADT #414 was filed on, wearing
    a new spelling.
    """
    selected = [
        row
        for row in rows
        if str(row.get("SCHEDULE_TYPE") or "").upper() != "IMMEDIATE"
        if matches("JOB", str(row["OBJECT_NAME"]))
    ]
    signatures = {
        str(row["OBJECT_NAME"]): str(row.get("SIGNATURE") or "") for row in selected
    }
    if not windowed or known is None:
        return list(signatures), signatures
    return changed_jobs(signatures, known), signatures


def discover_mview_log_names(
    rows: list[dict[str, Any]],
    matches: Matcher,
) -> list[str]:
    """The mview logs this run selected; the window was already applied in SQL."""
    return [
        str(row["OBJECT_NAME"])
        for row in rows
        if matches("MVIEW LOG", str(row["OBJECT_NAME"]))
    ]
