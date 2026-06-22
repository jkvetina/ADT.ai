"""Console rendering helpers for recompile reports.

The report row formatting and the streaming materialized-view console reporter
live here so a single source of truth feeds both the batch render
(``print_adt_table``) and the live streamed render — they cannot drift. Mirrors
the ``discovery/render.py`` split.
"""

from __future__ import annotations

from adt_ai.export_db.runner import (
    _commit_stdout,
    _compute_adt_layout,
    print_adt_header,
    print_adt_table,
)
from adt_ai.recompile.queries import mview_type_code
from adt_ai.recompile.runner import RecompileReporter


def _mview_status(mview) -> str:
    """Combine staleness and compile state into one STATUS cell, e.g. 'FRESH / VALID'."""
    return f"{mview.staleness or ''} / {mview.compile_state or ''}"


def _format_mview_timer(seconds: int | None) -> str:
    """Render Oracle's recorded refresh duration as a rounded-up TIMER cell.

    The dictionary times a refresh at one-second granularity (the difference of two
    DATE columns), so a genuinely sub-second refresh records an honest 0. Rather than
    the comparator style (``<1s``) Jan dislikes, round any real measurement *up* to a
    bare ``Ns``: a sub-second refresh reads as ``1s`` (never ``0``, never ``<``/``>``),
    and an N-second refresh as ``Ns``. Blank (``""``) is reserved for a NULL timer: a
    materialized view that has never been refreshed.
    """
    if seconds is None:
        return ""
    return f"{max(1, seconds)}s"


_MVIEW_COLUMNS = ("OBJECT_NAME", "STATUS", "TYPE", "LOG", "LAST_REFRESHED_AT", "TIMER")
_SYNONYM_COLUMNS = ("SYNONYM_NAME", "OBJECT_NAME", "TYPE", "PRIV", "GRNT", "VALID")
_DISABLED_COLUMNS = ("OBJECT_NAME", "TABLE_NAME")
_DISABLED_SECTION_TYPES = (
    ("CONSTRAINT", "DISABLED CONSTRAINTS:"),
    ("INDEX", "DISABLED INDEXES:"),
    ("TRIGGER", "DISABLED TRIGGERS:"),
)
_JOBS_COLUMNS = ("JOB_NAME", "LAST_START_DATE", "DURAT", "CPU")
_JOB_STATUS_ORDER = ("FAILED", "SUCCEEDED")


def _mview_row_cells(mview) -> dict[str, object]:
    """One materialized-view row as an ordered column→cell mapping.

    TYPE resolves the configured refresh_method to a clean F/C (FORCE picks F vs C
    by whether a usable MV log exists); LOG flags that log; LAST_REFRESHED_AT is the
    dictionary timestamp; TIMER is Oracle's own recorded refresh duration, rounded
    up to a bare ``Ns``. The single source of truth for both the batch render and
    the streamed render, so they cannot drift.
    """
    return {
        "OBJECT_NAME":       mview.object_name,
        "STATUS":            _mview_status(mview),
        "TYPE":              mview_type_code(mview.refresh_method, mview.has_log),
        "LOG":               "Y" if mview.has_log else "",
        "LAST_REFRESHED_AT": mview.last_refreshed_at or "",
        "TIMER":             _format_mview_timer(mview.last_timer),
    }


def _mview_row_values(mview) -> list[object]:
    """The MV row as a positional list in ``_MVIEW_COLUMNS`` order."""
    cells = _mview_row_cells(mview)
    return [cells[column] for column in _MVIEW_COLUMNS]


def _locked_row_cells(lock) -> dict[str, object]:
    """One locked-object row as an ordered column→cell mapping."""
    return {
        "OBJECT_TYPE": lock.object_type,
        "OBJECT_NAME": lock.object_name,
        "SID":         lock.session_id if lock.session_id is not None else "",
        "SERIAL":      lock.serial if lock.serial is not None else "",
        "ORACLE_USER": lock.oracle_user or "",
        "OS_USER":     lock.os_user or "",
        "MACHINE":     lock.machine or "",
        "PROGRAM":     lock.program or "",
        "LOCK_MODE":   lock.lock_mode or "",
    }


def _disabled_row_cells(item) -> dict[str, object]:
    """One disabled-object row as an ordered column→cell mapping."""
    return {
        "OBJECT_NAME": item.object_name,
        "TABLE_NAME":  item.table_name or "",
    }


def _disabled_type(item) -> str:
    return (item.object_type or "").upper()


def print_disabled_tables(disabled_objects) -> None:
    """Render -disabled as one compact table per disabled object type."""
    for object_type, heading in _DISABLED_SECTION_TYPES:
        print_adt_header(heading)
        print_adt_table(
            [
                _disabled_row_cells(item)
                for item in disabled_objects
                if _disabled_type(item) == object_type
            ],
            columns=list(_DISABLED_COLUMNS),
        )


def _job_status(item) -> str:
    return (item.status or "UNKNOWN").upper()


def _format_job_duration(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).split(".", 1)[0]


