# Patch Signatures and Locks

What a patch asks the target about before it overwrites anything, and how to turn each half of it off. Deploying the patch is on [patch_deploy.md](patch_deploy.md); the application-level check a `-deploy -app` runs is on [patch_verify.md](patch_verify.md).

<br>

## Why a patch asks first

You build a patch carrying ten objects and deploy it twenty minutes later. In between, a colleague compiles `APP_LEDGER` into the same DEV schema. Your patch ships the body it snapshotted at build time, so deploying it quietly reverts their work.

So a patch lists the objects it will overwrite and asks the target about them before writing anything. The top of the install script sets that list and links the scripts that ask:

```sql
PROMPT --;
PROMPT -- OBJECT LOCKS
PROMPT --;
VARIABLE objects  VARCHAR2(32767)
VARIABLE built_at VARCHAR2(19)
BEGIN
    :objects :=
        'APP_LEDGER:PACKAGE BODY,' ||
        'APP_REPORT_V:VIEW,';
    :built_at := '2026-09-14 08:00:00';
END;
/

PROMPT -- TEMPLATE: config/patch_template/locks/lock_objects.sql
@"./../../config/patch_template/locks/lock_objects.sql";

PROMPT -- TEMPLATE: config/patch_template/locks/check_objects.sql
@"./../../config/patch_template/locks/check_objects.sql";
```

`:objects` holds one `NAME:TYPE` row per object, each ending on a comma. `:built_at` is the moment the patch was built, in UTC. The bottom of the script opens `PROMPT -- OBJECT UNLOCK` and links `unlock_objects.sql`, which reads the same `:objects` in the same session.

Nothing sits above the individual `CREATE OR REPLACE` lines, deliberately. Oracle does not roll DDL back, so a guard refusing on the tenth object leaves nine already overwritten. Checking up front means a patch either runs or does not.

The scripts are plain SQL. You do not need ADT to get the protection, so a patch you hand to a DBA is guarded the same way.

<br>

### The shared lock scripts

The SQL itself lives in six scripts under `locks/` in your `patch_template_dir`, `config/patch_template/locks/` by default. ADT.ai ships them in its reference scaffold, and `doctor -init` copies them with the rest of that folder:

| Script                  | Linked when                                                | Reads                        |
| ----------------------- | ---------------------------------------------------------- | ---------------------------- |
| `lock_objects.sql`      | `patch_core_locks`                                         | `:objects`                   |
| `check_objects.sql`     | `patch_signatures` and `patch_core_locks`                  | `:objects`, `:built_at`      |
| `check_objects_all.sql` | `patch_signatures` without `patch_core_locks`              | `:objects`, `:built_at`      |
| `unlock_objects.sql`    | `patch_core_locks`, at the bottom                          | `:objects`                   |
| `check_rest.sql`        | `patch_signatures` and the patch carries a REST module     | `:rest_modules`, `:built_at` |
| `check_files_ws.sql`    | `patch_signatures` and the patch carries a workspace file  | `:ws_files`, `:built_at`     |

Each is **linked in place, never copied**, the way a template is ([patch_templates.md](patch_templates.md)). Edit one and every patch built afterwards runs your version. `patch_add_templates: False` does not switch them off; the two keys below do.

A script your project does not have is not linked. The install script says so where the link would be, and the build completes:

```text
PROMPT -- LOCK FILE MISSING: config/patch_template/locks/check_objects.sql
```

That guard then does not run, so copy the folder in rather than leaving the line there.

The scripts split each list with `APEX_STRING.SPLIT`, so the target needs APEX installed. A list longer than the 32767 bytes a `VARCHAR2` bind holds fails `-create` rather than being cut short, and so does a name containing a comma, which the split could not read back. Split such a patch into smaller ones.

<br>

### Which objects are guarded

The ones the database keeps a source for and a patch overwrites in place: `FUNCTION`, `PACKAGE`, `PACKAGE BODY`, `PROCEDURE`, `TRIGGER`, `TYPE`, `TYPE BODY` and `VIEW`.

A table is not one of them. A patch ships an ALTER helper for it ([patch_install.md](patch_install.md)) rather than overwriting it, and there is no source to compare. Same for sequences, synonyms and grants.

<br>

### CORE_LOCKS, when you have it

