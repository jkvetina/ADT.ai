# Templates and Per-Patch Scripts (adtai patch)

The project SQL a generated install script wraps around the object files: the session defaults, the reusable templates linked in at fixed slots, the one-off scripts moved into the patch, the emitted APEX environment, and the generated helpers. What goes into a patch is on [patch_install.md](patch_install.md).

<br>

## Templates

Every generated patch opens with the session defaults, before anything a project configures:

```text
SET DEFINE OFF
SET TIMING OFF
SET SQLBLANKLINES ON
```

`SET DEFINE OFF` is the one that matters. SQLcl reads `&` as a substitution prompt, so a package body holding a literal `'&APP_ID.'` stops a deploy dead with `Substitution cancelled` when no terminal is attached. These three are emitted whether or not the project has a template folder.

On top of that, `-create` injects the project's own reusable SQL at fixed slots, read from `patch_template_dir` (default `config/patch_template/`) relative to the project root:

| Folder            | Runs                                                              |
| ----------------- | ----------------------------------------------------------------- |
| `db_init/`        | first, in every database patch                                    |
| `<group>_before/` | before the object files of that `patch_map` group                 |
| `<group>_after/`  | after the object files of that `patch_map` group                  |
| `db_end/`         | last, in every database patch                                     |
| `apex_init/`      | first, in every APEX patch; an APEXlang application's `init` half |
| `apex_end/`       | last, in every APEX patch; an APEXlang application's `end` half   |

Files within a slot are injected in filename order, which is what the numeric prefixes in the shipped scaffold are for. `patch_add_templates: False` turns the mechanism off. An APEXlang application is two scripts around the import `patch -deploy -app` runs, `<SCHEMA>.<APP>.init.sql` and `<SCHEMA>.<APP>.end.sql`, so `apex_init/` runs before the tree lands and `apex_end/` after it ([patch_import.md](patch_import.md)).

A template is **linked in place, never copied**, so the install script names which template shipped instead of absorbing its body:

```text
PROMPT -- TEMPLATE: config/patch_template/db_init/00_init.sql
@"./../../config/patch_template/db_init/00_init.sql";
```

Two consequences worth knowing before you rely on either:

- **No token in a template body is ever substituted.** A template's bytes reach SQLcl exactly as they sit on disk. Those tokens still resolve in config *paths*, which is a different mechanism.
- **The patch folder is no longer self-contained if you move it out of the repository.** Committed, cloned and deployed in place the relative link resolves; carried off on its own the linked templates do not travel with it.

ADT.ai ships a reference scaffold in its own checkout. It is not read from there: copy it into your project and edit it. Read `db_end/` before you keep it, since those files refresh every materialized view, gather schema stats and run every enabled daily job.

One folder beside the slots is not a slot. `locks/` holds the six shared scripts that guard a patch against overwriting a colleague's work, and nothing in it is injected by folder: ADT links each one by name, under `patch_core_locks` and `patch_signatures` rather than `patch_add_templates` ([patch_signatures.md](patch_signatures.md)).

<br>

## Per-patch scripts move into the patch

A template is per-project config every patch reuses. A script in `patch_scripts_dir/<CODE>/` is the opposite: it was written for one patch code and has no life after that patch ships. So `-create` **moves** it, and the patch becomes the whole record of the change:

```text
PROMPT -- SCRIPT: patch_scripts/REPORTING/tables_after/00_fix.sql
@"./patch_scripts/tables_after/00_fix.sql";
```

Three things follow, and they are the whole reason the move is safe:

