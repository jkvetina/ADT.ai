--
-- WORKSPACE FILE LOCKS
--
-- Reads :ws_files, the 'name,' list of workspace static files the patch installs,
-- and :built_at, the UTC moment the patch was built, both set by the install
-- script. Linked by patch -create when patch_signatures is on and the patch
-- carries a workspace file, below apex_init, because wwv_flow_files returns no
-- rows until a workspace is set.
--
-- Refuses with ORA-20901 WORKSPACE_FILE_CHANGED when a listed file was changed
-- after the patch was built. Only the slice export_apex -files_ws reads counts:
-- flow_id 0 and no content type.
--
-- The list is split in PL/SQL, never inside the query, because SQL caps a
-- VARCHAR2 at 4000 bytes on most databases and the list can be longer.
--
DECLARE
    l_files         APEX_T_VARCHAR2 := APEX_STRING.SPLIT(:ws_files, ',');
BEGIN
    FOR c IN (
        SELECT
            a.filename AS artifact
        FROM TABLE(l_files) t
        JOIN wwv_flow_files a
            ON  a.filename      = t.column_value
        WHERE 1 = 1
            AND a.flow_id       = 0
            AND a.content_type  IS NULL
            AND SYS_EXTRACT_UTC(FROM_TZ(CAST(NVL(a.updated_on, a.created_on) AS TIMESTAMP), TO_CHAR(SYSTIMESTAMP, 'TZH:TZM')))
                > TO_TIMESTAMP(:built_at, 'YYYY-MM-DD HH24:MI:SS')
    ) LOOP
        RAISE_APPLICATION_ERROR(-20901, 'WORKSPACE_FILE_CHANGED: ' || c.artifact
            || ' was changed after this patch was built, deploying it would overwrite work this patch never saw');
    END LOOP;
END;
/
