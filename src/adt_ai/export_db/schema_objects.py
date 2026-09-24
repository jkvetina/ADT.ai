"""One schema's listed objects exported, then what that export wrote recorded.

Split out of `runner.py` by `#923`, whose job baseline fix would have grown that
file further past the 20 KB context budget. The seam is the one the schema loop
already had: the runner lists a schema's objects and puts its overview on screen,
and this module pulls, normalizes and yields each listed object, lists the ones
the database refused, and stamps the state the written ones earned. The runner
keeps the order the calls come in.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from typing import TYPE_CHECKING

from adt_ai.export_db.failures import ObjectExportFailure
from adt_ai.export_db.inventory import DatabaseObject, ObjectDiscovery
from adt_ai.export_db.job_signatures import JobSignatureStore, advance_job_signatures
from adt_ai.export_db.object_content import object_content
from adt_ai.export_db.watermarks import advance_watermark
from adt_ai.shared.config import is_enabled

if TYPE_CHECKING:
    from adt_ai.export_db.files import ObjectFileResolver
    from adt_ai.export_db.normalizers import NormalizerRegistry
    from adt_ai.export_db.render import ExportDbReporter
    from adt_ai.export_db.request import ExportDbRequest
    from adt_ai.export_db.timers import SegmentTimer

#: What `export_objects` yields per written object, and what it returns at the end.
ObjectContents = Generator[
    tuple[DatabaseObject, str, str | None], None, list[ObjectExportFailure]
]


def export_objects(
    request: ExportDbRequest,
    schema: str,
    database_objects: list[DatabaseObject],
    *,
    discovery: ObjectDiscovery,
    resolver: ObjectFileResolver,
    reporter: ExportDbReporter,
    registry: NormalizerRegistry,
    fatal_error: Callable[[Exception], bool],
    timer: SegmentTimer,
    overtaken_by: Mapping[str, str],
    failures: list[ObjectExportFailure],
) -> ObjectContents:
    """Yield each listed object's content, and return the ones refused (`#917`).

    A refusal is appended to `failures`, the run's own list that `run` raises
    from once every file is written, and the export goes on; only an error
    `fatal_error` names stops it. This schema's refusals are also the return
    value, because `stamp_schema` must know which objects never landed.
    """
    reports_objects = reporter.reports_objects
    add_if_not_exists = is_enabled(request.config.get("add_if_not_exists", True))
    keep_owner = is_enabled(request.config.get("keep_owner", False))
    keep_view_column_names = is_enabled(
        request.config.get("keep_view_column_names", False)
    )
    dropped_job_arguments: list[str] = []
    failures_before = len(failures)
    for index, database_object in enumerate(database_objects):
        if reports_objects:
            # A filename sitting in more than one place under the type
            # subtree is reported on the object's own row rather than
            # aborting the export: the run still finishes, and the user
            # sees which objects carry stale clones to clean up by hand.
            reporter.export_object(
                database_object,
                duplicates = [
                    resolver.display_path(location)
                    for location in resolver.duplicate_locations(database_object)
                ],
                changed_by = overtaken_by.get(database_object.name.upper()),
            )
        error: Exception | None = None
        try:
            raw_ddl = discovery.ddl(database_object)
        except Exception as caught:
            error = caught
        except BaseException:
            reporter.finish_object(failed=True)
            reporter.abort_export()
            raise
        # The row closes whether the pull came back or raised (`#232`);
        # `-compact`'s bar needs to know which, to close on `FAILED`.
        reporter.finish_object(failed=error is not None)
        if error is None:
            timer.record(database_object.object_type)
            try:
                content, fix_content = object_content(
                    request,
                    database_object,
                    raw_ddl,
                    registry               = registry,
                    resolver               = resolver,
                    discovery              = discovery,
                    add_if_not_exists      = add_if_not_exists,
                    keep_owner             = keep_owner,
                    keep_view_column_names = keep_view_column_names,
                    dropped_job_arguments  = dropped_job_arguments,
                )
            except Exception as caught:
                error = caught
        if error is not None:
            # One refused object no longer ends the export (`#917`):
            # recorded here, raised by `run` once the rest is written.
            if fatal_error(error):
                reporter.abort_export()
                raise error
            failures.append(ObjectExportFailure(database_object, error))
        else:
            yield database_object, content, fix_content
        next_object = (
            database_objects[index + 1]
            if index + 1 < len(database_objects)
            else None
        )
        if (
            reports_objects
            and (
                next_object is None
                or next_object.object_type != database_object.object_type
            )
        ):
            reporter.finish_type(schema, database_object.object_type)
    # Every object of this schema is written, so `-compact`'s bar has
    # nothing left to count: close it at 100% before the segment's TIMER.
    # One bar per schema, never a grand total across them, the same split
    # the shared per-schema section helper applies to every other output.
    reporter.finish_export(schema)
    reporter.objects_not_exported(schema, failures[failures_before:])
    reporter.job_arguments_not_exported(schema, dropped_job_arguments)
    return failures[failures_before:]


def stamp_schema(
    request: ExportDbRequest,
    schema: str,
    database_objects: list[DatabaseObject],
    refused: list[ObjectExportFailure],
    *,
    candidate: str | None,
    stored: str | None,
    narrowed: bool,
    job_signatures: Mapping[str, str] | None,
) -> None:
    """Advance the schema's `-recent` watermark and job baseline past what it wrote."""
    # **A measured run advances neither** (`#452`): both record what an
    # export WROTE, and this one wrote nothing, so stamping either would
    # make the next real `-recent` run skip what this one only read.
    if request.baseline:
        return
    # Refused objects do not hold the stamp back (`#917`): an hour of
    # written objects is recorded even when Oracle will describe one of
    # them to nobody. A schema where every object was refused wrote
    # nothing and is not stamped, so a privilege gap cannot make the
    # next `-recent` skip the lot. A schema that raised mid-export keeps
    # its old watermark while the finished ones keep theirs.
    if database_objects and len(refused) == len(database_objects):
        return
    advance_watermark(request, schema, candidate, stored, narrowed=narrowed)
    # Same placement and the same reason as the watermark above: the
    # baseline moves only once this schema's files are all written, so a
    # schema that raised mid-export re-offers its jobs on the next run
    # instead of recording a signature for a file that never landed.
    advance_job_signatures(
        request,
        schema,
        _written_job_signatures(request, schema, job_signatures, refused),
        narrowed = narrowed,
    )


def _written_job_signatures(
    request: ExportDbRequest,
    schema: str,
    signatures: Mapping[str, str] | None,
    refused: list[ObjectExportFailure],
) -> Mapping[str, str] | None:
    """This run's job signatures, a refused job's left where they were (`#923`).

    The export carries on past a refused job (`#917`), and its fresh signature
    used to be recorded all the same, so the next windowed run found the job
    unchanged and never offered it again. A refused job keeps whatever its last
    written export stored, or stays out of the store: forgetting it would retry
    a job whose file is current, and advancing it would never retry one whose
    file is not.
    """
    refused_jobs = {
        failure.database_object.name.upper()
        for failure in refused
        if failure.database_object.object_type.upper() == "JOB"
    }
    if not signatures or not refused_jobs or request.environment is None:
        return signatures
    kept = JobSignatureStore.load(request.root).get(request.environment, schema)
    written = {
        name: signature
        for name, signature in signatures.items()
        if name.upper() not in refused_jobs
    }
    written.update({name: kept[name] for name in sorted(refused_jobs) if name in kept})
    return written
