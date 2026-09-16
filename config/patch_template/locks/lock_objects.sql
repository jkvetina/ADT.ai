--
-- OBJECT LOCKS, the CORE_LOCKS half
--
-- Reads :objects, the 'NAME:TYPE,' list the install script sets above the link.
-- Linked by patch -create when patch_core_locks is on.
--
-- Takes a CORE_LOCKS lock on every listed object that exists, which also runs
-- the CORE_LOCKS source hash check. Does nothing where CORE_LOCK is not
-- installed and valid, and every CORE_LOCKS call is dynamic, so this compiles
-- in a schema without it.
--
-- LOCK_TIME_ERROR (somebody else holds the object) and LOCK_HASH_ERROR (its
-- source moved) stop the deploy. Any other error only prints
-- -- OBJECT LOCK SKIPPED, so a CORE_LOCKS that cannot answer blocks nothing.
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
        BEGIN
            EXECUTE IMMEDIATE 'BEGIN core_lock.create_lock(USER, :t, :n); END;'
                USING c.object_type, c.object_name;
        EXCEPTION
        WHEN OTHERS THEN
            IF INSTR(SQLERRM, 'LOCK_TIME_ERROR') > 0
                OR INSTR(SQLERRM, 'LOCK_HASH_ERROR') > 0
            THEN
                RAISE;
            END IF;
            --
            DBMS_OUTPUT.PUT_LINE('-- OBJECT LOCK SKIPPED: ' || c.object_name || ' -- ' || SQLERRM);
        END;
    END LOOP;
END;
/