def _job_row_cells(item) -> dict[str, object]:
    """One scheduler-job run row as an ordered column→cell mapping."""
    return {
        "JOB_NAME":        item.job_name,
        "LAST_START_DATE": item.last_start_date or "",
        "DURAT":           _format_job_duration(item.run_duration),
        "CPU":             _format_job_duration(item.cpu_used),
    }


def print_job_tables(jobs) -> None:
    """Render -jobs as one compact table per scheduler status."""
    extra_statuses = sorted({_job_status(job) for job in jobs} - set(_JOB_STATUS_ORDER))
    for status in [*_JOB_STATUS_ORDER, *extra_statuses]:
        print_adt_header(f"SCHEDULER JOBS - {status}:")
        print_adt_table(
            [_job_row_cells(job) for job in jobs if _job_status(job) == status],
            columns=list(_JOBS_COLUMNS),
        )


def _synonym_owner(synonym) -> str:
    return synonym.owner or "UNKNOWN"


def _synonym_status(synonym) -> str:
    return synonym.status or "UNKNOWN"


def _synonym_privileges(synonym) -> list[str]:
    if not synonym.privileges:
        return [""]
    privileges = [privilege.strip() for privilege in synonym.privileges.split(",")]
    return [privilege for privilege in privileges if privilege] or [""]


def _synonym_sort_key(synonym) -> tuple[str, str, str, str, str]:
    return (
        _synonym_owner(synonym),
        _synonym_status(synonym),
        synonym.synonym_name,
        synonym.object_name or "",
        synonym.object_type or "",
    )


def _synonym_row_cells(synonym, privilege: str) -> dict[str, object]:
    return {
        "SYNONYM_NAME": synonym.synonym_name,
        "OBJECT_NAME":  synonym.object_name or "",
        "TYPE":         synonym.object_type or "",
        "PRIV":         privilege,
        "GRNT":         "Y" if synonym.is_grantable else "",
        "VALID":        "Y" if _synonym_status(synonym) == "VALID" else "",
    }


def print_synonym_tables(synonyms) -> None:
    """Render -synonyms as one compact table per target owner."""
    sorted_synonyms = sorted(synonyms, key=_synonym_sort_key)
    if not sorted_synonyms:
        print_adt_header("SYNONYMS")
        print_adt_table([], columns=list(_SYNONYM_COLUMNS))
        return

    for owner in sorted({_synonym_owner(synonym) for synonym in sorted_synonyms}):
        owner_synonyms = [
            synonym for synonym in sorted_synonyms if _synonym_owner(synonym) == owner
        ]
        print_adt_header(f"SYNONYMS TO SCHEMA: {owner}")
        print_adt_table(
            [
                _synonym_row_cells(synonym, privilege)
                for synonym in owner_synonyms
                for privilege in _synonym_privileges(synonym)
            ],
            columns=list(_SYNONYM_COLUMNS),
        )


class _ConsoleMViewReporter(RecompileReporter):
    """Streams the MATERIALIZED VIEWS table so each refresh's visible hang attaches
    to the view being worked on instead of the connection block above the table.

    ``begin_mviews`` measures the table from the pre-action snapshot and prints the
    header; ``begin_mview`` prints just the OBJECT_NAME cell (no newline) so the
    refresh visibly runs against that view; ``end_mview`` completes the same line
    with the re-read STATUS / TYPE / LOG / date / TIMER. The pre-action widths are a
    safe upper bound — OBJECT_NAME is stable, STATUS only shrinks (STALE→FRESH),
    LAST_REFRESHED_AT is header-dominated, and TIMER is the final column — so the
    streamed rows stay byte-aligned with the batch render.

    ``streamed`` lets the CLI fall back to the batch render when the runner never
    drove the reporter (the CLI test fakes, every non-mview run), so existing
    output is unchanged.
    """

    def __init__(self) -> None:
        self.streamed = False
        self._layout = None

    def locked(self, locked) -> None:
        if locked:
            print_adt_header("LOCKED OBJECTS")
            print_adt_table([_locked_row_cells(lock) for lock in locked])

    def begin_mviews(self, mviews) -> None:
        self.streamed = True
        rows = [_mview_row_cells(mview) for mview in mviews]
        self._layout = _compute_adt_layout(rows, list(_MVIEW_COLUMNS), {})
        # Mirror print_adt_table's opening exactly: header_adt prints the section
        # title, then a leading blank, the column header, and the separator.
        print_adt_header("MATERIALIZED VIEWS")
        print()
        print(self._layout.header_line())
        print(self._layout.separator_line())
        _commit_stdout()

    def begin_mview(self, mview) -> None:
        values = _mview_row_values(mview)
        print(self._layout.cells_segment(values, 0, 1), end="", flush=True)

    def end_mview(self, mview) -> None:
        values = _mview_row_values(mview)
        print(self._layout.cells_segment(values, 1, len(_MVIEW_COLUMNS)), flush=True)

    def end_mviews(self, mview_actions) -> None:
        # close the table with the trailing blank print_adt_table emits, then list
        # any failed refresh/compile below it (keyed by name, like the batch render).
        print()
        _commit_stdout()
        failed_actions = [a for a in mview_actions if not a.ok and a.error]
        for action in failed_actions:
            print(f"  {action.object_name}) {action.error}")
        if failed_actions:
            print()