- **Each statement is hardened on the way in.** A bare `ALTER TABLE ... ADD note` fails with `ORA-01430` the second time it meets a database that already has the column, and that stops the whole install script. Every `CREATE`, `ALTER` and `DROP` is rewritten into an existence-checked PL/SQL block, and `--` comment lines become `PROMPT`s so they reach the deploy log. Anything else passes through untouched: making DML idempotent is yours to decide.
- **A re-create recovers what the first one moved.** The second `-create` finds the source folder emptied and carries the scripts forward out of the patch folder. A script you have since re-edited wins over the recovered copy, and hardening is idempotent.
- **Only what this patch uses moves.** A script no selected commit touched stays where it is, reported under `WARNING - NOT COMMITTED SCRIPTS, IGNORED:`. One in a slot no `patch_map` group can produce stays too, under `WARNING - UNKNOWN SCRIPTS:`. The filter runs before the move, so it cannot see what the folder already holds: `-force` empties it, and without one a re-create adds to the pile and the install script links all of it, so a folder first built from a wide commit range keeps shipping that range's generated `ALTER TABLE` helpers. Clearing loses nothing hand-written, which goes back to `patch_scripts/<CODE>/<slot>/` and faces the filter again.

A `name.[ENV].sql` script moves like any other but is linked only under its own `-target`.

<br>

## The APEX environment is emitted, not templated

Because nothing in a body is substituted, the two values an APEX patch needs are generated into the install script with real values:

```text
PROMPT -- APEX ENVIRONMENT
BEGIN
    APEX_UTIL.SET_WORKSPACE (
        p_workspace => 'MY_APP_WS'
    );

    -- keep sessions alive
    APEX_APPLICATION_INSTALL.SET_KEEP_SESSIONS(p_keep_sessions => TRUE);
    COMMIT;
END;
/
```

The workspace comes from the application's entry in `config/internal/apex.db`, the cache `export_apex` writes, so `-create` still connects to nothing. An application that cache has never recorded gets no block at all: a guessed or blank workspace fails the deploy at the first APEX call.

The block lands after the session defaults and before `apex_init/`, so a template of yours still overrides it.

`patch_apex_build_status` names a build status per target environment and is empty by default:

```yaml
patch_apex_build_status:
  PROD: RUN_ONLY
```

On a matching `-target` the install script closes with `APEX_UTIL.SET_APP_BUILD_STATUS`; on any other target nothing is emitted. Locking an application is never a tool default.

<br>

## APEX examples you switch on

Three things old ADT did to an application through config keys ship in the scaffold as examples instead. Each is a PL/SQL block inside `/* */`, so none of them runs until you delete its `/*` and `*/` lines:

| Example                                         | File                    | Call                                               |
| ----------------------------------------------- | ----------------------- | -------------------------------------------------- |
| install or upgrade supporting objects on import | `apex_init/00_init.sql` | `APEX_APPLICATION_INSTALL.SET_AUTO_INSTALL_SUP_OBJ` |
| switch the authentication scheme                | `apex_end/00_end.sql`   | `APEX_APPLICATION_ADMIN.SET_AUTHENTICATION_SCHEME`  |
| set the application version                     | `apex_end/00_end.sql`   | `APEX_APPLICATION_ADMIN.SET_APPLICATION_VERSION`    |

- **Supporting objects are an install setting**, read by the import that runs after `apex_init/`.
- **The scheme and the version change the installed application**, so they run in `apex_end/`, after the import.
- **Both `apex_end/` blocks name their applications in `IN (...)`**, because nothing in a template is substituted. An id the workspace does not have is skipped, and a sandbox id from `-deploy -app <id>` changes only if you list it.
- **A setting for one environment only** goes into its own `name.[ENV].sql` file.

<br>

## Helpers create generates for you

Two kinds of one-off are written into `patch_scripts_dir` (default `patch_scripts/{$PATCH_CODE}/`) and then move into the patch like any other script:

| Written to       | When                                                                          |
| ---------------- | ----------------------------------------------------------------------------- |
| `objects_after/` | the patch window **deleted** an object file, as a `drop.<type>.<name>.sql` |
| `tables_after/`  | a table file that changed, as the `ALTER TABLE` Oracle itself writes, per version step; a sequence file that changed, as its `ALTER SEQUENCE` |

The DROP helper is written for any object your `path_objects` layout resolves, and it runs on deploy, so review it first and delete it if the deletion was a repository-side move rather than a real drop.

