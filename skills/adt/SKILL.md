---
name: adt
description: "Lean ADT.ai command router for Oracle/APEX work. Invoke only when the user explicitly asks an agent to use the ADT skill by name; never auto-load it for repository work, general discussion, development, review, command lookup, or incidental mentions of ADT."
metadata:
  created: "2026-06-10"
  updated: "2026-09-02 22:09"
  version: "2.0.0"
  tags: [oracle, apex, deployment, cli, database]
---
# ADT.ai

Use this skill to translate an operational request into a current `adtai` command. It is a router, not a second manual. It is intentionally agent-neutral and must not assume Codex-, Claude-, Copilot-, or editor-specific tools.

The executable is `adtai`; `adt` and `python -m adt_ai` are aliases. Run it from the project root, the folder that owns `config/` and the exported files.

## Conventions in this skill

1. Choose the command below.
2. Read only that command's linked page. If the repository docs are unavailable, run `adtai <command> --help` and use the installed CLI as the source of truth.
3. Preserve the user's environment, schema, application, branch, patch, and output scope. Never invent a deployment target or broaden a selector.
4. Distinguish a preview/read from a write. If the requested action writes files, changes Git state, modifies connection data, or changes a database/APEX application, make that effect clear before running it.
5. Run one understandable shell command at a time and return its meaningful output. Use `-debug` only when diagnosis needs resolved parameters and SQL.

Do not preload every linked page. The repository's [documentation index](../../docs/README.md) owns the detailed behavior, full flag tables, and output descriptions. Install, prerequisites, and machine repair belong to `adt-setup` or [SETUP.md](../../SETUP.md).

## flow: map APEX page navigation

Read [docs/flow.md](../../docs/flow.md). Queries use the local flow store; `-refresh` rewrites it from APEX and `-delete` removes an application's stored graph.

```bash
adtai flow -app 100 -from 10
```

## calendar: show Git activity

Read [docs/calendar.md](../../docs/calendar.md). This is a read-only Git-history report.

```bash
adtai calendar -calendar
```

## connection: edit connection configuration

Read [docs/connection.md](../../docs/connection.md) and, for credentials, [docs/connection_passwords.md](../../docs/connection_passwords.md). Changes preview by default and apply only with `-go`. Let password actions prompt; do not put passwords or literal encryption keys in shell history. Prefer key-file paths for `-old-key`, `-new-key`, and `-key`. With no terminal attached the prompt reads stdin, so `-create -go` and `-add-schema -go` write their file with no password, while `-set-pwd` and `-set-wallet-pwd` refuse and ask for a terminal.

```bash
adtai connection -add-schema -env DEV -schema APP
```

## dependencies: query the object graph

Read [docs/dependencies.md](../../docs/dependencies.md). `-from`, `-to`, `-impact`, `-tree`, and `-age` query the local SQLite mirror offline. `-refresh` connects and updates the mirror; `-force` first wipes the requested refresh scope.

```bash
adtai dependencies -impact APP_ORDERS
adtai dependencies -age
```

## discovery: run SELECT exploration

Read [docs/discovery.md](../../docs/discovery.md). ADT.ai accepts SELECT statements and starts a read-only transaction, but a SELECT can invoke a stored function and an autonomous function can commit. Treat the SQL and called functions as executable database code, not as side-effect-proof input. `-nolog` suppresses the separate report; it does not suppress `-file` result write-back to the source file.

```bash
adtai discovery -sql "SELECT object_type, COUNT(*) FROM user_objects GROUP BY object_type" -nolog
```

## doctor: setup checks, updates, and project bootstrap

Read [docs/doctor.md](../../docs/doctor.md). Bare `doctor` checks the machine. `-init`, `-update`, `-sqlcl`, and `-force` change the project or installed tools, so use them only when that change was requested.

```bash
adtai doctor
```

## export_apex: export APEX applications

Read [docs/export_apex.md](../../docs/export_apex.md) and [docs/export_apex_formats.md](../../docs/export_apex_formats.md). `-reveal` lists applications; an export writes only the formats named. After exporting or editing APEXlang, run `validate`.

```bash
adtai export_apex -app 100 -full -split -files
adtai export_apex -app 100 -apexlang
```

## export_data: export table data

