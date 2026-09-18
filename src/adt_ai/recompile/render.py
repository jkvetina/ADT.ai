"""Console rendering helpers for recompile reports.

The report row formatting and the streaming materialized-view console reporter
live here so a single source of truth feeds both the batch render
(``print_adt_table``) and the live streamed render, they cannot drift. Mirrors
the ``discovery/render.py`` split.
"""

from __future__ import annotations

from adt_ai.recompile.contracts import MViewAction, RecompileRequest, TrailingAction
from adt_ai.recompile.inventory import (
    DisabledObject,
    MaterializedView,
    SchedulerJobRun,
    SynonymInfo,
    TrailingObject,
)
from adt_ai.recompile.queries import mview_type_code
from adt_ai.recompile.runner import RecompileReporter
from adt_ai.recompile.vpd import VpdAssignment, VpdFunction, VpdReport
from adt_ai.shared.object_list import (
    ObjectRowFormatter,
    print_listing_gap,
    type_separator,
)
from adt_ai.shared.progress import print_adt_header, schema_label
from adt_ai.shared.tables import (
    _AdtTableLayout,
    _commit_stdout,
    _compute_adt_layout,
    print_adt_table,
)


def _mview_status(mview: MaterializedView) -> str:
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
_VPD_OPENING = "VPD FUNCTIONS:"
_SYNONYMS_OPENING = "SYNONYMS:"
_TRAILING_OPENING = "UPDATED OBJECTS:"


def opening_header(request: RecompileRequest) -> str:
    """The first header of the report-only run `RecompileRunner.run` will take.

    The CLI prints it BEFORE the run, so the read sits under the name of the
    report instead of under the connection block's closing blank (ADT #887,
    the `#372` move for `OBJECTS OVERVIEW:`). The checks follow the runner's own
    precedence, so a request naming two reports opens the one that runs.
    `-mviews` answers "": its reporter's `reading_mviews()` already does this.

    Jan, 2026-09-18, on the two whose header was built from the rows they read:
    *"Should be same everywhere."* So `-synonyms` opens on `SYNONYMS:` with its
    per-schema blocks below, and `-trailing` on `UPDATED OBJECTS:`, which can
    no longer carry a count it has not read yet.
    """
    if request.synonyms:
        return _SYNONYMS_OPENING
    if request.disabled:
        return _DISABLED_SECTION_TYPES[0][1]
    if request.jobs:
        return f"SCHEDULER JOBS - {_JOB_STATUS_ORDER[0]}:"
    if request.vpd:
        return _VPD_OPENING
    if request.trailing:
        return _TRAILING_OPENING
    return ""


def _mview_row_cells(mview: MaterializedView) -> dict[str, object]:
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


def _mview_row_values(mview: MaterializedView) -> list[object]:
    """The MV row as a positional list in ``_MVIEW_COLUMNS`` order."""
    cells = _mview_row_cells(mview)
    return [cells[column] for column in _MVIEW_COLUMNS]


def _disabled_row_cells(item: DisabledObject) -> dict[str, object]:
    """One disabled-object row as an ordered column→cell mapping."""
    return {
        "OBJECT_NAME": item.object_name,
        "TABLE_NAME":  item.table_name or "",
    }


def _disabled_type(item: DisabledObject) -> str:
    return (item.object_type or "").upper()


def print_disabled_tables(disabled_objects: list[DisabledObject], *, opening: str = "") -> None:
    """Render -disabled as one compact table per disabled object type.

    `opening` is the header the CLI already printed before the read, never twice.
    """
    for object_type, heading in _DISABLED_SECTION_TYPES:
        if heading != opening:
            print_adt_header(heading)
        print_adt_table(
            [
                _disabled_row_cells(item)
                for item in disabled_objects
                if _disabled_type(item) == object_type
            ],
            columns=list(_DISABLED_COLUMNS),
        )


def _job_status(item: SchedulerJobRun) -> str:
    return (item.status or "UNKNOWN").upper()


def _format_job_duration(value: object | None) -> str:
    if value is None:
        return ""
    return str(value).split(".", 1)[0]


def _job_row_cells(item: SchedulerJobRun) -> dict[str, object]:
    """One scheduler-job run row as an ordered column→cell mapping."""
    return {
        "JOB_NAME":        item.job_name,
        "LAST_START_DATE": item.last_start_date or "",
        "DURAT":           _format_job_duration(item.run_duration),
        "CPU":             _format_job_duration(item.cpu_used),
    }


