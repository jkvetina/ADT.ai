--
-- OBJECT LOCKS, the last_ddl_time half
--
-- Reads :objects, the 'NAME:TYPE,' list, and :built_at, the UTC moment the
-- patch was built ('YYYY-MM-DD HH24:MI:SS'), both set by the install script.
-- Linked by patch -create when patch_signatures is on.
--
-- Refuses with ORA-20901 OBJECT_CHANGED when a listed object was compiled after
-- the patch was built. The database reading is converted to UTC on the server,
-- with the offset in force at deploy time.
--
-- Checks only where CORE_LOCK is NOT installed and valid: where it is,
-- lock_objects.sql asks CORE_LOCKS instead, which compares the source itself.
--
-- The list is split in PL/SQL, never inside the query, because SQL caps a
-- VARCHAR2 at 4000 bytes on most databases and the list can be longer.
--
DECLARE
    l_objects       APEX_T_VARCHAR2 := APEX_STRING.SPLIT(:objects, ',');
BEGIN
    FOR c IN (
        SELECT
            o.object_type,
            o.object_name
        FROM TABLE(l_objects) t
        JOIN user_objects o
            ON  o.object_name   = SUBSTR(t.column_value, 1, INSTR(t.column_value, ':') - 1)
            AND o.object_type   = SUBSTR(t.column_value, INSTR(t.column_value, ':') + 1)
        WHERE 1 = 1
            AND SYS_EXTRACT_UTC(FROM_TZ(CAST(o.last_ddl_time AS TIMESTAMP), TO_CHAR(SYSTIMESTAMP, 'TZH:TZM')))
                > TO_TIMESTAMP(:built_at, 'YYYY-MM-DD HH24:MI:SS')
            AND NOT EXISTS (
                SELECT 1
                FROM user_objects x
                WHERE 1 = 1
                    AND x.object_name   = 'CORE_LOCK'
                    AND x.object_type   = 'PACKAGE BODY'
                    AND x.status        = 'VALID'
            )
    ) LOOP
        RAISE_APPLICATION_ERROR(-20901, 'OBJECT_CHANGED: ' || c.object_type || ' ' || c.object_name
            || ' was compiled after this patch was built, deploying it would overwrite work this patch never saw');
    END LOOP;
END;
/
