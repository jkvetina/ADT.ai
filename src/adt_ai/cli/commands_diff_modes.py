"""The `diff` modes that replace the object comparison: `-rest`, `-apex`, `-data`.

Split out of `cli/commands_diff.py` by ADT #893, when `-page` and a `-limit`
for every mode took that module to the 24 KB context cap. What stays there is
the command itself: its arguments, its connections and the SQLcl object
comparison. What moved is one runner per mode and the countdown they share,
each small enough that a new mode or flag is a helper of its own rather than a
branch through the others.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from adt_ai.cli.constants import QueryGateway, print_adt_header
from adt_ai.cli.context_apex import (
    ApexAppSelection,
    _apex_scope,
    _app_in_selection,
    _parse_apex_app_selection,
)
from adt_ai.cli.context_errors import _project_relative
from adt_ai.cli.diff_apex_reporter import report_apex
from adt_ai.cli.diff_data_reporter import report_data_diff, write_data_log
from adt_ai.cli.diff_pull import Pull
from adt_ai.cli.diff_reporters import (
    COMPARING_HEADER,
    IN_SYNC_APEX,
    IN_SYNC_APEX_PAGES,
    IN_SYNC_REST,
    report_summary,
)
from adt_ai.diff.apex import APEX_APPLICATION_TYPE, ApexDiffRunner, ApexSide
from adt_ai.diff.data import TABLE_DATA_TYPE, DataDiffRunner, DataOptions, DataSide
from adt_ai.diff.pull import pull_data, pull_rest
from adt_ai.diff.pull_apex import pull_apex
from adt_ai.diff.rest import REST_MODULE_TYPE, RestDiffRunner
from adt_ai.diff.summary import DiffSummary
from adt_ai.diff.timers import pair_key, previous_seconds, record_seconds, timers_path
from adt_ai.export_apex.filters import ApexPageSelection
from adt_ai.export_apex.rest import RestExport, export_rest
from adt_ai.shared.connections import Connection
from adt_ai.shared.timed_bar import FALLBACK_TARGET_SECONDS, TimedProgressBar


@dataclass(frozen=True)
class ApexSelection:
    """What `diff -apex` was asked to compare: `-app` and `-page`, parsed and as typed.

    The tokens key the countdown; `apps` replaces each side's configured
    application list, and `pages` narrows the screen (ADT #893). `target_app`
    is the target's application paired with the one `-app` names. `None` is a
    flag that was not given.
    """

    app_tokens  : tuple[str, ...] = ()
    apps        : ApexAppSelection | None = None
    page_tokens : tuple[str, ...] = ()
    pages       : ApexPageSelection | None = None
    target_app  : int | None = None


def _run_rest_diff(
    gateways: Sequence[QueryGateway],
    connections: tuple[Connection, Connection],
    config: Mapping[str, object],
    root: Path,
    *,
    names: tuple[str, ...],
    verbose: bool,
    debug: bool,
    limit: int | None = None,
    pull: Pull | None = None,
) -> int:
    """`diff -rest`: both schemas' REST modules, privileges and roles, compared (ADT #878, #880).

    The same screen as the object comparison, top to bottom: `COMPARING
    SCHEMAS:` over one crawling row, then the counts, and `-verbose` for the
    listing. `REST MODULE`, `REST PRIVILEGE` and `REST ROLE` are the object
    types it can print. The two exports
    run from a throwaway folder that is gone when the run ends, because a
    comparison writes nothing a user asked to keep. What the target's export
    answered is kept for `-restore`, which writes it rather than exporting again.
    """
    exports: dict[str, RestExport] = {}

    def export(gateway: QueryGateway, cwd: Path, settings: Mapping[str, object]) -> RestExport:
        exports[cwd.name] = export_rest(gateway, cwd, settings)
        return exports[cwd.name]

    def compare() -> DiffSummary:
        with tempfile.TemporaryDirectory(prefix="adt_diff_rest_") as workdir:
            return RestDiffRunner(export).run(gateways[0], gateways[1], config, Path(workdir))

    # Keyed apart from the object comparison of the same pair: a REST export
    # takes seconds where the object one takes a quarter minute, so one shared
    # figure would count each down from the other's clock.
    source, target = connections
    summary = _compare_under_a_bar(
        connections,
        root,
        pair_key(source.schema, target.schema, types=(REST_MODULE_TYPE,)),
        compare,
        debug = debug,
    )
    if summary is None:
        return 1
    shown = summary.select(names=names)
    report_summary(
        shown,
        verbose      = verbose,
        in_sync_note = IN_SYNC_REST,
        limit        = limit,
        tail         = pull.tail(
            REST_MODULE_TYPE, lambda sides: pull_rest(sides, shown, exports["target"])
        )
        if pull
        else None,
    )
    return 1 if pull and pull.failed else 0


def _run_apex_diff(
    gateways: Sequence[QueryGateway],
    connections: tuple[Connection, Connection],
    root: Path,
    *,
    selection: ApexSelection,
    names: tuple[str, ...],
    verbose: bool,
    debug: bool,
    limit: int | None = None,
    pull: Pull | None = None,
) -> int:
    """`diff -apex`: both schemas' APEX applications and static files, compared (ADT #778).

    Each application gets a summary, a row per page and per component type
    outside a page; `-verbose` adds a group per page and one for the
    application, and the workspace's files close the screen
    (`cli/diff_apex_reporter`, ADT #896).
    `-page` narrows the run to those pages (ADT #893), and a run where none of
    them differ says the selected pages match rather than that the whole
    schemas do. `-target-app` reads the target's side of the one application
    `-app` names from another id, a working copy of it (ADT #893).
    """
    source, target = connections
    paired = (
        {int(selection.apps.explicit_ids[0]): selection.target_app}
        if selection.apps is not None and selection.target_app is not None
        else {}
    )
    sides = (
        _apex_side(gateways[0], source, selection.apps),
        _apex_side(
            gateways[1],
            target,
            _parse_apex_app_selection([str(own) for own in paired.values()]) or selection.apps,
            paired,
        ),
    )
    # Keyed by the applications and pages asked for, since both change the
    # size of the job, and unkeyed by `-page` and `-target-app` when they are
    # absent, so a history recorded before them still counts down the same run.
    pages = tuple(f"PAGE {token}" for token in selection.page_tokens)
    copies = tuple(f"TARGET APP {own}" for own in paired.values())
    result = _compare_under_a_bar(
        connections,
        root,
        pair_key(
            source.schema,
            target.schema,
            types = (APEX_APPLICATION_TYPE, *pages, *copies),
            names = selection.app_tokens,
        ),
        lambda: ApexDiffRunner().run(*sides),
        debug = debug,
    )
    if result is None:
        return 1
    if selection.pages is not None:
        result = result.select_pages(selection.pages.matches)
    shown = result.select(names)
    report_summary(
        shown.summary,
        verbose      = verbose,
        in_sync_note = IN_SYNC_APEX if selection.pages is None else IN_SYNC_APEX_PAGES,
        listing      = lambda: report_apex(shown, verbose=verbose, limit=limit),
        tail         = pull.tail(
            APEX_APPLICATION_TYPE,
            lambda pulled: pull_apex(pulled, shown, sides, selection.pages),
        )
        if pull
        else None,
    )
    return 1 if pull and pull.failed else 0


def _apex_side(
    gateway: QueryGateway,
    connection: Connection,
    apps: ApexAppSelection | None,
    ids: Mapping[int, int] | None = None,
) -> ApexSide:
    """One side scoped the way `export_apex` scopes it, `-app` replacing the list.

    The connection's `apex` workspace, group and application list narrow what
    the owner has, and `-app` replaces that list when it is given. `ids` is the
    `-target-app` pairing, the compared id to the one this side keeps it under.
    """
    scope = _apex_scope(connection.apex)
    selection = apps or _parse_apex_app_selection(scope.app_ids)
    return ApexSide(
        gateway,
        connection.schema,
        workspace = scope.workspace,
        group     = scope.group,
        selects   = (
            None if selection is None else lambda app_id: _app_in_selection(app_id, selection)
        ),
        ids       = dict(ids or {}),
    )


def _run_data_diff(
    sides: tuple[DataSide, DataSide],
    connections: tuple[Connection, Connection],
    config: Mapping[str, object],
    root: Path,
    *,
    names: tuple[str, ...],
    options: DataOptions,
    verbose: bool,
    debug: bool,
    log: Path | None = None,
    pull: Pull | None = None,
) -> int:
    """`diff -data`: the rows of both schemas' exported tables, compared (ADT #877, #883).

    The `-rest` screen up to the counts, which are rows per table rather than
    objects per type (`cli/diff_data_reporter`). Its countdown is keyed by the
    tables asked for and the `-limit`, since either one changes the size of the
    job. `log` is where `-out` asked for the untrimmed rows (`#886`).
    """
    source, target = connections
    variant = (TABLE_DATA_TYPE, *((f"LIMIT {options.limit}",) if options.limit else ()))
    result = _compare_under_a_bar(
        connections,
        root,
        pair_key(source.schema, target.schema, types=variant, names=names),
        lambda: DataDiffRunner().run(sides[0], sides[1], config, names=names, options=options),
        debug = debug,
    )
    if result is None:
        return 1
    if log is not None:
        write_data_log(result, log, _comparison_row(source, target))
    compared = result
    report_data_diff(
        result,
        verbose = verbose,
        log     = _project_relative(log, root) if log is not None else None,
        tail    = pull.tail(TABLE_DATA_TYPE, lambda sides: pull_data(sides, compared))
        if pull
        else None,
    )
    return 1 if pull and pull.failed else 0


def _compare_under_a_bar[T](
    connections: tuple[Connection, Connection],
    root: Path,
    pair: str,
    compare: Callable[[], T],
    *,
    debug: bool,
) -> T | None:
    """`COMPARING SCHEMAS:` over one crawling row, for the modes that replace the objects.

    `None` means the comparison failed and `DIFF FAILED:` is already on screen.
    `-debug` compares without the bar and lets a failure raise, the posture
    the object comparison takes.
    """
    print_adt_header(COMPARING_HEADER)
    print()
    carried: list[T] = []
    try:
        if debug:
            carried.append(compare())
        else:
            path = timers_path(root)
            target_seconds = previous_seconds(path, pair) or FALLBACK_TARGET_SECONDS
            elapsed = TimedProgressBar().run(
                _comparison_row(*connections), target_seconds, lambda: carried.append(compare())
            )
            record_seconds(path, pair, elapsed)
    except RuntimeError as error:
        if debug:
            raise
        print_adt_header("DIFF FAILED:")
        print(str(error))
        return None
    return carried[0]


def _comparison_row(source: Connection, target: Connection) -> str:
    """`DEV.GSN -> TEST.GSN`: the direction, named once, for the whole screen.

    It read `GSN -> GSN` until `#790`, which is the same word twice and the usual
    comparison, one schema across two environments. The listings below carry
    `MISSING` / `CHANGED` / `EXTRA` and no `ON <env>` suffix on any row, because
    the target is the same for every one of them (Jan: *"thats redundant and
    increasing column width for no valid reason"*). This row is where the reader
    learns which side those words are about, so it has to name both sides.
    """
    return (
        f"{source.environment}.{source.schema} -> "
        f"{target.environment}.{target.schema}"
    )


__all__ = [name for name in globals() if not name.startswith("__")]
