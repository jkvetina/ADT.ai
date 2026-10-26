"""What `calendar`, `connection`, `diff`, `rebuild` and `search` found, as `-json` data (ADT #1070).

These commands answer with lists more than with failures: the commits a search
matched, the branches a reveal found, the objects two schemas disagree about.
Each recorder takes the object the command already holds and turns it into
`result` counts and listings, or into diagnostics where an entry is a finding,
and is called beside the line that renders the same object, so the screen and
the JSON are read off one value and cannot drift apart.

`json_findings.py` holds the compile and scan findings; this module holds the
rest. It imports no command module, so none of them imports it back in a
cycle, and every function is a no-op outside a `-json` run.

**No secret reaches this module.** The `connection` recorders take the action,
the file and the editor's summary line, never the request: a password, a key
or a stored ciphertext has no field here to land in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from adt_ai.cli.context_errors import _DATABASE_ERROR_CODE_RE, _project_relative
from adt_ai.diff.inventory import CHANGED, EXTRA, MISSING
from adt_ai.shared import run_report

if TYPE_CHECKING:  # pragma: no cover - typing only
    from adt_ai.calendar.runner import CalendarResult
    from adt_ai.connection.probe import ObjectCount
    from adt_ai.connection.runner import ConnectionEditResult
    from adt_ai.diff.apex import ApexDiff, FileChange
    from adt_ai.diff.data import DataDiff
    from adt_ai.diff.summary import DiffSummary
    from adt_ai.rebuild.models import RebuildResult
    from adt_ai.rebuild.reveal import RevealResult
    from adt_ai.search.data import DataFailure, DataHit
    from adt_ai.search.runner import SearchResult
    from adt_ai.search.term_model import TermResult
    from adt_ai.shared.commit_store import Span

#: The `Hit` fields a TERM hit is written with, empty ones left out: a file hit
#: has no page, and a `null` per absent field is tokens an agent pays for.
_HIT_FIELDS = ("source", "component", "prop", "app", "page", "line", "flag", "excerpt")


def _first_line(text: object) -> str:
    """The headline of a refusal: a database message's code line, not its help URL."""
    lines = [line.strip() for line in str(text).splitlines() if line.strip()]
    return lines[0] if lines else ""


def database_code(error: object, fallback: str) -> str:
    """The `ORA-`/`DPY-` code a database refusal carries, else the screen's own code.

    The code is what an agent branches on, and the screen code is the same for
    a wrong password and an unreachable listener.
    """
    match = _DATABASE_ERROR_CODE_RE.search(str(error))
    return match.group(0).upper() if match else fallback