A type listed in `immutables`, `TABLE` and `SEQUENCE` as shipped, is the exception: its helper is linked commented out and never runs. Uncomment that line in the patch script when the drop is real.

The type and the name come out of `object_types`, whole. Where two types share a folder, the longest configured extension a file ends with owns it, so `packages/core.spec.sql` is `PACKAGE CORE` and `packages/core.sql` is `PACKAGE BODY CORE`. Stripping only the last suffix would leave a name that is not an Oracle identifier at all.

The ALTER helper compares each version of a table file against the one before it, including the version standing before the patch opens, which is read from the parent of the first selected commit that touches the file. A table the window **creates** earns no ALTER: the `CREATE TABLE` shipping in the patch is the whole statement needed.

A sequence is compared by ADT.ai itself, clause by clause, since no database is needed to read one statement. Every clause `ALTER SEQUENCE` can change that differs is written, and a clause the new version no longer states goes back to Oracle's default, which is why the export left it out.

`START WITH` is never compared: the export strips it and `ALTER` cannot set it. The helper is written only while `SEQUENCE` is in `immutables`; otherwise the patch runs the sequence's `CREATE` as before.

<br>

### Oracle writes the ALTER, not ADT.ai

`-create` builds the two versions as `<NAME>$1` and `<NAME>$2` in the schema `-target` points at, hands them to `DBMS_METADATA.GET_SXML` and `DBMS_METADATA_DIFF`, and ships what `ALTERXML` answers.

That is why `-create` opens a connection at all, and it opens one only when a table in that schema has two versions to compare. Both shadow tables are dropped before they are built and again afterwards; `patch -deploy` sweeps any that a lost connection left behind.

So the coverage is Oracle's own: columns added, dropped and retyped, `NOT NULL` and `DEFAULT`, and `PRIMARY KEY`, `UNIQUE`, `FOREIGN KEY` and `CHECK` added or dropped. Three things are worth knowing before you read a helper:

- **A renamed column is a drop and an add.** The comparison matches columns by name, so the data in the old column does not survive the patch. Write the `RENAME COLUMN` into `patch_scripts/` yourself when you need the rows kept.
- **An index is not part of a table.** Indexes are their own object files under `path_objects`, so adding or removing one travels the ordinary object path and never appears in an ALTER helper.
- **A `DEFAULT` cannot be removed by the comparison.** Oracle answers `ORA-39267: Cannot remove default from table column.` and no statement; ADT.ai ships that sentence as a comment, so it reaches your deploy log where the missing statement would have run.

A generated ALTER runs **ahead of** its own table file in the patch script, because a table with an ALTER already exists on the target: its exported file then contributes a no-op `CREATE TABLE IF NOT EXISTS` plus `COMMENT ON COLUMN` lines describing the shape the ALTER just produced. Hand-written scripts you put in `tables_after/` still run after the files.

A table file without `IF NOT EXISTS` is linked commented out instead, because running it on a target that holds the table would stop the deploy on `ORA-00955`.

If the target database refuses one of the two versions, no comparison happens and `-create` says so under `WARNING - NO TABLE DIFF:`, with Oracle's own error under the file. The patch still builds; the table simply carries no ALTER, and that is the one case where a green deploy would otherwise change nothing.

One refusal is repaired rather than reported: the earlier version, the one your target already stands at. When Oracle refuses it, `-create` rebuilds it from the columns and constraints it declares and tries once more, comparing only if Oracle builds the rebuilt statement. Otherwise the warning quotes Oracle about the file as committed.

The rebuild leaves out comments, the empty items an extra comma leaves, and everything after the column list, such as a partition clause. A line that starts a new column or constraint starts a new item even when the comma before it is missing. Your patch's own version is never rebuilt, because it deploys exactly as committed.

Whitespace inside SQL string literals is part of the value. Generated ADD and MODIFY statements preserve it, including quoted defaults; changing only that whitespace still produces a column change.

The selected commits decide, not the current working tree. A file some later commit deleted, outside the window you patched, gets no DROP helper, and neither does an object the window both added and deleted.