def print_job_tables(jobs: list[SchedulerJobRun], *, opening: str = "") -> None:
    """Render -jobs as one compact table per scheduler status.

    `opening` is the header the CLI already printed before the read, never twice.
    """
    extra_statuses = sorted({_job_status(job) for job in jobs} - set(_JOB_STATUS_ORDER))
    for status in [*_JOB_STATUS_ORDER, *extra_statuses]:
        if f"SCHEDULER JOBS - {status}:" != opening:
            print_adt_header(f"SCHEDULER JOBS - {status}:")
        print_adt_table(
            [_job_row_cells(job) for job in jobs if _job_status(job) == status],
            columns=list(_JOBS_COLUMNS),
        )


# One overview block per policy function, then one block per function listing
# the tables it protects. DYNAMIC is the policy's type, so it sits with the
# function (ADT #881). Jan, 2026-09-18 (#887): one column name per row, the
# order FUNCTION | COLUMNS | DYNAMIC | TABLES, and a name that would push a row
# past 80 cut with "..." rather than `~`.
_VPD_WIDTH = 80
_VPD_FUNCTION_COLUMNS = ("FUNCTION", "COLUMNS", "DYNAMIC", "TABLES")
_VPD_POLICY_COLUMNS = ("TABLE_NAME", "POLICY_NAME", "SEL", "DML")
_VPD_COVERAGE_COLUMNS = ("TABLES", "WITH_POLICY", "WITHOUT_POLICY")


