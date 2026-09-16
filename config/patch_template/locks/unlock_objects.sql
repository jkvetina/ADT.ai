--
-- OBJECT UNLOCK
--
-- Reads :objects, the same 'NAME:TYPE,' list the install script set at its top,
-- in the same SQLcl session. Linked by patch -create when patch_core_locks is on,
-- after every object is installed.
--
-- Releases the CORE_LOCKS lock on every listed object, and only a lock this
-- deploy's own user holds, so a colleague who took one mid-run keeps it. Does
-- nothing where CORE_LOCK is not installed and valid.
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
            AND EXISTS (
                SELECT 1
                FROM user_objects x
                WHERE 1 = 1
                    AND x.object_name   = 'CORE_LOCK'
                    AND x.object_type   = 'PACKAGE BODY'
                    AND x.status        = 'VALID'
            )
    ) LOOP
        EXECUTE IMMEDIATE 'BEGIN core_lock.unlock(in_locked_by => core_lock.get_user(), in_object_type => :t, in_object_name => :n); END;'
            USING c.object_type, c.object_name;
    END LOOP;
END;
/
