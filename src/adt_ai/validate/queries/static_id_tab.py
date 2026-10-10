"""The `validate -scan` read of every Static ID holding a Tab (ADT #1065).

APEX 26.2 refuses to save a component of 21 types while its Static ID holds a
Tab, and leaves the values already stored alone until somebody edits one (release
notes, Changed Behavior, "Static ID Changes"). This asks the dictionary for each
of the 21, one branch per view, measured against the 26.2.0 views on `SANDBOX`:

- application components, by `APPLICATION_ID`;
- workspace components (credentials and remote servers), by the workspace the
  application lives in, since that is where the App Builder edits them.

Credentials and Remote Print Server Credentials share one view and are not told
apart by it, so both read `Credentials`. A remote server is named by its type:
`Print Server` is the Remote Print Server, `REST Enabled SQL Service` keeps its
own name, and every other type is a Remote Server.

The Static ID comes back raw; the console spells its Tab `\\t`.
"""

from __future__ import annotations

#: One row per Tab-bearing Static ID: `COMPONENT_TYPE`, `STATIC_ID`.
STATIC_ID_TAB_QUERY = """
WITH application (workspace) AS (
    SELECT workspace FROM apex_applications WHERE application_id = :app_id
),
components (component_type, static_id) AS (
    SELECT 'ACL Role', role_static_id
    FROM   apex_appl_acl_roles WHERE application_id = :app_id
    UNION ALL
    SELECT 'Application Setting', static_id
    FROM   apex_application_settings WHERE application_id = :app_id
    UNION ALL
    SELECT 'Automation', static_id
    FROM   apex_appl_automations WHERE application_id = :app_id
    UNION ALL
    SELECT 'Data Load Definition', static_id
    FROM   apex_appl_data_loads WHERE application_id = :app_id
    UNION ALL
    SELECT 'Document Source', source_static_id
    FROM   apex_appl_json_sources WHERE application_id = :app_id
    UNION ALL
    SELECT 'Email Template', static_id
    FROM   apex_appl_email_templates WHERE application_id = :app_id
    UNION ALL
    SELECT 'Generative AI Agent', agent_static_id
    FROM   apex_appl_ai_agents WHERE application_id = :app_id
    UNION ALL
    SELECT 'Report Layout', static_id
    FROM   apex_application_rpt_layouts WHERE application_id = :app_id
    UNION ALL
    SELECT 'Report Query', static_id
    FROM   apex_application_rpt_queries WHERE application_id = :app_id
    UNION ALL
    SELECT 'REST Data Source', module_static_id
    FROM   apex_appl_web_src_modules WHERE application_id = :app_id
    UNION ALL
    SELECT 'REST Source Operation', operation_static_id
    FROM   apex_appl_web_src_operations WHERE application_id = :app_id
    UNION ALL
    SELECT 'REST Synchronization Step', static_id
    FROM   apex_appl_web_src_sync_steps WHERE application_id = :app_id
    UNION ALL
    SELECT 'Search Configuration', static_id
    FROM   apex_appl_search_configs WHERE application_id = :app_id
    UNION ALL
    SELECT 'Shortcut', static_id
    FROM   apex_application_shortcuts WHERE application_id = :app_id
    UNION ALL
    SELECT 'Task Definition', static_id
    FROM   apex_appl_taskdefs WHERE application_id = :app_id
    UNION ALL
    SELECT 'Text Message', static_id
    FROM   apex_application_translations WHERE application_id = :app_id
    UNION ALL
    SELECT 'Credentials', c.static_id
    FROM   apex_workspace_credentials c
    WHERE  c.workspace = (SELECT workspace FROM application)
    UNION ALL
    SELECT CASE s.remote_server_type
               WHEN 'Print Server'             THEN 'Remote Print Server'
               WHEN 'REST Enabled SQL Service' THEN 'REST Enabled SQL Service'
               ELSE 'Remote Server'
           END,
           s.remote_server_static_id
    FROM   apex_workspace_remote_servers s
    WHERE  s.workspace = (SELECT workspace FROM application)
)
SELECT component_type, static_id
FROM   components
WHERE  INSTR(static_id, CHR(9)) > 0
ORDER  BY component_type, static_id
"""