Read [docs/export_data.md](../../docs/export_data.md). It writes CSV and generated MERGE SQL. Narrow shared schemas with `-schema` and tables with `-name`; use `-silent` for agent-driven exports unless per-table progress is useful.

```bash
adtai export_data -silent -name APP_LOOKUP%
```

## export_db: export database objects

Read [docs/export_db.md](../../docs/export_db.md) and [docs/export_db_layout.md](../../docs/export_db_layout.md). Use `-silent` for agent-driven exports unless per-object progress is useful. Combine `-schema`, `-type`, `-name`, and `-recent` to keep the write set intentional. `-delete` removes existing object files before export; `-baseline` measures an environment instead of exporting it.

```bash
adtai export_db -silent -recent 7
adtai export_db -silent -type PACKAGE% -name APP_%
```

## patch: build and deploy patches from commits

Read [docs/patch.md](../../docs/patch.md), then only the linked patch topic needed for content, install order, deployment, hashes, archiving, or sandbox removal. A bare filtered run previews. `-create` rewrites a patch folder, `-deploy` changes the target database/APEX application, `-archive` moves and removes patch folders, and `-drop` removes sandbox APEX applications whose recorded creator is the `apex_account` in `config/IDENTITY.yaml`, or which record no creator at all (no APEX import writes that column); somebody else's needs `-force`. A successful or failed `-drop` also writes one dictionary-verified receipt per application at `<path_apex>/logs_<ENV>/<timestamp>_apex_drop_<application-id>_<DELETED|FAILED>.log`; the folder comes from `-target` and the filename id comes from `-drop`. APEXlang files remain selected but are never snapshotted; deploy imports the application's live `apexlang/` folder. `-deploy -app <sandbox-id>` also stamps that sandbox's `last_updated_by`/`last_updated_on` with the same `apex_account` and the current moment, so the clone shows its author in the Builder; a bare `-app` stamps nothing, and no import can write `created_by` in any format. A target already deployed is skipped only when its `logs_<ENV>/deployment.json` receipt matches the same executable inputs and target, so a partial script failure, a SQLcl error or a failed APEX verification leaves that target incomplete and it deploys again on the next run; a patch deployed before 1.0 carries no receipt and runs once more. A post-deploy APEX verification that could not complete fails the deploy instead of passing as skipped. Never guess `-target`, `-name`, commit selectors, content mode, or application id. Preview the exact selection before creation or deployment.

```bash
adtai patch -target UAT -name TASK-123
adtai patch -target UAT -name TASK-123 -create
adtai patch -target UAT -name TASK-123 -deploy
```

## rebuild: refresh the commit store

Read [docs/rebuild.md](../../docs/rebuild.md). A normal run updates the branch's local SQLite commit cache. `-reveal` only lists remote branches; `-switch` changes the checked-out Git branch.

```bash
adtai rebuild
```

## recompile: recompile invalid objects

Read [docs/recompile.md](../../docs/recompile.md). The default and `-mviews` modify database objects. `-synonyms`, `-disabled`, and `-jobs` are reports. `-trailing` is not cleanup of exported files: it rewrites stored database source through `CREATE OR REPLACE` and has no preview mode.

```bash
adtai recompile -schema APP
```

## search_repo: search Git history

Read [docs/search_repo.md](../../docs/search_repo.md). Searching is read-only. `-restore` writes historical copies; adding `-stage` replaces original paths and stages them in Git, refusing a file with uncommitted changes rather than overwrite it.

```bash
adtai search_repo -name APP_ORDERS -files
```

## ut: run utPLSQL test suites

Read [docs/ut.md](../../docs/ut.md), [docs/ut_discovery.md](../../docs/ut_discovery.md), and [docs/ut_coverage.md](../../docs/ut_coverage.md) only as needed. A failed test, coverage-gate miss, or zero-test run exits non-zero.

```bash
adtai ut -name APP_% -gate 90
```

## validate: check exported APEXlang source

Read [docs/validate.md](../../docs/validate.md). It validates local APEXlang folders or zips without a database connection, credentials, environment, or schema. The loop from export to promotion, one command per step, is docs/apex_round_trip.md.

```bash
adtai validate -app 100
```

## Examples

The examples above are starting shapes, not substitutes for the selected command's page or `--help`. Add only the selectors and actions required by the user's request.
