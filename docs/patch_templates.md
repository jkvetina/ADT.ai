# Templates and Per-Patch Scripts (adtai patch)

The project SQL a generated install script wraps around the object files: the session defaults, the reusable templates linked in at fixed slots, the one-off scripts moved into the patch, the emitted APEX environment, and the generated helpers. What goes into a patch is on [patch_install.md](patch_install.md).

## Templates

Every generated patch opens with the session defaults, before anything a project configures:

```text
SET DEFINE OFF
SET TIMING OFF
SET SQLBLANKLINES ON
```

`SET DEFINE OFF` is the one that matters. SQLcl reads `&` as a substitution prompt, so a package body holding a literal `'&APP_ID.'` stops a deploy dead with `Substitution cancelled` when no terminal is attached. These three are emitted whether or not the project has a template folder.

On top of that, `-create` injects the project's own reusable SQL at fixed slots, read from `patch_template_dir` (default `config/patch_template/`) relative to the project root:

| Folder            | Runs                                              |
| ----------------- | ------------------------------------------------- |
| `db_init/`        | first, in every database patch                    |
| `<group>_before/` | before the object files of that `patch_map` group |
| `<group>_after/`  | after the object files of that `patch_map` group  |
| `db_end/`         | last, in every database patch                     |
| `apex_init/`      | first, in every APEX patch                        |
| `apex_end/`       | last, in every APEX patch                         |

Files within a slot are injected in filename order, which is what the numeric prefixes in the shipped scaffold are for. `patch_add_templates: False` turns the mechanism off.

A template is **linked in place, never copied**, so the install script names which template shipped instead of absorbing its body:

```text
PROMPT -- TEMPLATE: config/patch_template/db_init/00_init.sql
@"./../../config/patch_template/db_init/00_init.sql";
```

Two consequences worth knowing before you rely on either:

- **No token in a template body is ever substituted.** A template's bytes reach SQLcl exactly as they sit on disk. Those tokens still resolve in config *paths*, which is a different mechanism.
- **The patch folder is no longer self-contained if you move it out of the repository.** Committed, cloned and deployed in place the relative link resolves; carried off on its own the linked templates do not travel with it.

ADT.ai ships a reference scaffold in its own checkout. It is not read from there: copy it into your project and edit it. Read `db_end/` before you keep it, since those files refresh every materialized view, gather schema stats and run every enabled daily job.

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

## Helpers create generates for you

Two kinds of one-off are written into `patch_scripts_dir` (default `patch_scripts/{$PATCH_CODE}/`) and then move into the patch like any other script:

| Written to       | When                                                                          |
| ---------------- | ----------------------------------------------------------------------------- |
| `objects_after/` | the patch window **deleted** an object file, as a `drop.<type>.<name>.sql` |
| `tables_after/`  | a table file whose columns changed, as an `ALTER TABLE` per version step   |

The DROP helper is written for any object your `path_objects` layout resolves. It is a helper, not an automatic destructive action: review it before deploying, and delete it if the deletion was a repository-side move rather than a real drop.

The type and the name come out of `object_types`, whole. Where two types share a folder, the longest configured extension a file ends with owns it, so `packages/core.spec.sql` is `PACKAGE CORE` and `packages/core.sql` is `PACKAGE BODY CORE`. Stripping only the last suffix would leave a name that is not an Oracle identifier at all.

The ALTER helper compares each version of a table file against the one before it, including the version standing before the patch opens, which is read from the parent of the first selected commit that touches the file. A table the window **creates** earns no ALTER: the `CREATE TABLE` shipping in the patch is the whole statement needed.

Whitespace inside SQL string literals is part of the value. Generated ADD and MODIFY statements preserve it, including quoted defaults; changing only that whitespace still produces a column change.

The selected commits decide, not the current working tree. A file some later commit deleted, outside the window you patched, gets no DROP helper, and neither does an object the window both added and deleted.