def _present(values: Mapping[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in values.items() if value is not None and value != ""}


# -------------------------------------------------------------------- calendar


def calendar(result: CalendarResult) -> None:
    """The month, and one row per author with the counts their header prints."""
    run_report.result(month=result.month)
    run_report.listing(
        "authors",
        [
            {
                "author"  : author.author,
                "commits" : author.commit_count,
                "tickets" : author.ticket_count,
                "prs"     : author.pr_count,
                "days"    : author.days,
            }
            for author in result.authors
        ],
    )


# ---------------------------------------------------------------------- search


def search_history(result: SearchResult, root: Path, *, file_limit: int, restore: bool) -> None:
    """The commits, the files `-files` asked for, and what `-restore` wrote or could not."""
    run_report.listing(
        "commits",
        [
            {
                "number"  : record.number,
                "hash"    : record.commit_hash,
                "date"    : record.date,
                "author"  : record.author,
                "summary" : record.summary,
                "files"   : [
                    {"path": path, "status": record.file_statuses.get(path, "M")}
                    for path in record.files[:file_limit]
                ],
            }
            for record in result.records
        ],
    )
    if restore:
        run_report.listing(
            "restored", [{"file": _project_relative(path, root)} for path in result.restored_files]
        )
    # A partial restore is not a failed run, the screen says WARNING and exits 0.
    for path in result.failed_restores:
        run_report.diagnostic(
            "warning",
            "COULD NOT RESTORE",
            "git could not show this version, so it was not restored",
            file = path,
        )


def search_term(result: TermResult, links: Sequence[Mapping[str, object]]) -> None:
    """The hits, the graph around them, and every layer that could not be read."""
    run_report.result(searched=sorted(result.searched))
    run_report.listing(
        "hits",
        [_present({name: getattr(hit, name) for name in _HIT_FIELDS}) for hit in result.hits],
    )
    run_report.listing(
        "relations",
        [
            {"object": rel.obj, "relation": rel.relation, "type": rel.kind, "name": rel.name}
            for rel in result.relations
        ],
    )
    run_report.listing("links", list(links))
    # A layer nobody refreshed is not an empty answer, and the screen keeps the
    # two apart under `WARNING - NOT SEARCHED`; so does this. With no layer read
    # at all they are the run's failure, exit 1, and so errors.
    severity = "warning" if result.searched else "error"
    for layers, reason in result.not_searched:
        run_report.diagnostic(severity, "NOT SEARCHED", f"{layers}: {reason}")


def search_data(
    schemas  : Sequence[str],
    hits     : Sequence[DataHit],
    failures : Sequence[DataFailure],
) -> None:
    """The rows holding TERM, and each object the database refused, which fails the run."""
    run_report.result(schemas=list(schemas))
    run_report.listing(
        "hits",
        [
            {"table": hit.table, "row": hit.row, "column": hit.column, "value": hit.value}
            for hit in hits
        ],
    )
    for failure in failures:
        run_report.diagnostic(
            "error",
            database_code(failure.error, "NOT SEARCHED"),
            _first_line(failure.error) or type(failure.error).__name__,
            object_name = failure.table,
        )


# --------------------------------------------------------------------- rebuild


def rebuild_walk(result: RebuildResult) -> None:
    """Each branch store and how many commits it holds after the walk."""
    run_report.listing(
        "branches",
        [
            {"branch": branch, "commits": result.record_counts.get(branch, 0)}
            for branch in result.branches
        ],
    )


def verified_stores(stores: Sequence[tuple[str, Span, Sequence[str]]]) -> None:
    """`-verify`: one row per store, and each hole in its numbering as an error."""
    rows = []
    for branch, span, problems in stores:
        state = "EMPTY" if span.size == 0 else "BROKEN" if problems else "CONTIGUOUS"
        rows.append(
            {
                "branch"  : branch,
                "commits" : span.size,
                "first"   : span.floor,
                "last"    : span.ceiling,
                "state"   : state,
            }
        )
        for problem in problems:
            run_report.diagnostic("error", "BROKEN", problem)
    run_report.listing("stores", rows)


def revealed(result: RevealResult) -> None:
    """`-reveal`: the branches shown, and how many matched before `-limit` cut them."""
    run_report.result(matched=result.total)
    run_report.listing(
        "branches",
        [
            {"name": branch.name, "committed": branch.committed, "author": branch.author}
            for branch in result.branches
        ],
    )


def switched(branch: str, commits: Sequence[tuple[str, str]]) -> None:
    """`-switch`: the branch checked out and its own commits, newest first."""
    run_report.result(branch=branch)
    run_report.listing("commits", [{"date": when, "summary": subject} for when, subject in commits])


def refresh_planned(schemas: Sequence[str], apps: Sequence[int]) -> None:
    """The database half: which schemas and applications this run refreshes."""
    run_report.result(refreshed_schemas=list(schemas), refreshed_apps=list(apps))


def scan_failures(schema: str, apps: Iterable[int] | None) -> None:
    """An application whose component scan failed: stepped over, and the run exits 1.

    `None` reads as no failure, the way the exit code beside the call reads it.
    """
    for app in apps or ():
        run_report.diagnostic(
            "error",
            "COMPONENT SCAN FAILED",
            f"APP {app} component scan failed, its dependencies were kept",
            schema = schema,
            app    = app,
        )


# ------------------------------------------------------------------ connection


def connection_tested(
    environment : str,
    schemas     : Sequence[str],
    failures    : Sequence[tuple[str, Exception]],
    code_of     : Callable[[Exception], str],
) -> None:
    """`-test`: each schema's `OK`/`ERROR`, and each refusal by its database code."""
    failed = {schema for schema, _error in failures}
    run_report.result(environment=environment)
    run_report.listing(
        "schemas",
        [{"schema": schema, "status": "ERROR" if schema in failed else "OK"} for schema in schemas],
    )
    for schema, error in failures:
        run_report.diagnostic(
            "error", database_code(error, code_of(error)), _first_line(error), schema=schema
        )


def schema_objects(environment: str, schema: str, counts: Sequence[ObjectCount]) -> None:
    """`-test -schema`: the object overview, one row per type."""
    run_report.result(environment=environment, schema=schema)
    run_report.listing(
        "objects",
        [
            {"object_type": count.object_type, "count": count.total, "invalid": count.invalid}
            for count in counts
        ],
    )


def connection_edited(
    action  : str,
    path    : Path,
    edit    : ConnectionEditResult,
    *,
    applied : bool,
) -> None:
    """An edit action: what it does to which file, previewed or applied.

    The preview block is left out on purpose. It is the edited file's shape,
    stripped of secrets for the screen, and a reader of the JSON wants the
    action, which the summary line already names.
    """
    run_report.result(
        action  = action,
        file    = str(path),
        mode    = "applied" if applied else "preview",
        summary = edit.summary,
        notes   = list(edit.notes),
    )


# ------------------------------------------------------------------------ diff


def diff_summary(summary: DiffSummary | None) -> None:
    """Every difference an `info` finding typed by its status, the tally in `result`.

    A difference is the answer the comparison was asked for, not a problem with
    the run, which is why none of them is a warning. `None` is a comparison
    whose trees could not be read, and saying `in_sync` either way would be a
    claim about objects nobody looked at.
    """
    if summary is None:
        run_report.result(compared=False)
        return
    for change in summary.changes:
        run_report.diagnostic(
            "info",
            change.status,
            f"{change.object_type} {change.object_name} {change.status}",
            object_type = change.object_type,
            object_name = change.object_name,
        )
    for grant in summary.grants:
        run_report.diagnostic(
            "info",
            grant.status,
            f"GRANT {', '.join(grant.privileges)} ON {grant.owner}.{grant.object_name} "
            f"TO {grant.grantee} {grant.status}",
            schema      = grant.owner,
            object_type = grant.object_type,
            object_name = grant.object_name,
        )
    statuses = [change.status for change in summary.changes]
    statuses += [grant.status for grant in summary.grants]
    run_report.result(
        compared = True,
        in_sync  = summary.in_sync,
        objects  = len(summary.changes),
        grants   = len(summary.grants),
        missing  = statuses.count(MISSING),
        changed  = statuses.count(CHANGED),
        extra    = statuses.count(EXTRA),
    )


def apex_diff(diff: ApexDiff) -> None:
    """`-apex`: the summary's findings, then each application and workspace file."""
    diff_summary(diff.summary)
    run_report.listing(
        "applications",
        [
            _present(
                {
                    "application" : app.application,
                    "app"         : app.app_id,
                    "status"      : app.status,
                    "target"      : app.target,
                    "components"  : [
                        {"kind": part.kind, "name": part.name, "status": part.status}
                        for part in app.components
                    ],
                    "files"       : [_file(file) for file in app.files],
                }
            )
            for app in diff.applications
        ],
    )
    run_report.listing("workspace_files", [_file(file) for file in diff.workspace_files])


def _file(file: FileChange) -> dict[str, object]:
    return {"name": file.name, "status": file.status, "size": file.size}


def data_diff(diff: DataDiff) -> None:
    """`-data`: the row counts per table, every compared table listed."""
    run_report.result(
        in_sync = diff.in_sync,
        stopped = diff.stopped,
        missing = sum(table.missing for table in diff.tables),
        extra   = sum(table.extra for table in diff.tables),
        changed = sum(table.changed for table in diff.tables),
    )
    run_report.listing(
        "tables",
        [
            {
                "table"   : table.name,
                "missing" : table.missing,
                "extra"   : table.extra,
                "changed" : table.changed,
                "stopped" : table.stopped,
            }
            for table in diff.tables
        ],
    )


def restored(branch: str, files: Sequence[tuple[str, str]]) -> None:
    """`-restore`: what git sees changed on disk, and the branch it is on."""
    run_report.result(branch=branch)
    run_report.listing("restored", [{"file": name, "status": status} for name, status in files])


def restore_failed(code: str, error: object) -> None:
    """A restore that raised: an error, since it fails the run."""
    run_report.diagnostic("error", database_code(error, code), _first_line(error))