[CORE_LOCKS](https://github.com/jkvetina/CORE_LOCKS) hooks every DDL in a schema, keeps a hash of every object's source, and refuses a compile when somebody else holds a live lock.

Where it is installed, `lock_objects.sql` calls `core_lock.create_lock` for each object on the list. That takes the lock and runs the source comparison, so the patch does no hashing of its own. A colleague holding one stops the deploy before it writes:

```text
ORA-20990: LOCK_TIME_ERROR: OBJECT_LOCKED_BY `NOVAK` [10231]
```

An object whose source moved since the last lock is the other refusal, `LOCK_HASH_ERROR`. While your patch holds the locks, a colleague's compile is the one refused, which is the whole point on a shared DEV. The locks are released at the end rather than left to expire, so nobody waits out a deploy that already finished.

Both refusals stop the deploy. Any other `create_lock` error degrades to no lock and prints `-- OBJECT LOCK SKIPPED`, so a CORE_LOCKS that cannot answer does not block every patch on the schema.

That release only happens when the deploy reaches its own end. `create_lock` commits each row as it takes it, so a script that fails later exits before `unlock_objects.sql` runs, under the default `WHENEVER SQLERROR EXIT ROLLBACK`.

Those locks stay held until they expire on their own, twenty minutes by default (`g_lock_length` in CORE_LOCKS). The unlock releases only locks this deploy's own user holds, so a colleague who took one mid-run keeps it.

<br>

### Without CORE_LOCKS

A target that does not have it still gets the cheap half. `check_objects.sql` reads `user_objects.last_ddl_time` for each listed object and refuses when it is newer than the moment the patch was built:

```text
ORA-20901: OBJECT_CHANGED: PACKAGE BODY APP_LEDGER was compiled after this
patch was built, deploying it would overwrite work this patch never saw.
```

It costs one dictionary read and needs no grant, and it is approximate on purpose. A recompile moves `last_ddl_time` without changing a line, so this check also refuses a second run of a patch that already deployed. Re-export and rebuild, or turn it off for that run.

CORE_LOCKS is used only where it is installed **and** `patch_core_locks` is on; everywhere else this check runs. With both keys on, `check_objects.sql` skips a schema holding a valid `CORE_LOCK` package body, because the lock already compared the source. With `patch_core_locks: False` the patch links `check_objects_all.sql` instead, which checks every schema, CORE_LOCKS or not.

Every reference to `core_lock` is dynamic, so a schema without it compiles every object script unchanged.

#### The two clocks it compares

Those two timestamps are written by two different machines. The moment a patch was built is a git commit's own instant, carrying the author's offset; `last_ddl_time` is a wall-clock reading taken on the database server, an Oracle `DATE` with no zone on it at all.

Where the two sit in different zones, comparing the digits of one against the digits of the other is simply wrong. A build committed at 10:00 `+02:00` names 08:00 UTC, and an object compiled at 08:30 UTC is newer than it however the clock faces read.

So the comparison happens in UTC, and each side is converted by whoever knows its own zone. The patch carries the build moment as a UTC instant. The script resolves the server's reading on the server:

```sql
SYS_EXTRACT_UTC(FROM_TZ(CAST(o.last_ddl_time AS TIMESTAMP), TO_CHAR(SYSTIMESTAMP, 'TZH:TZM')))
```

`SYSTIMESTAMP` rather than `SESSIONTIMEZONE`, because the session's zone is whatever machine happens to be running the deploy and says nothing about the server the DDL time came off. It is read when the patch runs rather than when it was built, so a patch built in August and deployed in November is compared against November's offset.

One residue is worth knowing about. That offset is the one in force at deploy time, so an object compiled on the far side of a daylight saving change resolves up to an hour out.

Reading the exact offset for an older instant needs the server's zone *region*, and no SQL exposes the region `SYSDATE` reads (`DBTIMEZONE` is the value the database was created with, not the clock the server keeps). A server on UTC, which most containers and cloud instances are, has no such window.

<br>

### REST modules and workspace files

An ORDS module and a workspace static file belong to a schema rather than to an application, so neither is a row of `user_objects` and neither is covered by the application signature check ([patch_verify.md](patch_verify.md)). A patch carrying one gets its own block, on the same rule and the same clock:

```text
PROMPT -- REST MODULE LOCKS
PROMPT -- WORKSPACE FILE LOCKS
```

Each sets its own list, `:rest_modules` or `:ws_files`, one name per row, beside `:built_at`, and links `check_rest.sql` or `check_files_ws.sql`. Those read `updated_on` instead of `last_ddl_time`, off `user_ords_modules` for a module and off `wwv_flow_files` for a workspace file, which is the same view `export_apex -files_ws` reads its payloads out of. The refusal names the artifact:

```text
ORA-20901: REST_MODULE_CHANGED: report_api was changed after this patch was
built, deploying it would overwrite work this patch never saw
```

A file created and never edited carries no `updated_on` at all, so the comparison falls back to `created_on` rather than letting that row through. There is no CORE_LOCKS half here: it hashes source it reads out of `user_objects`, and neither artifact is there.

One placement note. The workspace-file block sits below your `apex_init` template rather than at the very top, because `wwv_flow_files` returns nothing until a workspace is set and that is where the script sets one. It is still above every file the patch installs.

`__enable_schema.sql` is not guarded. It carries the roles and privileges no single module owns, so there is no module row named after it. An application's own static files are not guarded here either, being covered by that application's signature.

<br>

### Turning it off

Two keys in `config.yaml`, both on by default:

```yaml
patch_signatures        : True
patch_core_locks        : True
```

Separate, because the halves are: `patch_core_locks` links the lock and the unlock, `patch_signatures` links the `last_ddl_time` check for a target with no CORE_LOCKS. Both off and no block is written at all. `patch_signatures` owns the REST and workspace-file checks too, being the same comparison over a different table; `patch_core_locks` does not reach them.

This is not `-hash` mode, which picks which files a patch carries by comparing your working tree against a recorded baseline ([patch_hash.md](patch_hash.md)) and never asks the database. This rides whatever patch you built.
