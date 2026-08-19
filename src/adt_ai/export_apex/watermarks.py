"""`-recent` watermark planning for the APEX export runner (ADT #155).

The stamps lived in the `export_apex` branch of `config/internal/recent.yaml`
until `#369` moved them into `config/internal/apex.db` beside the rest of what
ADT caches about an application. `export_db`'s watermarks did not move: they
describe a database export, and `recent.yaml` is still where they live.
"""

from __future__ import annotations

from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.request import ApexExportRequest
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.recent_state import is_bare_recent, may_advance

# Formats whose coverage a `-recent` watermark can describe. `rest`, `files`, and
# `files_ws` export artefacts that carry no component `last_updated_on`, so a
# component-level cutoff says nothing about whether they are current.
_WATERMARKED_FORMATS = ("full", "split", "readable", "embedded")


def is_watermarking(request: ApexExportRequest) -> bool:
    """Whether this run could stamp a watermark, and so needs a database clock read."""
    if request.recent_report_only or request.narrowed:
        return False
    return any(
        action in _WATERMARKED_FORMATS
        for action, wanted in request.actions.items()
        if wanted
    )


class ApexWatermarkMixin:
    def _requested_formats(self, request: ApexExportRequest) -> list[str]:
        return [action for action, wanted in request.actions.items() if wanted]

    def _listing_cutoff(
        self,
        request: ApexExportRequest,
        application: ApexApplication,
    ) -> str | None:
        """The oldest watermark across the formats this run will export.

        One listing feeds every requested format, so it must reach back as far as
        the least recently exported one. A format with no watermark yet makes the
        listing unbounded, over-export is harmless, a missed component is not.
        """
        if not is_bare_recent(request.recent) or request.environment is None:
            return None
        formats = self._requested_formats(request) or _WATERMARKED_FORMATS
        store = request.apex_store or ApexStore.load(request.root)
        stored = [
            store.watermark(request.environment, application.app_id, action)
            for action in formats
        ]
        if any(value is None for value in stored):
            return None
        return min(stored)  # type: ignore[type-var]

    def _advance_watermarks(
        self,
        request: ApexExportRequest,
        application: ApexApplication,
        candidate: str | None,
    ) -> None:
        """Stamp each exported format's own key after the app's pass succeeded.

        Each format is judged against **its own** stored watermark, never the
        shared listing cutoff: the listing reached back to the oldest of them, so
        every format was covered at least as far back as its own stamp.
        """
        if candidate is None or request.environment is None or request.recent_report_only:
            return
        store = request.apex_store or ApexStore.load(request.root)
        for action in self._requested_formats(request):
            if action not in _WATERMARKED_FORMATS:
                continue
            if not may_advance(
                recent   = request.recent,
                stored   = store.watermark(request.environment, application.app_id, action),
                db_now   = candidate,
                narrowed = request.narrowed,
            ):
                continue
            # No `save()` and no dirty flag: each stamp is its own committed
            # write, so a run that dies after the third application keeps the
            # three it covered instead of losing all of them with the file.
            store.set_watermark(request.environment, application.app_id, action, candidate)
