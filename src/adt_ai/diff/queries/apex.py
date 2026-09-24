from __future__ import annotations

# `wwv_flow_files` answers nothing until a workspace is set, and a schema hosting
# no application has no `EXPORT_START_QUERY` to set one for it. The schema's own
# mapping answers which workspace that is: the configured `apex.workspace` when
# there is one, else the first by name. A schema mapped to none sets nothing, and
# the read after it answers no files, which is the truth about that schema.
# `diff -apex` runs it before reading a side's files, and `-restore` before
# writing the target's (`diff/pull_apex.py`).
WORKSPACE_START_QUERY = """
BEGIN
    FOR c IN (
        SELECT w.workspace
        FROM apex_workspace_schemas s
        JOIN apex_workspaces w
            ON w.workspace_id = s.workspace_id
        WHERE UPPER(s.schema) = UPPER(:owner)
            AND (UPPER(w.workspace) = UPPER(:workspace) OR :workspace IS NULL)
        ORDER BY w.workspace
        FETCH FIRST 1 ROW ONLY
    ) LOOP
        APEX_UTIL.SET_WORKSPACE (
            p_workspace => c.workspace
        );
    END LOOP;
END;
""".strip()
