"""One lifecycle boundary around ``APEX_APP_OBJECT_DEPENDENCY.SCAN`` (ADT #699).

The scan generates `DEPSCAN$<n>#<n>` helper procedures on the target schema, and
dropping them again is a second statement the caller has to issue. Both callers,
the post-deploy verification in `patch/apex_scan.py` and the
`rebuild -app` APEX axis in `dependencies/runner.py`, used to issue that
pair in sequence, which is a guarantee only for the runs that succeed: an
exception anywhere between the two skipped the drop, and a helper left on the
schema is one a later scan reads as its own or somebody removes by hand.

So the steps live here, behind one boundary, and the cleanup runs in a `finally`.
Once the scan statement has been ISSUED the helpers may exist, and that is the
moment the obligation to remove them starts rather than the moment the scan
returns. The cleanup statement is idempotent on its own terms: it loops over
whatever `user_objects` currently matches the helper pattern and drops that, so a
run with nothing to clean is a no-op and a second run after a first is another.

**What the schema actually carries afterwards, measured rather than assumed.**
`tests/tools/depscan_lifecycle_probe.py` issues the scan by hand against a live
target with no cleanup behind it and then counts. On SANDBOX (APEX 26.1,
2026-09-04), against both applications the workspace holds, a bare scan left
**no** `DEPSCAN` object at all, exact pattern or the looser `DEPSCAN%`. So on that
release the stranding this guards against did not reproduce, and the boundary is
defence rather than the repair of an observed leak. It is worth having anyway,
because the cleanup statement exists precisely because some release or some
application does leave them, and a guarantee that costs one `finally` is cheaper
than finding out which.

**Both diagnostics survive a double failure.** A cleanup that fails while the
scan is already failing must not replace the scan's error: the scan's is the one
that says what went wrong with the verification, and the cleanup's is what says
the schema needs looking at. `ScanLifecycleError` carries both, and each single
failure re-raises its own exception unchanged so callers that match on an
`ORA-` message keep matching.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from adt_ai.dependencies.plscope import _Crawl
from adt_ai.dependencies.queries import (
    APEX_APPLICATION_PAGE_IDS_QUERY,
    APEX_SCAN_PAGE_STATEMENT,
    APEX_SCAN_STATEMENT,
    DEPSCAN_CLEANUP_STATEMENT,
    PLSCOPE_SESSION_STATEMENT,
)
from adt_ai.dependencies.write import stamped_row
from adt_ai.export_apex.queries import EXPORT_START_QUERY
from adt_ai.shared.scan_helpers import drop_scan_helpers


class ScanLifecycleError(RuntimeError):
    """A scan that failed AND could not put its helper objects away.

    Raised only when both halves fail, because that is the only case where one
    exception cannot carry the whole truth. The message leads with the scan's
    own error -- it is what the reader came for -- and names the cleanup failure
    after it, since the consequence of that half is a schema still carrying
    `DEPSCAN$` procedures rather than a failed verification.
    """

    def __init__(self, scan_error: BaseException, cleanup_error: BaseException) -> None:
        super().__init__(
            f"{scan_error}; and the DEPSCAN helper cleanup also failed: {cleanup_error}"
        )
        self.scan_error = scan_error
        self.cleanup_error = cleanup_error


def run_component_scan(
    gateway: Any,
    app_id: int,
    *,
    page_id: int | None = None,
    session_statements: Sequence[str] = (),
) -> None:
    """Scan one application's components, always taking the helpers away after.

    ``page_id`` narrows the scan to a single page (ADT #751). It is a real
    narrowing rather than a filter over the answer: `APEX_APP_OBJECT_DEPENDENCY.
    SCAN` takes `p_page_id`, and given one it compiles that page's fragments
    instead of the application's. `None` scans the whole application, which is
    what both the post-deploy verification and the dependency refresh want --
    neither asks about a page, and neither should have to say so.

    ``session_statements`` are issued between the security context and the scan
    itself, for a caller whose session prerequisites are not already set. The
    post-deploy verification passes the PL/Scope session ALTER here; the
    dependency refresh passes nothing, because `plscope.ensure_plscope` has
    already prepared that connection.

    The security context comes first or the scan raises `ORA-20001:
    g_security_group_id must be set`, so it is not part of the guarded region:
    a scan that never ran installed no helpers.
    """
    gateway.execute(EXPORT_START_QUERY, {"app_id": app_id})
    for statement in session_statements:
        gateway.execute(statement)
    statement = APEX_SCAN_STATEMENT if page_id is None else APEX_SCAN_PAGE_STATEMENT
    params: dict[str, int] = {"app_id": app_id}
    if page_id is not None:
        params["page_id"] = page_id
    scan_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        gateway.execute(statement, params)
    except Exception as error:  # noqa: BLE001 - re-raised below, after the cleanup
        scan_error = error
    finally:
        try:
            gateway.execute(DEPSCAN_CLEANUP_STATEMENT)
        except Exception as error:  # noqa: BLE001 - reported beside the scan's own
            cleanup_error = error
    if scan_error is not None and cleanup_error is not None:
        raise ScanLifecycleError(scan_error, cleanup_error) from scan_error
    if scan_error is not None:
        raise scan_error
    if cleanup_error is not None:
        raise cleanup_error


#: The session prerequisite every scan in the walk issues for itself (ADT #921).
#: `plscope.ensure_plscope` sets it on the connection the refresh opens, which is
#: a guarantee only for as long as that connection lives; the walk recycles the
#: session to clear a poisoned one, and an `ALTER SESSION` does not survive that.
#: Issued per scan rather than per session so the two cannot come apart again --
#: it is one statement, and `patch`'s own verification already passes it this way.
SCAN_SESSION_STATEMENTS = (PLSCOPE_SESSION_STATEMENT,)


def reset_session(gateway: Any) -> None:
    """Drop the connection, so the next statement runs on a brand new session.

    **A plain `ROLLBACK` is not enough, measured rather than reasoned** (ADT
    #921). A scan that dies inside `APEX_APP_OBJECT_DEPENDENCY.SCAN` leaves
    state behind that the next scan in that session trips over, and
    `tests/tools/scan_session_reset_probe.py` measured exactly how far each
    repair gets, live, in four arms against the same application:

        after the genuine ORA-01427 page crash, ROLLBACK   -> next page FAILS
        after the genuine ORA-01427 page crash, reconnect  -> next page OK
        after the whole-application crash,       ROLLBACK   -> first page FAILS
        after the whole-application crash,       reconnect  -> first page OK

    A rollback clears the derivative `ORA-06510` crash, which is why a
    rollback-only repair still let most of a walk through, and never the
    genuine one: it cannot reach PL/SQL package state. A new session starts
    with none of it.

    `close()` rather than a second gateway on purpose: `OracleGateway.close()`
    drops the connection and the next call reconnects, so this reaches a fresh
    session through the gateway the caller already holds, whatever factory built
    it and whether or not that factory caches per schema.
    """
    gateway.close()


@dataclass
class PageWalk:
    """What the page-by-page fallback came back with (ADT #865)."""

    #: One ``{table: rows}`` read per page that scanned, in page order.
    scans: list[dict[str, list[dict[str, Any]]]] = field(default_factory=list)
    #: ``(page_id, error)`` for every page whose own scan failed too.
    failed: list[tuple[int, BaseException]] = field(default_factory=list)
    #: Every walked page's name as `apex_application_pages` carries it, which is
    #: what the warning prints beside a broken page's id (ADT #929). A page the
    #: dictionary holds no name for is absent, not blank.
    names: dict[int, str] = field(default_factory=dict)


def scan_page_by_page(
    gateway: Any,
    app_id: int,
    table_queries: Mapping[str, str],
    crawl: _Crawl,
) -> PageWalk:
    """Scan each page of an application on its own after its whole-app scan failed.

    Jan's decision on ADT #865, quoted: *"Page-by-page fallback"*. A whole-app
    scan dies on one component's data, and the page-scoped scan (`#751`)
    compiles one page's fragments instead, so one bad page costs that page
    rather than the application. Every scan clears the application's cache
    first, so ``table_queries`` are read right after each page scan that
    worked, when the views hold that page plus the application-level components
    the scan re-compiles each time, and never after one that failed.

    Page 0 is walked like any other: `apex_application_pages` lists it and the
    scan takes it. ``crawl`` is the caller's row, already open, so the page list
    is read under it; it closes on 100%, or on FAILED when no page scanned.

    **A failed scan poisons the session, so no page is named on one attempt**
    (ADT #921). The crash that sent the run here poisoned the session on its way
    out, and so does every page crash inside the walk, so a scan failing says
    two quite different things and the row cannot tell them apart: this page's
    own data breaks it, or the page before it left the session broken. The first
    live run naming 430.0 and 430.2120 beside the one real defect, 430.2110, is
    exactly that, and both of them scan perfectly clean on their own.

    Two measures, and the second is the one that makes the answer PROVABLE
    rather than trusted. The session is reset before the first page, because the
    whole-application crash is already behind it. And a page that fails is
    scanned once more **on a session of its own**, reset either side of it; it
    is named only when that isolated scan fails too, and a page that comes back
    clean is collateral, contributing its rows from that second pass and never
    reaching the warning. A reset costs a reconnect and only a failure pays it.
    """
    walk = PageWalk()
    pages = gateway.fetch_all(APEX_APPLICATION_PAGE_IDS_QUERY, {"app_id": app_id})
    page_ids = [int(row["PAGE_ID"]) for row in pages]
    walk.names = {
        int(row["PAGE_ID"]): str(row["PAGE_NAME"]).strip()
        for row in pages
        if row.get("PAGE_NAME") is not None and str(row["PAGE_NAME"]).strip()
    }

    def scan_once(page: int) -> BaseException | None:
        try:
            run_component_scan(
                gateway, app_id, page_id=page, session_statements=SCAN_SESSION_STATEMENTS
            )
        except Exception as error:  # noqa: BLE001 - the caller names it, or retries it
            return error
        return None

    def read_tables() -> dict[str, list[dict[str, Any]]]:
        return {
            table: gateway.fetch_all(query, {"app_id": app_id})
            for table, query in table_queries.items()
        }

    # The whole-application scan died on this session; the first page inherits
    # that and nothing else would clear it before the walk's own first failure.
    reset_session(gateway)
    for index, page in enumerate(page_ids, start=1):
        error = scan_once(page)
        if error is not None:
            # The isolated re-scan: one page, one session, nothing before it.
            reset_session(gateway)
            error = scan_once(page)
            if error is not None:
                walk.failed.append((page, error))
                reset_session(gateway)
        if error is None:
            walk.scans.append(read_tables())
        # The last page is closed below, on 100% or on FAILED, not by `advance`.
        if index < len(page_ids):
            crawl.advance(index, len(page_ids))
    if walk.scans:
        crawl.close()
    else:
        crawl.fail()
    return walk


_OBJECTS_TABLE = "APEX_USED_DB_OBJECTS"
_PROPS_TABLE = "APEX_USED_DB_OBJECT_COMP_PROPS"
_OBJECT_IDENTITY = ("USED_DB_OBJECT_OWNER", "USED_DB_OBJECT_NAME", "USED_DB_OBJECT_TYPE")


def union_scans(
    app_id: int, scans: list[dict[str, list[dict[str, Any]]]]
) -> dict[str, list[dict[str, Any]]]:
    """One application's dependency rows from several scans, each row once.

    The scan clears and re-inserts, so an object or property row a second scan
    also returns (the application-level components, which come back from every
    page scan) may arrive under a fresh id. An object is therefore the same
    object by its owner, name and type, keeping the id it was first seen under,
    and a property row by everything except its own id once its object id is
    mapped onto that. Rows repeated inside one scan are left alone: only a
    repeat across scans is the scan's doing. The 24.2 read repeats a property
    row once per dependency it carries, and those identical rows share one
    store key, so the store keeps one of each exactly as after a whole-app
    scan: on app 430 the walk read 21010 property rows and stored 10184.
    """
    objects: list[dict[str, Any]] = []
    props: list[dict[str, Any]] = []
    object_ids: dict[tuple[Any, ...], Any] = {}
    prop_keys: set[tuple[Any, ...]] = set()
    stamp = {"APPLICATION_ID": app_id}
    for scan in scans:
        local_ids: dict[Any, Any] = {}
        for raw in scan.get(_OBJECTS_TABLE, []):
            row = stamped_row(_OBJECTS_TABLE, raw, stamp)
            key = tuple(row.get(column) for column in _OBJECT_IDENTITY)
            if key not in object_ids:
                taken = set(object_ids.values())
                new_id = row["USED_DB_OBJECT_ID"]
                if new_id in taken:
                    new_id = max(taken) + 1
                object_ids[key] = new_id
                objects.append({**row, "USED_DB_OBJECT_ID": new_id})
            local_ids[row["USED_DB_OBJECT_ID"]] = object_ids[key]
        seen_here: set[tuple[Any, ...]] = set()
        for raw in scan.get(_PROPS_TABLE, []):
            row = stamped_row(_PROPS_TABLE, raw, stamp)
            object_id = row["USED_DB_OBJECT_ID"]
            row["USED_DB_OBJECT_ID"] = local_ids.get(object_id, object_id)
            key = tuple(value for column, value in row.items() if column != "PROPERTY_ID")
            if key in prop_keys:
                continue
            seen_here.add(key)
            props.append(row)
        prop_keys |= seen_here
    return {_OBJECTS_TABLE: objects, _PROPS_TABLE: props}


#: The lines of an APEX scan stack that are not its cause (ADT #865): the
#: savepoint error APEX's handler raises on the way out, and the frames.
_MASKING_ERRORS = ("ORA-01086", "ORA-06512")


def scan_error_sentence(error: BaseException) -> str:
    """The error's own first message, whole, however Oracle broke it up.

    Usually the first line is the message. **A stack opening on `ORA-01086` is
    the exception** (ADT #865): `savepoint 'START_SCAN' never established` is
    APEX's scan failing to roll back to its own savepoint on the way out, and
    the error that stopped the scan sits further down, under `ORA-06512`
    frames (`ORA-01427: single-row subquery returns more than one row` on app
    430). So the first `ORA-` line that is neither is the sentence, and the
    mask is printed only when nothing sits under it.

    `ORA-06550` opens with a locator, `ORA-06550: line 1, column 7:`, and puts
    the message on the line under it, so a first-line rule printed the
    coordinates of the error and never the error. A line ending in a colon is
    Oracle saying the sentence continues, measured live on SANDBOX 2026-09-20.
    """
    lines = [line.strip() for line in str(error).splitlines() if line.strip()]
    if not lines:
        return repr(error)
    start = 0
    if lines[0].startswith("ORA-01086"):
        start = next(
            (
                index
                for index, line in enumerate(lines)
                if line.startswith("ORA-") and not line.startswith(_MASKING_ERRORS)
            ),
            0,
        )
    if lines[start].endswith(":") and len(lines) > start + 1:
        return f"{lines[start]} {lines[start + 1]}"
    return lines[start]


__all__ = [
    "SCAN_SESSION_STATEMENTS",
    "PageWalk",
    "ScanLifecycleError",
    "drop_scan_helpers",
    "reset_session",
    "run_component_scan",
    "scan_error_sentence",
    "scan_page_by_page",
    "union_scans",
]
