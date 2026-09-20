"""Fill the text mirrors `search TERM` reads, beside the dependency refresh (ADT #895).

APEX component source and static files, on `rebuild -app`. Always a full
replace of the application's rows, and of its workspace's files, because the
dictionary carries no per-component change stamp to be incremental against.
Stamped `("apex_source", <app id>)`, so `search` can tell a layer it never
filled from one that found nothing. A version 5 mirror's `app` stamps predate
the text, so they never stand in for it: the first refresh after the lift is a
first fill.

A schema's own source used to be copied here too, `USER_SOURCE` line for line
on every schema refresh. The files `export_db` writes already hold it, and
`search` reads those now (ADT #904), so nothing on the schema axis is mirrored.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from adt_ai.dependencies import apex_source, apex_static_files, queries
from adt_ai.dependencies.schema import APEX_SOURCE_SCOPE
from adt_ai.dependencies.write import insert_rows
from adt_ai.shared.db import QueryGateway


def refresh_app_source(
    connection: sqlite3.Connection,
    gateway: QueryGateway,
    app_id: int,
    columns: dict[str, set[str]] | None,
    *,
    progress: Any,
    refreshed_at: str,
) -> dict[str, set[str]]:
    """Replace one application's component source and static files, then stamp it.

    ``columns`` is the dictionary's column list from an earlier application of
    the same run, or None for the first one, which reads it; it is returned so
    the caller hands it to the next application and the run reads it once.
    """
    progress.begin("APEX_COMPONENT_SOURCE")
    try:
        if columns is None:
            columns = apex_source.discover_columns(gateway)
        components = apex_source.read_component_source(gateway, app_id, columns)
    except Exception:
        progress.fail("APEX_COMPONENT_SOURCE")
        raise
    with connection:
        connection.execute(queries.DELETE_APP_COMPONENT_SOURCE, (app_id,))
        insert_rows(connection, "APEX_COMPONENT_SOURCE", components)
    progress.finish("APEX_COMPONENT_SOURCE", len(components))

    progress.begin("APEX_STATIC_FILES")
    try:
        workspace, files = apex_static_files.read_static_files(gateway, app_id)
    except Exception:
        progress.fail("APEX_STATIC_FILES")
        raise
    with connection:
        connection.execute(queries.DELETE_APP_STATIC_FILES, (app_id,))
        connection.execute(queries.DELETE_WORKSPACE_STATIC_FILES, (workspace,))
        insert_rows(connection, "APEX_STATIC_FILES", files)
    progress.finish("APEX_STATIC_FILES", len(files))
    with connection:
        connection.execute(
            queries.REFRESH_UPSERT, (APEX_SOURCE_SCOPE, str(app_id), refreshed_at, None)
        )
    return columns


__all__ = ["refresh_app_source"]
