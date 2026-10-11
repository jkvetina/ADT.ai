"""SQL for `patch -deploy -app` over an `ords:` connection (ADT #1071).

The install settings that go ahead of the compiled blocks in the one request,
and the read after them that the application landed. See `patch/ords_import.py`.
"""

from __future__ import annotations

ORDS_INSTALL_SETTINGS_BLOCK = """
BEGIN
    FOR c IN (
        SELECT s.workspace_name
        FROM   apex_workspace_schemas s
        WHERE  UPPER(s.schema) = USER
        ORDER  BY s.workspace_name
    ) LOOP
        APEX_UTIL.SET_WORKSPACE (p_workspace => c.workspace_name);
        APEX_APPLICATION_INSTALL.SET_WORKSPACE (p_workspace => c.workspace_name);
        EXIT;
    END LOOP;
    APEX_APPLICATION_INSTALL.SET_APPLICATION_ID (p_application_id => :app_id);{alias}
    APEX_APPLICATION_INSTALL.SET_KEEP_SESSIONS (p_keep_sessions => TRUE);
END;
""".strip()

ORDS_INSTALL_ALIAS_LINE = (
    "\n    APEX_APPLICATION_INSTALL.SET_APPLICATION_ALIAS (p_application_alias => :alias);"
)

ORDS_INSTALLED_QUERY = """
SELECT COUNT(*) AS n
FROM   apex_applications a
WHERE  a.application_id = :app_id
""".strip()

__all__ = [
    "ORDS_INSTALLED_QUERY",
    "ORDS_INSTALL_ALIAS_LINE",
    "ORDS_INSTALL_SETTINGS_BLOCK",
]
