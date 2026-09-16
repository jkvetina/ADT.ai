"""Every PL/SQL block `live_upload` sends, ported from old ADT verbatim."""

from __future__ import annotations

# Bind the session to the application's workspace, once per run. `WWV_FLOW_API`
# writes into whatever workspace the session carries, so a session with none
# raises instead of uploading, and a session carrying the wrong one would put
# the file in somebody else's application.
APEX_SECURITY_CONTEXT = """
BEGIN
    FOR c IN (
        SELECT a.workspace
        FROM apex_applications a
        WHERE a.application_id = :app_id
    ) LOOP
        APEX_UTIL.SET_WORKSPACE (
            p_workspace => c.workspace
        );
        APEX_UTIL.SET_SECURITY_GROUP_ID (
            p_security_group_id => APEX_UTIL.FIND_SECURITY_GROUP_ID(p_workspace => c.workspace)
        );
    END LOOP;
END;
"""

# Application static files: the `Shared Components > Static Application Files`
# list, addressed by the name the builder shows.
UPLOAD_APP_FILE = """
BEGIN
    WWV_FLOW_API.CREATE_APP_STATIC_FILE (
        p_flow_id       => :app_id,
        p_file_name     => :name,
        p_mime_type     => :mime,
        p_file_charset  => 'utf-8',
        p_file_content  => :payload
    );
END;
"""

# Workspace static files: the same list one level up, shared by every
# application in the workspace, so it takes no application id.
UPLOAD_WORKSPACE_FILE = """
BEGIN
    WWV_FLOW_API.CREATE_WORKSPACE_STATIC_FILE (
        p_file_name     => :name,
        p_mime_type     => :mime,
        p_file_charset  => 'utf-8',
        p_file_content  => :payload
    );
END;
"""
