---
created: 2026-06-10
updated: 2026-07-16 18:05
name: adt
version: 1.1.0
tags: [oracle, apex, deployment, cli, database]
description: "ADT.ai usage guide for Oracle/APEX work: export database objects, APEX apps and data, run read-only SQL discovery, search Git history, query the dependency graph, and recompile invalid objects. Use for any ADT.ai command help."
---
# ADT.ai

ADT.ai is a Python CLI that exports, inspects, and deploys Oracle Database objects and APEX applications. It reads from config files, Git, and the database; it never stores its own metadata in the database. Exports work against any ordinary folder — a Git repository is useful but not required.

The command is `adtai` (aliases: `adt`, `python -m adt_ai`). Full argument tables for every command live in per-command files under the repo's `USAGE/`; this skill is the operating cheat-sheet for the common commands, including the full `doctor` module. Lower-frequency commands such as `flow` are not expanded here — see its page under `USAGE/`. The repo-only `adt-setup` skill remains a deeper one-time setup checklist, not a daily runtime skill.

Run commands from the project root (the folder holding `config/` and the export output). Every command prints a standard banner, dashed section headers, and a final `TIMER: Ns` footer.

## Conventions in this skill

- Pick one quick-reference line and run it as its own shell call.
- Show the user the full console output of export/dependencies/recompile commands — the overview tables and progress are the point.
- `-debug` on any command prints the resolved parameters and the SQL with bind values.
- `--help` (or `-h`) on any command prints its full argument list.

## export_db — export database objects

Each object becomes a clean `.sql` file under `<schema>/database/<object_type>/` (the default layout; the legacy `database/<schema>/<object_type>/` layout is also supported via `path_objects`). Filter by time (`-recent`), type (`-type`), and name (`-name`); these combine.

**Always pass `-silent`** when driving exports from this skill — it suppresses the per-object name/progress flood while keeping the banner, connection block, overview, and timer. Drop `-silent` only when the user is interactively debugging a specific object.

Recent changes (last 7 days):

```bash
adtai export_db -silent -recent 7
```

Specific object types:

```bash
adtai export_db -silent -type PACKAGE%,VIEW%
```

Name + time filters combined:

```bash
adtai export_db -silent -name APP_% -recent 7
```

Jobs export separately — `JOB` objects have no reliable `last_ddl_time`, so **never** combine `-type JOB` with `-recent`:

```bash
adtai export_db -silent -type JOB
```

Filter by author in a shared schema worked through proxy users — `-by <NAME>` for a specific db user/schema, `-my` for yourself (db schema read from the gitignored `config/me.yaml`). Both resolve authorship against the project's configured `audit:` source (a DDL-log table/view), so they need no DBA audit-trail access; without an `audit:` block in `config.yaml` they exit `2`:

```bash
adtai export_db -silent -by SCOTT
adtai export_db -silent -my
```

Clean export (delete existing object files first, excluding `DATA`):

```bash
adtai export_db -silent -recent 7 -delete
```

Preview the write plan without touching files:

```bash
adtai export_db -silent -dry-run -recent 7
```

## export_apex — export APEX applications

Exports only the formats named on the command line. Use `-all` for every format. ADT.ai has no `-only` and no `-no...` suppressors (old-ADT flags that no longer exist).

List available workspaces and applications:

```bash
adtai export_apex -reveal
```

List apps under a different / all owners, hiding high-id temp apps:

```bash
adtai export_apex -reveal -owners -max_app_id 10000
```

Typical export of one app:

```bash
adtai export_apex -app 100 -full -split -files -rest -readable
```

Export only selected pages/shared components from component-based formats:

```bash
adtai export_apex -app 100 -split -readable -page 1-50 55,56 -component LOV:NAME% LIST:MENU%
adtai export_apex -app 100 -page 7
adtai export_apex -app 100 -component LOV:%
```

**Rules:**
- Name the formats explicitly. Common set: `-full -split -files -rest -readable`. Skip `-embedded` unless asked — it slows the export.
- `-recent N`, `-page`, and `-component TYPE:NAME%` filter split/readable/embedded component output. `-page` or `-component` without an explicit format defaults to `-split`. Filtered component exports print affected rows instead of dotted progress and do not update `apex_timers.yaml`. Full app SQL, REST services, app files, and workspace files stay broad. With `-reveal`, `-recent` filters the listed apps.
- If apps don't appear, the connection's APEX schema likely doesn't match the owner — narrow or widen with `-schema`, or use `-owners` in reveal.

## export_data — export table data

Exports reference/seed tables to CSV with generated MERGE SQL. Not for transactional or sensitive data.

Specific tables:

```bash
adtai export_data -silent -name CONFIG_PARAMETERS,LOV_STATUS
```

Wildcards, or re-export every previously exported table:

```bash
adtai export_data -silent -name CONFIG%,LOV_%
adtai export_data -silent
```

**Limitations:** BLOB/CLOB/XMLTYPE/JSON columns are exported to table-named sidecar folders as `<primary-key>.<column>.<ext>`; audit columns are dropped per config; set correct NLS date formats on the target before running the generated SQL.

