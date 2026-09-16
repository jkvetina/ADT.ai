"""The shared lock scripts a patch links, and the names it links them by (ADT #850).

A patch lists the objects it will overwrite and asks the target about them
before writing anything, and releases what it took at the end. That has to
travel with the patch rather than live in ADT: a patch is run by hand in SQLcl,
or handed to a DBA, and the protection must hold there too.

Until ADT #850 the SQL was generated inline into every install script. It now
lives in six reusable scripts under `<patch_template_dir>/locks/`, shipped in
the reference scaffold, and the install script only sets its binds and links
the scripts its config asks for, the same `PROMPT -- TEMPLATE:` + `@` pair every
template uses. Linked, never copied, so a project that edits a lock script edits
it for every patch.

Placement is Jan's, 2026-09-02: *"I like 2 clean blocks (lock at the start,
unlock at the end) way more."* Checking everything up front is also the only
shape that cannot half-apply a patch: DDL does not roll back, so a per-object
guard refusing on object 10 of 10 leaves nine already overwritten.

## Two binds, and what reads them

`:objects` is one `'NAME:TYPE,'` row per guarded object, NAME first, a trailing
comma on every row, split on the target by `APEX_STRING.SPLIT`; the empty item
after the last comma matches no dictionary row. `:built_at` is the UTC moment
the patch was built, set only when a script that compares against it is linked.
The REST and workspace-file checks read their own name lists, `:rest_modules`
and `:ws_files`. The unlock at the bottom reads the `:objects` the top set, in
the same SQLcl session, so the list is written once.

## Where the hashing went, and the two clocks

Nowhere in ADT. Where CORE_LOCKS is installed, `lock_objects.sql` calls
`core_lock.create_lock`, which takes the lock AND compares the source. Where it
is not, `check_objects.sql` compares `user_objects.last_ddl_time`, resolved to
UTC on the server with `SYSTIMESTAMP`'s offset, against `:built_at`, which
`signatures.built_at` already answers in UTC (ADT #700).

CORE_LOCKS is used only where it is installed AND `patch_core_locks` is on;
everywhere else the `last_ddl_time` check runs (Jan, 2026-09-14). With both keys
on, `check_objects.sql` skips a schema holding a valid CORE_LOCK, the `ELSIF` the
inline block used to carry. With `patch_core_locks` off, `check_objects_all.sql`
is linked instead and checks every schema. Two scripts rather than a flag bind,
which Jan ruled out.
"""

from __future__ import annotations

# The folder under `patch_template_dir` the six scripts live in. Not a slot: no
# file in it is injected by folder, ADT links each one by name.
LOCKS_FOLDER = "locks"

LOCK_OBJECTS      = "lock_objects.sql"
CHECK_OBJECTS     = "check_objects.sql"
CHECK_OBJECTS_ALL = "check_objects_all.sql"
UNLOCK_OBJECTS    = "unlock_objects.sql"
CHECK_REST     = "check_rest.sql"
CHECK_FILES_WS = "check_files_ws.sql"

LOCK_HEADING   = "OBJECT LOCKS"
UNLOCK_HEADING = "OBJECT UNLOCK"

OBJECTS_BIND  = "objects"
BUILT_AT_BIND = "built_at"

# The widest a SQLcl VARCHAR2 bind is. A list that would not fit fails the build
# rather than being cut short, because a truncated list guards less than it says.
LIST_BIND_BYTES = 32767
LIST_BIND_TYPE = f"VARCHAR2({LIST_BIND_BYTES})"
BUILT_AT_BIND_TYPE = "VARCHAR2(19)"
LIST_SEPARATOR = ","

# Emitted in place of a link whose script the project does not have, so the
# install script says which guard it is missing and the build still completes.
MISSING_LOCK_FILE = "PROMPT -- LOCK FILE MISSING: {path}"

# The two schema-level artifacts `user_objects` never holds (ADT #724): the
# heading their block opens on, the bind their names go into, and the script
# that compares them. There is no lock half, CORE_LOCKS having nothing to say
# about either.
WORKSPACE_GUARDS: dict[str, dict[str, str]] = {
    "rest": {
        "heading" : "REST MODULE LOCKS",
        "bind"    : "rest_modules",
        "script"  : CHECK_REST,
    },
    "files_ws": {
        "heading" : "WORKSPACE FILE LOCKS",
        "bind"    : "ws_files",
        "script"  : CHECK_FILES_WS,
    },
}
