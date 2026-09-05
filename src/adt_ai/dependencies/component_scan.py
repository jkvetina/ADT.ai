"""One lifecycle boundary around ``APEX_APP_OBJECT_DEPENDENCY.SCAN`` (ADT #699).

The scan generates `DEPSCAN$<n>#<n>` helper procedures on the target schema, and
dropping them again is a second statement the caller has to issue. Both callers,
the post-deploy verification in `patch/apex_scan.py` and the
`dependencies -refresh` APEX axis in `dependencies/runner.py`, used to issue that
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

from collections.abc import Sequence
from typing import Any

from adt_ai.dependencies.queries import (
    APEX_SCAN_STATEMENT,
    DEPSCAN_CLEANUP_STATEMENT,
)
from adt_ai.export_apex.queries import EXPORT_START_QUERY


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
    session_statements: Sequence[str] = (),
) -> None:
    """Scan one application's components, always taking the helpers away after.

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
    scan_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        gateway.execute(APEX_SCAN_STATEMENT, {"app_id": app_id})
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


def drop_scan_helpers(gateway: Any) -> None:
    """Take the helper procedures away, on their own.

    Idempotent by construction: the statement drops whatever currently matches
    the `DEPSCAN$<n>#<n>` pattern, so a schema that carries none is a no-op.
    """
    gateway.execute(DEPSCAN_CLEANUP_STATEMENT)


__all__ = ["ScanLifecycleError", "drop_scan_helpers", "run_component_scan"]