## discovery — safe read-only SQL exploration

The first-choice command for "what's in this database?" questions. It is read-only by design: a static SELECT-only validator rejects anything else, and statements run under `SET TRANSACTION READ ONLY` which is rolled back. Runs write a Markdown report to `config/discovery/<YYYY-MM-DD_HH-MI>.md` by default (the folder is auto-gitignored); inline `-sql` results also print to the console. Add `-nolog` for throwaway exploration that must not write reports, `.gitignore` changes, or file-mode result blocks.

Exactly one of `-sql` or `-file` is required (both or neither errors).

Single inline query:

```bash
adtai discovery -env DEV -sql "SELECT object_type, COUNT(*) FROM user_objects GROUP BY object_type"
```

Several `;`-separated statements from a file:

```bash
adtai discovery -env DEV -file ./explore.sql
```

Target a schema and raise the per-query row cap (default 200):

```bash
adtai discovery -env DEV -schema APP -sql "SELECT * FROM app_settings" -limit 500
```

## dependencies — query the object graph

Answers "what uses this?" / "what would I break?" against a single gitignored SQLite mirror of the raw Oracle data dictionary at `config/dependencies.db` (multi-schema `USER_*` tables stamped with `OWNER`, plus the APEX dictionary keyed by application id). Every query mode recomputes its answer from the raw mirrors at query time — day-to-day queries are offline; only `-refresh` touches the database.

There is no separate rebuild step — re-running `-refresh` incrementally updates the mirror: `-schema` detects added/changed/dropped objects by `LAST_DDL_TIME` and removes stale relations to/from dropped objects; object names or SQL wildcards after `-refresh` force a deep refresh for matching objects and dependency rows in both directions; `-force` wipes only the requested schema/app scope before reloading it:

```bash
adtai dependencies -refresh -env DEV -schema APP
adtai dependencies -refresh CORE CORE% -env DEV -schema APP
adtai dependencies -refresh -force -env DEV -schema APP
```

`-refresh -app` updates the APEX dictionary mirror for one or more application ids (repeat the flag or space-separate ids under one `-app`) and ranges — `MIN-MAX` (closed) or `MIN+` (open) — resolved against the apps discovered across the configured schemas:

```bash
adtai dependencies -refresh -env DEV -app 100 200 300
adtai dependencies -refresh -env DEV -app 120-130
adtai dependencies -refresh -env DEV -app 300+
```

Query the mirror (`-from OBJ` = objects OBJ depends on; `-to OBJ` = objects that depend on OBJ; `-impact OBJ` = transitive reverse impact, appending APEX page/component/property callers when the APEX mirror is present; `-tree CONSTRAINT_NAME` = foreign-key cascade paths around a named constraint):

```bash
adtai dependencies -from "PACKAGE BODY.CORE"
adtai dependencies -to "TABLE.CORE_LOGS"
adtai dependencies -impact "TABLE.CORE_LOGS"
adtai dependencies -tree "ORDER_ITEMS_ORDER_FK"
```

On a multi-schema mirror an object name (e.g. `PACKAGE.CORE`) can be ambiguous across owners. Add `-schema OWNER[,OWNER ...]` to any query mode as an offline, case-insensitive owner filter that disambiguates by the owner column the mode matches on — `-from` filters the dependent `OWNER`, `-to` filters `REFERENCED_OWNER`, `-impact` constrains only the seed's `REFERENCED_OWNER` (the transitive walk is unchanged). It is parsed locally (no DB connection); omitting it matches every tracked owner. `-app`/`-force` stay refresh-only.

```bash
adtai dependencies -from "PACKAGE.CORE" -schema APP
```

`-age` reports when each schema and APEX app scope was last refreshed, offline. Check it before trusting a query — a stale mirror answers confidently and wrongly, and this is the supported staleness check rather than reading the file's mtime:

```bash
adtai dependencies -age
```

`-format yaml` or `-format md` moves the chrome to stderr so stdout stays pipeable.

## recompile — recompile invalid objects

Dependency-aware retry built in. Supports PL/SQL compile flags (native/interpreted, optimization level, PL/Scope, warnings) and old-ADT-style overview with PL/Scope gap counts.

Recompile invalid objects:

```bash
adtai recompile -env DEV
```

Force-recompile all with native code + optimization, scoped by type/name:

```bash
adtai recompile -env DEV -force -native -level 3 -type PACKAGE% -name XX%
```

`-type`, `-name`, and `-schema` are shared filters, and every mode below is scoped by them — no mode carries a name pattern of its own, so `-mviews DEP%` is a parser error and `-mviews -name DEP%` is the way. `-type`/`-name` take multiple patterns (`-type PACKAGE VIEW`, `-type PACKAGE,VIEW`, or a repeated flag). `-type` speaks Oracle's vocabulary: bare `PACKAGE` means specifications, `PACKAGE BODY` bodies, `MVIEW`/`MATERIALIZED` both mean `MATERIALIZED VIEW`. `-schema` is repeatable and pattern-aware (`-schema APP,CORE%`); each schema is an independent pass.

### Modes

