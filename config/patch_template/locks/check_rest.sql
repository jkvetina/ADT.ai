--
-- REST MODULE LOCKS
--
-- Reads :rest_modules, the 'name,' list of ORDS modules the patch installs, and
-- :built_at, the UTC moment the patch was built, both set by the install script.
-- Linked by patch -create when patch_signatures is on and the patch carries a
-- REST module.
--
-- Refuses with ORA-20901 REST_MODULE_CHANGED when a listed module was changed
-- after the patch was built. A module never edited has no updated_on, so its
-- created_on is compared instead.
--
-- The list is split in PL/SQL, never inside the query, because SQL caps a
-- VARCHAR2 at 4000 bytes on most databases and the list can be longer.
--
DECLARE
    l_modules       APEX_T_VARCHAR2 := APEX_STRING.SPLIT(:rest_modules, ',');
BEGIN
    FOR c IN (
        SELECT
            a.name AS artifact
        FROM TABLE(l_modules) t
        JOIN user_ords_modules a
            ON  a.name          = t.column_value
        WHERE 1 = 1
            AND SYS_EXTRACT_UTC(FROM_TZ(CAST(NVL(a.updated_on, a.created_on) AS TIMESTAMP), TO_CHAR(SYSTIMESTAMP, 'TZH:TZM')))
                > TO_TIMESTAMP(:built_at, 'YYYY-MM-DD HH24:MI:SS')
    ) LOOP
        RAISE_APPLICATION_ERROR(-20901, 'REST_MODULE_CHANGED: ' || c.artifact
            || ' was changed after this patch was built, deploying it would overwrite work this patch never saw');
    END LOOP;
END;
/
