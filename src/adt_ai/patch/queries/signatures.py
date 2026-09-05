"""The two blocks a patch carries so a deploy cannot overwrite someone's work.

Both are generated SQL rather than anything ADT does over a connection, and that
is the requirement rather than a preference: a patch is handed over and run by
hand in SQLcl, or handed to a DBA, and the protection has to travel with it.
Nothing here needs ADT installed, and nothing here needs a grant.

Placement is Jan's, 2026-09-02: *"we should have a lock file at the start and
unlock file at the end of the patch (or maybe even better to expose this in the
main driving file so it is not buried) ... I like 2 clean blocks (lock at the
start, unlock at the end) way more."* So the whole guard sits at the top of the
install script, before the first object is written, and the release sits at the
bottom. Nothing is emitted above each individual `CREATE OR REPLACE`.

**Checking everything up front is also the only shape that cannot half-apply a
patch.** DDL does not roll back, so a per-object guard that refuses on object 10
of 10 leaves nine objects already overwritten and no way back.

## Where the hashing went

Nowhere in here, which is the point. Jan, 2026-09-02: *"If you have core_locks,
you use that to calculate the hash. If you dont have the core_locks, you
calculate the hash from user_objects view or any other oracle views so it does
not cost much ... (you can kind of rely on last_ddl_at column)"*, and *"core_locks
should cover signature check automatically."*

So the block asks the database two questions and does no hashing of its own:

  * CORE_LOCKS installed. `core_lock.create_lock` takes the lock AND runs the
    source-hash comparison it has always run, refusing a foreign live lock with
    `LOCK_TIME_ERROR` and a changed source with `LOCK_HASH_ERROR`. It is the
    precise answer and it is already written.
  * CORE_LOCKS absent. `user_objects.last_ddl_time` against the moment this
    patch was built. Approximate on purpose: a recompile moves it without
    changing a line, so the fallback refuses a re-deploy of the same patch. It
    costs one dictionary read and needs nothing installed.

**Every reference to CORE_LOCKS is dynamic.** A schema without it must still
compile these blocks, and a static reference to `core_lock` is a compile error
for the whole anonymous block, which would turn "CORE_LOCKS is not installed"
into "the patch will not run".

## The two clocks the drift branch compares, and the domain it compares in

That fallback reads two timestamps written by two machines, and ADT #700 is what
happens when nobody says so. `built_at` is a GIT author's instant, offset and
all; `user_objects.last_ddl_time` is a naive wall-clock reading taken on the
DATABASE server, an Oracle DATE carrying no zone. Comparing the digits of one
against the digits of the other is right only where the two machines happen to
share a zone: a build committed at 10:00 +02:00 is 08:00 UTC, and against a
compile at 08:30 UTC the naive comparison asked `08:30 > 10:00` and let the
patch through.

**UTC is the domain, and each side is converted by whoever knows its zone.** The
build side is resolved here, by :func:`adt_ai.patch.signatures.built_at`, which
answers a UTC instant and carries no offset into the SQL; the database side is
resolved on the target, by the `SYS_EXTRACT_UTC(FROM_TZ(...))` in
:data:`CLOCK_COLUMN`. Neither side guesses the other's zone, which is the whole
of the fix.

**The server's offset comes from `SYSTIMESTAMP`, read at deploy time.** Not
`SESSIONTIMEZONE`, which is whatever the DEPLOYER's SQLcl reports and would hand
the same defect back wearing a database-side spelling (:mod:`adt_ai.patch.clocks`
refused it for that reason on #394); and not an offset baked in at build time,
which a patch built in August and deployed in November would carry an hour wrong
through every row.

What that leaves is bounded and worth saying out loud: `SYSTIMESTAMP` reports the
offset in force at DEPLOY time, so an object compiled on the far side of a
daylight-saving change resolves one DST step out, up to an hour, in whichever
direction that change went. Closing it needs the server's zone REGION rather than
its current offset, and no SQL reads the host's region: `DBTIMEZONE` is the value
the database was created with and says nothing about the clock `SYSDATE` reads. A
server on UTC, which is most containers and most cloud instances, has no such
window at all.
"""

