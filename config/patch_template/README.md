# Patch template folder

Every `.sql` file here is injected into **every** patch `adtai patch -create` generates, at the slot its folder names. This folder is the reference copy, `patch -create` reads the *project's* `config/patch_template/`, resolved from the project root, so copy this folder into your project and edit it there:

```bash
cp -R <adt.ai>/config/patch_template <your-project>/config/
```

Nothing here is active until you do. `patch_add_templates: False` in `config.yaml` turns the whole mechanism off; `patch_template_dir` moves it.

## Slots

| Folder            | Runs                                              |
| ----------------- | ------------------------------------------------- |
| `db_init/`        | first, in every database patch                    |
| `<group>_before/` | before the object files of that `patch_map` group |
| `<group>_after/`  | after the object files of that `patch_map` group  |
| `db_end/`         | last, in every database patch                     |
| `apex_init/`      | first, in every APEX patch                        |
| `apex_end/`       | last, in every APEX patch                         |

Files inside a slot are injected in filename order, which is what the numeric prefixes are for. A `<group>_before` / `<group>_after` folder is named after a `patch_map` group, `tables_before/`, `objects_after/` and so on.

## The locks folder is not a slot

`locks/` holds the six shared scripts that stop a deploy from overwriting a colleague's work: `lock_objects.sql`, `check_objects.sql`, `check_objects_all.sql` and `unlock_objects.sql` for database objects, `check_rest.sql` and `check_files_ws.sql` for REST modules and workspace files. Nothing in it is injected by folder. `patch -create` links each script by name, under `patch_core_locks` and `patch_signatures`, after setting the binds it reads (`:objects`, `:built_at`, `:rest_modules`, `:ws_files`). `patch_add_templates: False` does not switch them off.

Keep the file names. A script `patch -create` wants but cannot find is not linked; the install script carries `PROMPT -- LOCK FILE MISSING: <path>` in its place, and that guard does not run. The scripts use `APEX_STRING.SPLIT`, so the target needs APEX.

## Environment-specific files

`name.[ENV].sql` runs only on a `patch -deploy -target ENV`; an untagged file runs against every environment. Name a file `95_release.[PROD].sql` and it runs on PROD and nowhere else. The install script links it behind a `--[PROD] ` comment whatever `-create` was given, so one patch deploys to any target, and a hand-run in SQLcl skips it.

## Nothing in these files is substituted

**A slot file's bytes reach SQLcl exactly as they sit on disk.** No token of any kind is replaced, not `{$PATCH_CODE}`, not `{$TARGET_ENV}`, not an `<ANGLE_BRACKET>` placeholder. Write `{$PATCH_CODE}` into a file here and the database receives the eleven characters `{$PATCH_CODE}`.

That follows from how the file gets into the patch: `patch -create` **links** it where it lives (`PROMPT -- TEMPLATE: <path>` plus `@"./<path>"`) rather than copying it, so there is no per-patch copy to substitute into and your own file is never rewritten. Old ADT could substitute only because it took a snapshot of each template into the patch folder first and resolved tokens into that snapshot.

Tokens still resolve in config **paths**, which is a different mechanism, `patch_scripts_dir: 'patch_scripts/{$PATCH_CODE}/'`, `patch_hashes: 'patch_hashes/{$TARGET_ENV}/'` and `patch_deploy_logs: 'logs_{$TARGET_ENV}'` all work as documented.

So a value your SQL needs either comes from ADT (below) or is resolved at run time by the SQL itself.

## What ADT emits for you

Two things you would otherwise hand-edit into a template are generated into the install script instead, with real values:

| Emitted                                                                | From                                              | When                                        |
| ---------------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------- |
| `APEX_UTIL.SET_WORKSPACE` + `APEX_APPLICATION_INSTALL.SET_KEEP_SESSIONS` | the app's `workspace` in `config/internal/apex.db`  | every APEX patch whose app is in that file  |
| `APEX_UTIL.SET_APP_BUILD_STATUS`                                        | `patch_apex_build_status` in `config.yaml`        | only on a matching `-deploy -target`        |

`config/internal/apex.db` is written by `export_apex` and read without connecting, so this costs no database round trip. An app it has never recorded gets no workspace block at all, ADT does not guess one, and your own `apex_init/` file is then in charge.

`patch_apex_build_status` is a per-environment map and is empty by default:

```yaml
patch_apex_build_status:
  PROD: RUN_ONLY
```

The application **version** old ADT stamped alongside the workspace is deliberately not emitted; `apex_end/00_end.sql` carries it as an example instead.

## APEX examples, commented out

Three old ADT settings ship here as examples you switch on, never as defaults. Each is a block inside `/* */` and runs nothing until you delete its `/*` and `*/` lines:

| Example                                         | File                    |
| ----------------------------------------------- | ----------------------- |
| install or upgrade supporting objects on import | `apex_init/00_init.sql` |
| switch the authentication scheme                | `apex_end/00_end.sql`   |
| set the application version                     | `apex_end/00_end.sql`   |

The two `apex_end/` blocks run after the import and name their applications in `IN (...)`, because nothing here is substituted.

## A note on the db_end slot

`70_mviews.sql`, `80_jobs.sql` and `90_checks.sql` come from old ADT and they are opinionated: they refresh every materialized view, gather schema stats, run every enabled daily scheduler job, and recompile whatever the patch invalidated. That is a deliberate post-deploy routine, not a neutral default, read them before you keep them, and delete the ones your deploy should not do.

One statement ships commented out rather than carried over live. `80_jobs.sql` closed its loop with `DBMS_SESSION.SLEEP(60)`, a flat minute added to every deploy whatever the patch contains, and the jobs above it are launched with `use_current_session => FALSE`, so nothing in the script was waiting on that sleep except the person watching it. Uncomment it when you want the run details below to describe this deploy instead of the previous one.

## The session defaults are not here

`SET DEFINE OFF`, `SET TIMING OFF` and `SET SQLBLANKLINES ON` are emitted by `patch -create` itself, into every generated install script, before this folder is read. They are not yours to remember: a project with no template folder at all still gets them. `db_init/00_init.sql` repeats `SET DEFINE OFF` only so the file reads correctly when run on its own.

Everything ADT emits, the session defaults and the APEX environment block, goes in **before** this folder's `db_init/` and `apex_init/` files, so a template of yours can still override any of it.
