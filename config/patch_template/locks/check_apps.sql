--
-- APEX APPLICATION LOCKS
--
-- Reads :apex_apps, the 'id,' list of APEX applications the script installs by
-- SQL (a full export, split components, application static files), and
-- :built_at, the UTC moment the patch was built, both set by the install
-- script. Linked by patch -create when deploy_live_check is on, below
-- apex_init, and above the first component.
--
-- Refuses with ORA-20901 APP_CHANGED when a listed application, or any of its
-- pages, was changed after the patch was built, and names who changed it. A
-- deploy of a full or split export leaves the audit columns empty, so every
-- patch stamps its application after the install (the version written back
-- unchanged), which is what makes a colleague's deploy visible here.
--
-- An application the target does not hold yet has no row, and passes.
--
DECLARE
    l_apps          APEX_T_VARCHAR2 := APEX_STRING.SPLIT(:apex_apps, ',');
BEGIN
    FOR c IN (
        SELECT
            a.application_id    AS artifact,
            a.changed_by,
            TO_CHAR(a.changed_on, 'YYYY-MM-DD HH24:MI:SS') AS changed_on
        FROM TABLE(l_apps) t
        JOIN (
            SELECT application_id, last_updated_by AS changed_by, last_updated_on AS changed_on
            FROM apex_applications
            UNION ALL
            SELECT application_id, last_updated_by, last_updated_on
            FROM apex_application_pages
        ) a
            ON  TO_CHAR(a.application_id) = t.column_value
        WHERE 1 = 1
            AND SYS_EXTRACT_UTC(FROM_TZ(CAST(a.changed_on AS TIMESTAMP), TO_CHAR(SYSTIMESTAMP, 'TZH:TZM')))
                > TO_TIMESTAMP(:built_at, 'YYYY-MM-DD HH24:MI:SS')
        ORDER BY a.changed_on DESC
    ) LOOP
        RAISE_APPLICATION_ERROR(-20901, 'APP_CHANGED: ' || c.artifact
            || ' was changed by ' || NVL(c.changed_by, 'unknown') || ' on ' || c.changed_on
            || ' after this patch was built, deploying it would overwrite work this patch never saw');
    END LOOP;
END;
/