def _clip(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def _vpd_function_rows(item: VpdFunction) -> list[dict[str, object]]:
    """The function's own row carrying its first column, then one row per column."""
    columns = item.columns() or [""]
    return [
        {
            "FUNCTION": item.function_name if index == 0 else "",
            "COLUMNS":  column,
            "DYNAMIC":  item.dyn() if index == 0 else "",
            "TABLES":   len(item.assignments) if index == 0 else "",
        }
        for index, column in enumerate(columns)
    ]


def _fit_widths(first: int, second: int, budget: int) -> tuple[int, int]:
    """Share ``budget`` between two name columns, the shorter one kept whole."""
    if first + second <= budget:
        return first, second
    half = budget // 2
    if first <= half:
        return first, budget - first
    if second <= half:
        return budget - second, second
    return budget - half, half


def _fixed_width(columns: tuple[str, ...], flexible: tuple[str, str]) -> int:
    """Indent, every gutter, and the header width of every non-name column."""
    gutters = 3 * (len(columns) - 1)
    return 2 + gutters + sum(len(name) for name in columns if name not in flexible)


def _fitted_rows(
    rows: list[dict[str, object]], columns: tuple[str, ...], flexible: tuple[str, str]
) -> list[dict[str, object]]:
    def widest(name: str) -> int:
        header = len(name.replace("_", " "))
        return max([header, *(len(str(row[name])) for row in rows)])

    first, second = _fit_widths(
        widest(flexible[0]), widest(flexible[1]), _VPD_WIDTH - _fixed_width(columns, flexible)
    )
    for row in rows:
        row[flexible[0]] = _clip(str(row[flexible[0]]), first)
        row[flexible[1]] = _clip(str(row[flexible[1]]), second)
    return rows


def _vpd_policy_cells(item: VpdAssignment) -> dict[str, object]:
    return {
        "TABLE_NAME":  item.table_name,
        "POLICY_NAME": item.policy_name,
        "SEL":         "Y" if item.select else "",
        "DML":         item.dml,
    }


def print_vpd_tables(report: VpdReport, *, opening: str = "") -> None:
    """Render -vpd: functions, one block per function, the missing list, the counts.

    `opening` is the header the CLI already printed before the read, never twice.
    """
    functions = report.functions()
    if opening != _VPD_OPENING:
        print_adt_header(_VPD_OPENING)
    print_adt_table(
        _fitted_rows(
            [row for item in functions for row in _vpd_function_rows(item)],
            _VPD_FUNCTION_COLUMNS,
            ("FUNCTION", "COLUMNS"),
        ),
        columns=list(_VPD_FUNCTION_COLUMNS),
        numeric=("TABLES",),
    )
    for item in functions:
        name = _clip(item.function_name, _VPD_WIDTH - len("VPD POLICIES - :"))
        print_adt_header(f"VPD POLICIES - {name}:")
        print_adt_table(
            _fitted_rows(
                [_vpd_policy_cells(assignment) for assignment in item.assignments],
                _VPD_POLICY_COLUMNS,
                ("TABLE_NAME", "POLICY_NAME"),
            ),
            columns=list(_VPD_POLICY_COLUMNS),
        )
    if report.column:
        print_adt_header(f"VPD MISSING - {report.column}:")
        print_adt_table(
            [{"TABLE_NAME": name} for name in report.missing()],
            columns=["TABLE_NAME"],
        )
    print_adt_header("VPD COVERAGE:")
    total = len(report.tables)
    protected = report.protected_count()
    print_adt_table(
        [{"TABLES": total, "WITH_POLICY": protected, "WITHOUT_POLICY": total - protected}],
        columns=list(_VPD_COVERAGE_COLUMNS),
        numeric=_VPD_COVERAGE_COLUMNS,
    )


# `_TrailingRowFormatter` stood here until ADT #506. It was the third
# implementation of one `TYPE | NAME` shape, correct about the widths and about
# suppressing a repeated type, and wrong only in existing: `shared/object_list.py`
# is that row now, so a change to the column reaches this listing and
# `export_db`'s two in one edit. What it never had, and now inherits, is the
# separator row closing each type group.


def _print_trailing_updated_header(opening: str) -> None:
    # Printed before the read that finds the objects (ADT #887), so it carries
    # no count; `opening` is the header the CLI already put up, never twice.
    if opening != _TRAILING_OPENING:
        print_adt_header(_TRAILING_OPENING)


def print_trailing_updated_objects(
    trailing: list[TrailingObject],
    trailing_actions: list[TrailingAction],
    silent: bool = False,
    opening: str = "",
) -> None:
    """Batch fallback listing the rewritten objects, for non-streamed callers.

    Shares the shared row formatter and the header with the streamed reporter, so
    the two renders are byte-identical. The header counts the objects the sweep
    took on (``trailing``), matching what the streamed reporter knows when it opens
    the section; the rows are the objects actually rewritten.

    The objects arrive in the order the trailing query returned them
    (``ORDER BY s.type, s.name``), and that order is kept rather than re-sorted
    here: the streamed reporter cannot reorder a list it is printing as it goes,
    so a sort in this half alone is exactly the drift the two renders exist to
    rule out.
    """
    _print_trailing_updated_header(opening)
    # The gap and the closing blank frame a listing, so a run that rewrote
    # nothing prints neither (ADT #888): the footer owns the blanks under a bare
    # header, and two more of ours made it four.
    if not silent and trailing_actions:
        print_listing_gap()
        formatter = ObjectRowFormatter()
        for action in trailing_actions:
            for row in formatter.stream_rows(action.object_type, action.object_name):
                print(row)
        print(type_separator())
        print()
    _print_trailing_failures(trailing_actions)


def _print_trailing_failures(trailing_actions: list[TrailingAction]) -> None:
    # a failed rewrite lists its error below the list, keyed by object name and
    # styled like the COMPILE ERRORS / mview action message lists.
    failed_actions = [action for action in trailing_actions if not action.ok and action.error]
    for action in failed_actions:
        print(f"  {action.object_name}) {action.error}")
    if failed_actions:
        print()


class _ConsoleTrailingReporter(RecompileReporter):
    """Streams the object list during a ``-trailing`` run.

    Each object's row prints *before* that object's rewrite runs, so the visible pause
    attaches to the object being worked on rather than to the header above it.

    Under ``-silent`` the section header still prints and the per-object rows do not,
    matching export_db and the console contract (drop per-row detail, keep chrome).

    ``streamed`` lets the CLI skip its fallback render when the runner drove the
    reporter, so a path that never reaches the runner still gets its table.
    """

    def __init__(self, silent: bool = False, opening: str = "") -> None:
        self._silent = silent
        self._opening = opening
        self._formatter = ObjectRowFormatter()
        self._streamed_rows = False
        self.streamed = False

    def begin_trailing(self, candidates: list[TrailingObject]) -> None:
        self.streamed = True
        self._formatter = ObjectRowFormatter()
        self._streamed_rows = False
        _print_trailing_updated_header(self._opening)
        # Nothing to list opens no listing (ADT #888). The gap is committed the
        # moment it prints, so the footer could not fold it away afterwards: an
        # empty run showed four blank lines above TIMER instead of two.
        if self._silent or not candidates:
            return
        print_listing_gap()
        _commit_stdout()

    def trailing_object(self, candidate: TrailingObject) -> None:
        if self._silent:
            return
        self._streamed_rows = True
        for row in self._formatter.stream_rows(candidate.object_type, candidate.object_name):
            print(row, flush=True)

    def end_trailing(self, trailing_actions: list[TrailingAction]) -> None:
        if not self._silent and self._streamed_rows:
            # The last type group closes here, where the caller finally knows
            # there is no next object: the same moment `export_db`'s runner
            # calls `finish_type` for its final type.
            print(type_separator())
            print()
        _commit_stdout()
        _print_trailing_failures(trailing_actions)


def _synonym_owner(synonym: SynonymInfo) -> str:
    return synonym.owner or "UNKNOWN"


def _synonym_status(synonym: SynonymInfo) -> str:
    return synonym.status or "UNKNOWN"


def _synonym_privileges(synonym: SynonymInfo) -> list[str]:
    if not synonym.privileges:
        return [""]
    privileges = [privilege.strip() for privilege in synonym.privileges.split(",")]
    return [privilege for privilege in privileges if privilege] or [""]


def _synonym_sort_key(synonym: SynonymInfo) -> tuple[str, str, str, str, str]:
    return (
        _synonym_owner(synonym),
        _synonym_status(synonym),
        synonym.synonym_name,
        synonym.object_name or "",
        synonym.object_type or "",
    )


def _synonym_row_cells(synonym: SynonymInfo, privilege: str) -> dict[str, object]:
    return {
        "SYNONYM_NAME": synonym.synonym_name,
        "OBJECT_NAME":  synonym.object_name or "",
        "TYPE":         synonym.object_type or "",
        "PRIV":         privilege,
        "GRNT":         "Y" if synonym.is_grantable else "",
        "VALID":        "Y" if _synonym_status(synonym) == "VALID" else "",
    }


def print_synonym_tables(synonyms: list[SynonymInfo], *, opening: str = "") -> None:
    """Render -synonyms as one compact table per target owner.

    `opening` is the header the CLI already printed before the read, never twice.
    """
    sorted_synonyms = sorted(synonyms, key=_synonym_sort_key)
    if not sorted_synonyms:
        if opening != _SYNONYMS_OPENING:
            print_adt_header(_SYNONYMS_OPENING)
        print_adt_table([], columns=list(_SYNONYM_COLUMNS))
        return

    for owner in sorted({_synonym_owner(synonym) for synonym in sorted_synonyms}):
        owner_synonyms = [
            synonym for synonym in sorted_synonyms if _synonym_owner(synonym) == owner
        ]
        print_adt_header(f"SYNONYMS TO SCHEMA {schema_label(owner)}:")
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
    safe upper bound, OBJECT_NAME is stable, STATUS only shrinks (STALE→FRESH),
    LAST_REFRESHED_AT is header-dominated, and TIMER is the final column, so the
    streamed rows stay byte-aligned with the batch render.

    ``streamed`` lets the CLI fall back to the batch render when the runner never
    drove the reporter (the CLI test fakes, every non-mview run), so existing
    output is unchanged.
    """

    def __init__(self) -> None:
        self.streamed = False
        self._layout: _AdtTableLayout | None = None

    def reading_mviews(self) -> None:
        # The title goes up before the listing query, so the wait sits under the
        # name of the thing being listed instead of under the connection block's
        # closing blank (`#372`). The rest of the table opening cannot come with
        # it: the column widths are measured from rows this read has not
        # returned yet.
        print_adt_header("MATERIALIZED VIEWS:")

    def begin_mviews(self, mviews: list[MaterializedView]) -> None:
        self.streamed = True
        rows = [_mview_row_cells(mview) for mview in mviews]
        self._layout = _compute_adt_layout(rows, list(_MVIEW_COLUMNS), {})
        # Mirror print_adt_table's opening exactly: header_adt printed the
        # section title above, then a leading blank, the column header, and the
        # separator.
        print()
        print(self._layout.header_line())
        print(self._layout.separator_line())
        _commit_stdout()

    def _row_layout(self) -> _AdtTableLayout:
        """The widths `begin_mviews` measured, for the two halves of a row.

        A row cannot be drawn before the table it belongs to is open, and the
        runner calls `begin_mviews` before the first `begin_mview` for exactly
        that reason. Named here rather than assumed, so a caller that drives the
        reporter out of order says so instead of raising on `None` two frames
        further down.
        """
        if self._layout is None:  # pragma: no cover, ordering is the runner's
            raise RuntimeError("begin_mviews must open the table before a row is drawn")
        return self._layout

    def begin_mview(self, mview: MaterializedView) -> None:
        values = _mview_row_values(mview)
        print(self._row_layout().cells_segment(values, 0, 1), end="", flush=True)

    def end_mview(self, mview: MaterializedView) -> None:
        values = _mview_row_values(mview)
        # rstrip only here, on the half that completes the line: the leading
        # segment above is mid-line and must keep its gutter, or the two halves
        # stop rejoining byte-for-byte with the batch ``row_line`` (ADT #237).
        print(
            self._row_layout().cells_segment(values, 1, len(_MVIEW_COLUMNS)).rstrip(),
            flush=True,
        )

    def end_mviews(self, mview_actions: list[MViewAction]) -> None:
        # close the table with the trailing blank print_adt_table emits, then list
        # any failed refresh/compile below it (keyed by name, like the batch render).
        print()
        _commit_stdout()
        failed_actions = [a for a in mview_actions if not a.ok and a.error]
        for action in failed_actions:
            print(f"  {action.object_name}) {action.error}")
        if failed_actions:
            print()
