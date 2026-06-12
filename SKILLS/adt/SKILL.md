---
created: 2026-06-10
updated: 2026-06-12
name: adt
version: 1.0.0
tags: [oracle, apex, deployment, cli, database]
description: "ADT.ai usage guide for Oracle/APEX work: export database objects, APEX apps and data, run read-only SQL discovery, search Git history, query the dependency graph, diff, recompile, and build/deploy patches. Use for any ADT.ai command help."
---
# ADT.ai

ADT.ai is a Python CLI that exports, inspects, and deploys Oracle Database objects and APEX applications. It reads from config files, Git, and the database; it never stores its own metadata in the database. Exports work against any ordinary folder — a Git repository is useful but not required.

The command is `adtai` (aliases: `adt-ai`, `python -m adt_ai`). Full argument tables for every command live in the repo's `USAGE.md`; this skill is the operating cheat-sheet, including the full `doctor` module. The repo-only `adt-setup` skill remains a deeper one-time setup checklist, not a daily runtime skill.

Run commands from the project root (the folder holding `config/` and the export output). Every command prints a standard banner, dashed section headers, and a final `TIMER: Ns` footer.

## Conventions in this skill

- Pick one quick-reference line and run it as its own shell call.
- Show the user the full console output of export/recompile/patch commands — the overview tables and progress are the point.
- `-debug` on any command prints the resolved parameters and the SQL with bind values.
- `--help` (or `-h`) on any command prints its full argument list.

## export_db — export database objects

Each object becomes a clean `.sql` file under `database/<schema>/<object_type>/`. Filter by time (`-recent`), type (`-type`), and name (`-name`); these combine.

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

**Rules:**
- Name the formats explicitly. Common set: `-full -split -files -rest -readable`. Skip `-embedded` unless asked — it slows the export.
- Add `-recent N` only to print a recent-component report before the export; the export itself always writes all selected components. With `-reveal`, `-recent` filters the listed apps.
- If apps don't appear, the connection's APEX schema likely doesn't match the owner — narrow or widen with `-schema`, or use `-owners` in reveal.

## export_data — export table data

Exports reference/seed tables to CSV with generated MERGE SQL. Not for transactional or sensitive data.

Specific tables:

```bash
adtai export_data -name CONFIG_PARAMETERS,LOV_STATUS
```

Wildcards, or re-export every previously exported table:

```bash
adtai export_data -name CONFIG%,LOV_%
adtai export_data
```

**Limitations:** BLOB/CLOB/XMLTYPE/JSON columns are skipped; audit columns are dropped per config; set correct NLS date formats on the target before running the generated SQL.

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

Alias: `depends`. Answers "what uses this?" / "what would I break?" against a committed graph (`dependencies/index.yaml` + `edges.yaml`). Day-to-day queries are offline; only `-refresh` touches the database.

Build or rebuild the index from the database:

```bash
adtai dependencies -refresh -env DEV -schema APP
```

Query the graph:

```bash
adtai dependencies -uses "PACKAGE BODY.CORE"
adtai dependencies -used-by "TABLE.CORE_LOGS"
adtai dependencies -impact "TABLE.CORE_LOGS"
adtai dependencies -unused
```

`-format yaml` or `-format md` moves the chrome to stderr so stdout stays pipeable.

## diff — compare two environments or schemas

Generates a SQLcl DIFF artifact between two configured environments or schemas. See `USAGE/diff.md` for the source/target argument shape.

```bash
adtai diff -source DEV -target UAT
```

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

## patch — build and deploy patches from commits

Reads commits, resolves dependencies, orders objects, and generates deployment scripts. Object ordering follows `patch_map` in `config.yaml`.

Preview matching commits:

```bash
adtai patch -target UAT -patch TASK_ID
```

Create, then create-and-deploy:

```bash
adtai patch -target UAT -patch TASK_ID -create
adtai patch -target UAT -patch TASK_ID -create -deploy
```

Cherry-pick commits (ranges like `1-20` supported) and ignore some:

```bash
adtai patch -target UAT -patch TASK_ID -commit 1-20 -ignore 5
```

Full flag set in `USAGE/patch.md`. ADT.ai no longer accepts old placeholder source flags; use the default commit-resolved files, hash mode (`-hash` / `-locked`), or the explicit create/deploy/install/refresh/archive actions.

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

Incremental by default; one YAML cache per branch at `config/commits/<branch>.yaml`. `-full` rebuilds from scratch. `-reveal` is a read-only remote-branch inspector; `-reveal -switch N` checks out the Nth filtered branch.

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
4. Build the patch: `adtai patch -target UAT -patch TASK-123 -create`.
5. Commit the patch folder and open a pull request.

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

Create and deploy a UAT patch for a task:

```bash
adtai patch -target UAT -patch TASK-123 -create -deploy
```
