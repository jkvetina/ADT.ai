"""Objects the export could not write, carried to the end of the run (`#917`).

One refused object used to end the whole export: `ORA-31603` on a single
scheduler job stopped a WEBCRM run an hour in, with ~18k objects still queued
behind it. The runner now records the refusal, keeps exporting, and raises
`ExportObjectsFailedError` once everything it could write is on disk, so every
caller still fails and none of them loses the objects after the bad one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from adt_ai.export_db.files import ObjectWritePlan
from adt_ai.export_db.inventory import DatabaseObject


@dataclass(frozen=True)
class ObjectExportFailure:
    database_object: DatabaseObject
    error: Exception

    @property
    def label(self) -> str:
        """`TYPE NAME`, the object as the failure screen names it."""
        return f"{self.database_object.object_type} {self.database_object.name}"


class ExportObjectsFailedError(RuntimeError):
    """Raised after the export, never instead of it.

    `plans` is what the run did write, so a caller that refreshes something
    from the export (the dependency mirror, a `-baseline` measurement) can still
    act on the objects that made it.
    """

    def __init__(
        self,
        failures: Sequence[ObjectExportFailure],
        plans: Sequence[ObjectWritePlan],
    ) -> None:
        self.failures = list(failures)
        self.plans = list(plans)
        count = len(self.failures)
        noun = "object" if count == 1 else "objects"
        super().__init__(
            f"{count} {noun} could not be exported: "
            + ", ".join(failure.label for failure in self.failures)
        )


__all__ = ["ExportObjectsFailedError", "ObjectExportFailure"]
