"""The SQL behind the two text mirrors `search TERM` reads (ADT #895).

The APEX component source and the static files are read by `rebuild -app`. The
live Oracle SELECTs and the SQLite deletes that make room for their rows sit
together here because they answer one writer, `dependencies/source_mirror.py`.
A schema's own source is not mirrored at all: `search` reads it from the files
`export_db` wrote (ADT #904).

Every Oracle read here runs on 19c: no `VALUES` constructor and no SQL
`UNPIVOT`, which refuses CLOB columns.
"""

from __future__ import annotations

# ------------------------------------------------------ APEX component source

# The owner the APEX dictionary views resolve to. `MAX` because an upgraded
# instance keeps the previous release's schema beside the current one, and the
# zero-padded names (`APEX_240200`, `APEX_260100`) sort by release.
_APEX_OWNER_SUBQUERY = (
    "(SELECT MAX(owner) FROM all_views WHERE view_name = 'APEX_APPLICATION_PAGES')"
)


def apex_source_columns_query(views: tuple[str, ...]) -> str:
    """Every column of ``views`` on this APEX release, read once per run.

    The names are the reader's own constants, never user input, so they are
    inlined rather than bound: an `IN` list of binds would need one per view.
    """
    names = ", ".join(f"'{view}'" for view in views)
    return (
        "SELECT table_name, column_name\n"
        "FROM all_tab_columns\n"
        f"WHERE owner = {_APEX_OWNER_SUBQUERY}\n"
        f"  AND table_name IN ({names})"
    )


def apex_source_view_query(view: str, columns: tuple[str, ...]) -> str:
    """One view's key and text columns for one application."""
    return f"SELECT {', '.join(columns)}\nFROM {view}\nWHERE application_id = :app_id"


# --------------------------------------------------------------- static files

APEX_APPLICATION_WORKSPACE_QUERY = """
SELECT workspace
FROM apex_applications
WHERE application_id = :app_id
""".strip()

APEX_APP_STATIC_FILES_QUERY = """
SELECT file_name, mime_type, file_charset, file_content
FROM apex_application_static_files
WHERE application_id = :app_id
""".strip()

APEX_PLUGIN_FILES_QUERY = """
SELECT plugin_name, file_name, mime_type, file_charset, file_content
FROM apex_appl_plugin_files
WHERE application_id = :app_id
""".strip()

APEX_WORKSPACE_STATIC_FILES_QUERY = """
SELECT file_name, mime_type, file_charset, file_content
FROM apex_workspace_static_files
WHERE workspace = :workspace
""".strip()

# ------------------------------------------------------------- SQLite writes

DELETE_APP_COMPONENT_SOURCE = 'DELETE FROM APEX_COMPONENT_SOURCE WHERE "APPLICATION_ID" = ?'

# Version 6 to 7 (ADT #901): `COMPONENT_ID` turns TEXT, and SQLite cannot change
# a column's type in place. The table is a full replace per application anyway,
# so it is dropped for the schema script to recreate, and its `apex_source`
# stamps go with it: `search` then names the layer until `rebuild -app` refills
# it, rather than reading an empty table as a search that found nothing.
MIRROR_LIFT_6_SCRIPT = """
DROP TABLE IF EXISTS APEX_COMPONENT_SOURCE;
DELETE FROM refreshes WHERE scope_type = 'apex_source';
""".strip()

# Version 7 to 8 (ADT #904): `USER_SOURCE` was a line-for-line copy of what the
# exported object files already hold, and `search` reads those now. The table
# goes, and so do the `source` stamps that said it was filled; every other
# table and stamp stays.
MIRROR_LIFT_7_SCRIPT = """
DROP TABLE IF EXISTS USER_SOURCE;
DELETE FROM refreshes WHERE scope_type = 'source';
""".strip()

# The application's own files, both scopes it owns; its workspace's files go
# with the statement below, keyed by the workspace rather than the app.
DELETE_APP_STATIC_FILES = (
    "DELETE FROM APEX_STATIC_FILES WHERE \"SCOPE\" IN ('APP', 'PLUGIN') AND \"APPLICATION_ID\" = ?"
)

DELETE_WORKSPACE_STATIC_FILES = (
    "DELETE FROM APEX_STATIC_FILES WHERE \"SCOPE\" = 'WORKSPACE' AND \"WORKSPACE\" = ?"
)