from __future__ import annotations

# One object of the patch, as a row of the cursor's own IN list. The whole list
# lives in the FOR loop's SELECT. Jan, 2026-09-02: *"I would like to see a whole
# list in the select in the for loop clause, make the code as short as possible"*.
OBJECT_ROW = "            ('{object_type}', '{object_name}')"

# Is CORE_LOCKS installed here? A scalar subquery rather than a declared variable,
# so the block needs no DECLARE section at all.
CORE_LOCKS_COLUMN = """,
            (SELECT COUNT(*) FROM user_objects x WHERE x.object_name = 'CORE_LOCK'
                AND x.object_type = 'PACKAGE BODY' AND x.status = 'VALID') AS core_locks#"""

# Take the lock, and let CORE_LOCKS answer the signature question on its way.
LOCK_BRANCH = """        IF c.core_locks# > 0 THEN
            EXECUTE IMMEDIATE 'BEGIN core_lock.create_lock(USER, :t, :n); END;'
                USING c.object_type, c.object_name;"""

# The dictionary's own reading, resolved to UTC on the machine that took it. Only
# the drift branch reads it, so it is selected only when that branch is emitted.
CLOCK_COLUMN = """,
            SYS_EXTRACT_UTC(FROM_TZ(CAST(o.last_ddl_time AS TIMESTAMP),
                TO_CHAR(SYSTIMESTAMP, 'TZH:TZM'))) AS changed_utc"""

# The cheap half, for a schema with no CORE_LOCKS to ask. `{keyword}` is `IF` when
# it stands alone and `ELSIF` when it follows the lock branch. `{built_at}` is a
# UTC instant, so both sides of the `>` are plain UTC timestamps and no session
# zone enters the comparison.
DRIFT_BRANCH = (
    """        {keyword} c.changed_utc"""
    """ > TO_TIMESTAMP('{built_at}', 'YYYY-MM-DD HH24:MI:SS') THEN
            RAISE_APPLICATION_ERROR(-20901, 'OBJECT_CHANGED: ' || c.object_type
                || ' ' || c.object_name || ' was compiled after this patch was built,'
                || ' deploying it would overwrite work this patch never saw');"""
)

LOCK_BLOCK = """
PROMPT --;
PROMPT -- OBJECT LOCKS
PROMPT --;
BEGIN
    FOR c IN (
        SELECT
            o.object_type,
            o.object_name{clock}{columns}
        FROM user_objects o
        WHERE (o.object_type, o.object_name) IN (
{rows}
        )
    ) LOOP
{guard}
        END IF;
    END LOOP;
END;
/
""".strip()

# The other half of the pair. It releases only what this patch locked: by name, so a
# lock a developer took on something else is untouched, and by owner, so a colleague
# who took one on a listed object mid-deploy keeps it. The EXISTS is what keeps a
# schema with no CORE_LOCKS out of the loop entirely.
UNLOCK_BLOCK = """
PROMPT --;
PROMPT -- OBJECT UNLOCK
PROMPT --;
BEGIN
    FOR c IN (
        SELECT
            o.object_type,
            o.object_name
        FROM user_objects o
        WHERE (o.object_type, o.object_name) IN (
{rows}
        )
            AND EXISTS (SELECT 1 FROM user_objects x WHERE x.object_name = 'CORE_LOCK'
                AND x.object_type = 'PACKAGE BODY' AND x.status = 'VALID')
    ) LOOP
        EXECUTE IMMEDIATE 'BEGIN core_lock.unlock(in_locked_by => core_lock.get_user(),'
            || ' in_object_type => :t, in_object_name => :n); END;'
            USING c.object_type, c.object_name;
    END LOOP;
END;
/
""".strip()
