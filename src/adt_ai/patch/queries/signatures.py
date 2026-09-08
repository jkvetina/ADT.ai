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
#
# The handler is ADT #730, and the distinction it draws is the whole of it. A
# refusal is CORE_LOCKS doing its job -- `LOCK_TIME_ERROR` for an object somebody
# else holds, `LOCK_HASH_ERROR` for one whose source moved since they took it --
# and those still stop the deploy, because letting them through is the overwrite
# this block exists to prevent. Anything else is CORE_LOCKS itself failing to
# answer, and an advisory lock that cannot be taken is not a reason to abandon a
# release: it degrades to the same "no lock" a schema without CORE_LOCKS gets.
#
# Measured 2026-09-06 on a schema whose vendored CORE_LOCK predated `create_lock`
# being callable outside a DDL trigger: `get_object` read `ora_sql_txt`, which
# carries a statement in trigger context only, so every object raised ORA-06502
# and `WHENEVER SQLERROR EXIT ROLLBACK` took the whole patch down. That schema
# could not deploy ANY patch, the one carrying the corrected package included,
# because this block runs before the objects it protects. A guard that cannot be
# bypassed by fixing the thing it depends on is a bootstrap trap, not a guard.
LOCK_BRANCH = """        IF c.core_locks# > 0 THEN
            BEGIN
                EXECUTE IMMEDIATE 'BEGIN core_lock.create_lock(USER, :t, :n); END;'
                    USING c.object_type, c.object_name;
            EXCEPTION
            WHEN OTHERS THEN
                IF INSTR(SQLERRM, 'LOCK_TIME_ERROR')
                    + INSTR(SQLERRM, 'LOCK_HASH_ERROR') > 0 THEN RAISE; END IF;
                DBMS_OUTPUT.PUT_LINE('-- OBJECT LOCK SKIPPED: '
                    || c.object_name || ' -- ' || SQLERRM);
            END;"""

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

# One artifact of the patch, as a row of the cursor's own IN list. Its own row
# rather than the pair `OBJECT_ROW` writes: a workspace artifact is identified by
# a single name, there being no type column to pair it with.
WORKSPACE_ROW = "            '{name}'"

# The same guard, over the two dictionaries `user_objects` does not cover.
#
# `-rest` and `-files_ws` export artifacts that belong to a SCHEMA rather than to
# an application, which is what leaves them unguarded from both ends: the object
# block above walks `user_objects`, where neither appears, and `#592`'s checksum
# gate walks an application, which neither belongs to. So a REST handler edited
# in the Builder while a patch was in flight was overwritten silently.
#
# There is no lock branch and no CORE_LOCKS half. CORE_LOCKS hashes source it
# reads out of `user_objects`, so it has nothing to say about either artifact,
# and the drift comparison is the whole of the block.
#
# `{clock}` is the card's own `updated_on`, wrapped in the same
# `SYS_EXTRACT_UTC(FROM_TZ(...))` the object block uses and for the same reason
# (see §The two clocks above): both readings are the DATABASE server's naive wall
# clock, and `built_at` answers in UTC.
WORKSPACE_LOCK_BLOCK = """
PROMPT --;
PROMPT -- {heading}
PROMPT --;
BEGIN
    FOR c IN (
        SELECT a.{name_column} AS artifact
        FROM {view} a
        WHERE a.{name_column} IN (
{rows}
        ){scope}
            AND SYS_EXTRACT_UTC(FROM_TZ(CAST({clock} AS TIMESTAMP),
                TO_CHAR(SYSTIMESTAMP, 'TZH:TZM')))
                > TO_TIMESTAMP('{built_at}', 'YYYY-MM-DD HH24:MI:SS')
    ) LOOP
        RAISE_APPLICATION_ERROR(-20901, '{code}: ' || c.artifact
            || ' was changed after this patch was built, deploying it would'
            || ' overwrite work this patch never saw');
    END LOOP;
END;
/
""".strip()

# Where each kind lives on the target, and which column answers "did anybody move
# this?". Every value here was read off SANDBOX (Oracle 26ai, APEX 26.1, ORDS) on
# 2026-09-07 rather than recalled, the rule `#473` was filed on:
#
#   * `USER_ORDS_MODULES` carries `NAME`, `CREATED_ON` and `UPDATED_ON`, and ORDS
#     stamps both dates when it defines a module. The `NVL` is therefore only ever
#     reached on a row some other ORDS version left blank, and it is there because
#     a NULL compares FALSE: without it the guard would fail OPEN on exactly the
#     row it knows least about.
#   * `WWV_FLOW_FILES` carries `FILENAME` and `UPDATED_ON`, and is the same view
#     `-files_ws` reads its payloads out of (`APEX_FILES_QUERY`). Asking the
#     export's own source is what keeps the two halves from disagreeing about
#     which row an exported file came from.
#
# The scope repeats that query's slice for the same reason: `flow_id = 0` with no
# content type is what `-files_ws` wrote, so a row outside it is not something
# this patch overwrites and refusing on one would be a false refusal.
#
# Both views are referenced statically. Neither is optional the way CORE_LOCKS is:
# a patch carrying a REST module runs `ORDS.DEFINE_MODULE`, and one carrying a
# workspace file runs `wwv_flow_imp_shared.create_app_static_file`, so a target
# missing either dictionary cannot install that patch whatever this block does.
WORKSPACE_GUARDS: dict[str, dict[str, str]] = {
    "rest": {
        "heading"     : "REST MODULE LOCKS",
        "view"        : "user_ords_modules",
        "name_column" : "name",
        "clock"       : "NVL(a.updated_on, a.created_on)",
        "scope"       : "",
        "code"        : "REST_MODULE_CHANGED",
    },
    "files_ws": {
        "heading"     : "WORKSPACE FILE LOCKS",
        "view"        : "wwv_flow_files",
        "name_column" : "filename",
        "clock"       : "NVL(a.updated_on, a.created_on)",
        "scope"       : "\n            AND a.flow_id = 0"
                        "\n            AND a.content_type IS NULL",
        "code"        : "WORKSPACE_FILE_CHANGED",
    },
}
