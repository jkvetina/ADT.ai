"""The file text for one pulled object (`#917`).

Lifted out of the runner's object loop so a failure here is recorded exactly as
a refused DDL pull is: a normalizer that trips on one object, or a job whose
arguments cannot be read, must not cost the thousands of objects behind it.
It is its own module rather than a runner method because `runner.py` sits
against the context-size guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adt_ai.export_db.content import (
    _append_comments,
    _append_job_arguments,
    _has_column_comments,
    _has_comments,
    _ignored_comment_columns,
    _job_arguments_dropped,
)
from adt_ai.export_db.inventory import DatabaseObject, ObjectDiscovery
from adt_ai.export_db.normalizers import (
    NormalizerRegistry,
    build_table_fix_sql,
    normalize_ddl,
)

if TYPE_CHECKING:
    from adt_ai.export_db.files import ObjectFileResolver
    from adt_ai.export_db.request import ExportDbRequest


def object_content(
    request: ExportDbRequest,
    database_object: DatabaseObject,
    raw_ddl: str,
    *,
    registry: NormalizerRegistry,
    resolver: ObjectFileResolver,
    discovery: ObjectDiscovery,
    add_if_not_exists: bool,
    keep_owner: bool,
    keep_view_column_names: bool,
    dropped_job_arguments: list[str],
) -> tuple[str, str | None]:
    """The object's file text, and its `.fix.sql` companion or None."""
    # Whatever casing the object's file already carries, resolved HERE so it is
    # part of the content itself: `-baseline` hashes this same string and must
    # agree with what a real run writes (`#452`). Recasing after the write would
    # leave the two modes measuring different bytes for the same object.
    display_name = resolver.file_object_name(database_object)
    content = normalize_ddl(
        raw_ddl,
        object_type         = database_object.object_type,
        object_name         = database_object.name,
        registry            = registry,
        add_if_not_exists   = add_if_not_exists,
        keep_owner          = keep_owner,
        keep_view_column_names = keep_view_column_names,
        object_display_name = display_name,
        table_retention     = discovery.table_retention(database_object),
    )
    fix_content = (
        build_table_fix_sql(raw_ddl, database_object.name, display_name)
        if database_object.object_type == "TABLE"
        else None
    )
    if database_object.object_type == "JOB":
        arguments = discovery.job_arguments(database_object)
        if _job_arguments_dropped(content, arguments):
            dropped_job_arguments.append(database_object.name)
        content = _append_job_arguments(content, arguments)
    content = _append_comments(
        content,
        database_object,
        discovery.comments(database_object)
        if _has_comments(database_object.object_type, request.config)
        else [],
        include_columns = _has_column_comments(
            database_object.object_type,
            request.config,
        ),
        ignored_columns = _ignored_comment_columns(request.config),
        object_display_name = display_name,
    )
    return content, fix_content


__all__ = ["object_content"]
