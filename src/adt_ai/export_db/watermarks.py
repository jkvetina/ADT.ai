"""The `-recent` watermark an export stamps once a schema is fully written.

Split out of `runner.py` at the 20 KB per-file context budget, along the seam
`export_apex` already carries in its own `watermarks.py`: everything else in
that file discovers, pulls and normalizes objects, while these three answer one
separate question, whether this run earned the right to move the schema's
`-recent` cutoff forward.

`request` is untyped for the reason `grants.py` gives: annotating it would mean
importing `ExportDbRequest` back from the module that imports this one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adt_ai.shared.recent_state import RecentStore, may_advance

if TYPE_CHECKING:
    # Named for the checker only, which is what the module docstring above
    # says this module exists to avoid doing at runtime.
    from adt_ai.export_db.request import ExportDbRequest


def is_narrowed(request: ExportDbRequest) -> bool:
    """Whether the run selected a subset of the schema rather than covering it.

    A narrowed run must never advance a watermark: a `-name`/`-type`/`-by`/`-my`
    export touches a slice, so stamping it would silently mark everything it did
    not look at as current and hide those objects from every later bare `-recent`.
    """
    return any(
        (
            request.names is not None,
            request.object_types is not None,
            request.authors is not None,
            request.my_changes,
            request.changed_by is not None,
        )
    )


def stored_watermark(request: ExportDbRequest, schema: str) -> str | None:
    if request.environment is None:
        return None
    return RecentStore.load(request.root).get("export_db", [request.environment, schema])


def advance_watermark(
    request: ExportDbRequest,
    schema: str,
    candidate: str | None,
    stored: str | None,
    narrowed: bool,
) -> None:
    """Stamp this schema's pass, saving immediately so later failures cannot undo it."""
    if candidate is None or request.environment is None:
        return
    if not may_advance(
        recent   = request.recent,
        stored   = stored,
        db_now   = candidate,
        narrowed = narrowed,
    ):
        return
    store = RecentStore.load(request.root)
    store.set("export_db", [request.environment, schema], candidate)
    store.save()