Each flag below **replaces** the ordinary invalid-object recompile rather than adding to it, so the OBJECTS OVERVIEW does not print.

Report-only — these connect, read, and print, and change nothing:

```bash
adtai recompile -env DEV -synonyms
adtai recompile -env DEV -disabled -type TRIGGER
adtai recompile -env DEV -jobs -name APP%
```

- `-synonyms` maps each synonym to its target object, grouped by target owner, one privilege per row with `PRIV`/`GRNT`/`VALID`.
- `-disabled` lists disabled constraints, invalid/function-disabled indexes, and disabled triggers. It is the one report spanning several object types, so `-type` picks which of `CONSTRAINT`/`INDEX`/`TRIGGER`; bare `-disabled` reports all three.
- `-jobs` lists today's scheduler job runs, grouped by status.

Materialized views — reports, then acts:

```bash
adtai recompile -env DEV -mviews
adtai recompile -env DEV -mviews -name DEP% -force
```

`-mviews` compiles invalid MVs and refreshes stale ones using each view's **own** configured refresh method (a COMPLETE view is never flipped to FAST). With `-force`, every matching view is refreshed regardless of staleness.

**`-trailing` writes to the database.** It strips trailing whitespace from stored source via `CREATE OR REPLACE` — the fix for `export_db` diffing an untouched object on every export, since `export_db` strips trailing whitespace from every line it writes and the database's stored source does not:

```bash
adtai recompile -env DEV -trailing
adtai recompile -env DEV -trailing -type PACKAGE% -name APP%
```

There is **no preview and no dry run** — asking for `-trailing` is asking for the strip, and `-trailing -fix` is a parser error rather than the old spelling. The safety is structural rather than a confirmation prompt: an object with nothing to strip is never touched, and removing trailing whitespace cannot change behaviour. Scope with `-type`/`-name` to narrow the blast radius. Covers `PACKAGE`, `PACKAGE BODY`, `PROCEDURE`, `FUNCTION`, `TRIGGER`, and `VIEW`; wrapped objects, editioning views, and views carrying `WITH READ ONLY` / `WITH CHECK OPTION` are skipped, since none of those survive the rebuild. `CREATE OR REPLACE` invalidates dependents, so follow a sweep with a plain `adtai recompile`.

## search_repo — search Git history

Git-only history search for commit summaries, changed file paths, ADT-style database object type/name, authors, dates, numbers, and hashes. It searches `adtai rebuild` cache artifacts in `config/commits/<branch>.yaml`; no Oracle connection is required.

Search changed packages and object names:

```bash
adtai search_repo -file packages -name ORDER_API
adtai search_repo -file packages -name ORDER_API -files
```

Search author/date scope:

```bash
adtai search_repo -by bob@example.com -since 2026-06-01 -until 2026-06-10
```

Restore historical versions beside the original file, or stage one version to the original path:

```bash
adtai search_repo -file order_v -commit 42 45 -restore
adtai search_repo -file order_v -commit 42 -restore -stage
```

## rebuild — refresh the commit cache

Incremental by default; one YAML cache per branch at `config/commits/<branch>.yaml`. To rebuild a branch from scratch, delete its `config/commits/<branch>.yaml` and re-run. `-reveal` is a read-only remote-branch inspector; `-reveal -switch N` checks out the Nth filtered branch.

```bash
adtai rebuild
adtai rebuild -reveal -my
```

## doctor — setup checks, updates, and project bootstrap

Plain `doctor` is read-only: it checks local tools, environment variables, Python dependencies, Instant Client, SQLcl, and online update availability. It does not update ADT.ai, reinstall requirements, download SQLcl, or create files.

```bash
adtai doctor
```

Skip online metadata checks when offline or when you only want local diagnostics:

```bash
adtai doctor -offline
```

Run the full explicit update flow only when requested:

```bash
adtai doctor -update
```

Upgrade SQLcl only:

```bash
adtai doctor -sqlcl
```

Bootstrap project config:

Scaffolds project config, repo ignore rules for generated artifacts, and safe local `connections/.gitkeep` / `connections/wallets/.gitkeep` placeholders. It never creates connection YAML secrets, wallet contents, generated-cache folders, or APEX credentials folders. Existing generated files are skipped; `-force` overwrites the generated templates. The standalone `init` command is not public.

```bash
adtai doctor -init
```

## Typical developer workflow

1. Branch from the main line, make changes in the DEV database and APEX builder.
2. Export what changed:
   - `adtai export_db -silent -recent 1`
   - `adtai export_apex -app 100 -split -readable -recent 1`
   - `adtai export_data -name TABLE_NAME` (if data changed)
3. Stage and commit with the task-id prefix.
4. Open a pull request.

## Examples

Export database objects changed in the last week (agent-driven, silent):

```bash
adtai export_db -silent -recent 7
```

Explore a database safely without writing anything:

```bash
adtai discovery -env DEV -sql "SELECT table_name FROM user_tables ORDER BY table_name" -nolog
```

Find everything that depends on a table before changing it:

```bash
adtai dependencies -impact "TABLE.CORE_LOGS"
```
