---
created: 2026-06-10
updated: 2026-08-24 17:40
name: adt
version: 1.9.7
tags: [oracle, apex, deployment, cli, database]
description: "ADT.ai usage guide for Oracle/APEX work: export database objects, APEX apps and data, validate APEXlang source, run utPLSQL test suites, run read-only SQL discovery, search Git history, query the dependency graph, recompile, and build/deploy patches. Use for any ADT.ai command help."
---
# ADT.ai

ADT.ai is a Python CLI that exports, inspects, and deploys Oracle Database objects and APEX applications. It reads from config files, Git, and the database; it never stores its own metadata in the database. Exports work against any ordinary folder, a Git repository is useful but not required.

The command is `adtai` (aliases: `adt`, `python -m adt_ai`). Full argument tables for every command live in per-command files under the repo's `docs/`; this skill is the operating cheat-sheet for the common commands, including the full `doctor` module. Lower-frequency commands (`connection`, `calendar`, `flow`) are not expanded here, see their pages under `docs/`. One `connection` action is worth knowing about from here because nothing else offers it: `adtai connection -rekey -old-key OLD -new-key NEW -go` re-encrypts every stored secret in the resolved connection file in one pass, previews without `-go`, and writes nothing at all unless it can rewrite all of them. Re-encrypting is not the same as rotating the database password, and after a suspected leak only the second one matters. The repo-only `adt-setup` skill remains a deeper one-time setup checklist, not a daily runtime skill.

Run commands from the project root (the folder holding `config/` and the export output). Every command prints a standard banner, dashed section headers, and a final `TIMER: Ns` footer. `export_db`, `export_data`, `export_apex`, `recompile`, and `dependencies -refresh` accept a multi-schema `-schema A B` list; a multi-schema run prints the banner once, then executes schema by schema, connect, that schema's full output, its own `TIMER`, before moving to the next, so a `-schema A B` run reads as two single-schema runs back to back, not one interleaved pile.

## Conventions in this skill

- Pick one quick-reference line and run it as its own shell call.
- Show the user the full console output of export/recompile/patch commands, the overview tables and progress are the point.
- `-debug` on any command prints the resolved parameters and the SQL with bind values.
- `--help` (or `-h`) on any command prints its full argument list.

## export_db: export database objects

Each object becomes a clean `.sql` file under `<schema>/database/<object_type>/` (the default layout; the legacy `database/<schema>/<object_type>/` layout is also supported via `path_objects`). The schema token carries its own case, so a project whose folders are uppercase sets `path_objects: '<SCHEMA>/database/<object_type>/'`. Filter by time (`-recent`), type (`-type`), and name (`-name`); these combine.

**Always pass `-silent`** when driving exports from this skill, it suppresses the per-object name/progress flood while keeping the banner, connection block, overview, and timer. Drop `-silent` only when the user is interactively debugging a specific object.

Recent changes (last 7 days):

```bash
adtai export_db -silent -recent 7
```

Shorter than a day, `DAYS` takes a fraction, `1/24` for the past hour and `5/1440` for the past 5 minutes (Oracle counts a DATE in days, so the window is `SYSDATE - DAYS`). The same spelling works on `export_apex`, `dependencies -refresh`, and `search_repo`:

```bash
adtai export_db -silent -recent 1/24
```

Everything changed since your last export of each schema, bare `-recent` uses the per-schema watermark in the gitignored `config/internal/recent.yaml` (a schema with no recorded export yet exports in full and seeds it; a narrowed run never advances it):

```bash
adtai export_db -silent -recent
```

Specific object types:

```bash
adtai export_db -silent -type PACKAGE%,VIEW%
```

Name + time filters combined:

```bash
adtai export_db -silent -name APP_% -recent 7
```

`JOB` and `MVIEW LOG` both ride a windowed run, by two different routes. An mview log is filtered on its log table's `LAST_DDL_TIME`, a real DDL timestamp that DML never moves. A job has no change timestamp anywhere, so a window compares a content signature against `config/internal/job_signatures.yaml` and exports only the jobs whose definition actually moved, which keeps `-recent` on a schema of thousands of jobs down to the handful that changed. **The signature narrows a window and never an explicit request:** `-type JOB` with no `-recent` exports every matching job, unfiltered, which is how to re-pull a whole job tree on demand:

```bash
adtai export_db -silent -type JOB
```

**`config/IDENTITY.yaml` is the one place ADT.ai asks who you are**, gitignored and never committed, and it answers both halves of that question. `db_schema` is the DATABASE identity, read by `export_db -my` and by every connection's `SET_IDENTIFIER`. `email` and `apex_account` are the COMMIT identity, read by every git-backed `-my`/`-by`: `patch`, `search_repo`, `rebuild -reveal`, `calendar`, and `export_apex -my`, which matches APEX workspace logins on `apex_account` because those are `FIRST.LAST` rather than addresses. **Git is the fallback, not a second source of truth:** state nothing and each half falls back independently to `git config user.email` and `user.name`, so a checkout with no file behaves exactly as it always has. Set `email` whenever your git author is not the identity your work should be attributed to, the ordinary case on a machine account, a shared runner, or a laptop carrying a corporate address. Full shape: `docs/config.md` §Developer Identity.

Filter by author in a shared schema worked through proxy users, `-by <NAME>` for a specific db user/schema, `-my` for yourself (the `db_schema` above). Both resolve authorship against the project's configured `audit:` source (a DDL-log table/view), so they need no DBA audit-trail access; without an `audit:` block in `config.yaml` they exit `2`:

```bash
adtai export_db -silent -by SCOTT
adtai export_db -silent -my
```

Add the optional 4th `audit:` key `changed_at` (the DDL timestamp) and the filter becomes time-aware: an object you worked on that somebody else changed **last** is still exported but marked `[OTHER_AUTHOR]` on its row, and `-recent` narrows the DDL log as well as `user_objects`, so `-my -recent 1` means "changed within a day, by me" instead of ANDing two unrelated sources. Without the key the filter can only answer *ever touched*, an unordered log cannot rank.

Authorship has to be written at DDL time: Oracle records no actor, and in a proxy session `USER`/`ORA_LOGIN_USER`/`SESSION_USER` all return the *proxied schema*. The project's DDL trigger must record `COALESCE(SYS_CONTEXT('USERENV','CLIENT_IDENTIFIER'), SYS_CONTEXT('USERENV','PROXY_USER'), USER)`, ADT.ai publishes `db_schema` into `CLIENT_IDENTIFIER` on every connection, and `PROXY_USER` is the only place a proxy's real user appears.

Clean export (delete existing object files first, excluding `DATA`):

```bash
adtai export_db -silent -recent 7 -delete
```

**`-groups` reorganizes files that are already exported and never connects or exports.** Bare `-groups` auto-detects a per-prefix layout and lists it; `-groups PREFIX ...` lists only the prefixes named. Nothing moves until `-force` is added, and `-force GROUP` lands every named prefix in one uppercased `<object_type>/GROUP/` folder instead of one folder per prefix, across every object type they reach. A name needs named prefixes, so `-groups -force GROUP` is refused at exit `2`:

```bash
adtai export_db -groups ICT_VPD ICT_ABC -force VPD
```

When the user wants to *watch* a long export rather than have it stay quiet, `-compact` keeps the `OBJECTS OVERVIEW:` table and replaces the per-object rows with one dotted bar per schema, drawn under `EXPORTING <n> OBJECTS:` and advanced as each object's DDL comes back, with the time still to run on the right. The polarity is the reverse of `ut`, where the bar is the default and `-verbose` brings back the listing: here the listing is old-ADT parity output and stays the default. `-silent` outranks `-compact`, so pass one or the other:

```bash
adtai export_db -compact -recent 7
```

## export_apex: export APEX applications

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
adtai export_apex -app 100 -page 7 -deep
adtai export_apex -app 100 -component LOV:%
```

Export APEXlang `.apx` source (APEX 26.1+):

```bash
adtai export_apex -app 100 -apexlang
adtai export_apex -app 100 -apx
```

Exporting several apps in several formats is a block per app, headed `EXPORTING APP <id>/<alias>:`, and a row per format, which scrolls the `APEX APPLICATIONS:` table away long before the run ends. `-compact` keeps that table and replaces everything under it with **one bar for the whole schema**, drawn under `EXPORTING <SCHEMA> APPS:`. Same polarity as `export_db -compact`: the per-action rows are old-ADT parity output and stay the default, so the flag is the bar.

```bash
adtai export_apex -app 100,101,102 -full -split -rest -compact
```

The countdown opens on a real figure: the budget is the sum of what each app/format pair cost on its last run, stored per pair in `config/internal/apex.db`. Both compact bars are seeded, and the two stores stay apart because the units do, `export_db` times seconds per object type while an APEX slice has no comparable sub-unit, so the pair itself is what gets timed. That is also what makes it honest across formats, a `FULL APP EXPORT` and a `REST SERVICES` slice take the share of the bar they actually cost, where a bar counting actions would sit at half way with most of the work still ahead. A pair never exported before falls back to a long placeholder instead of counting as free. One bar per schema, never spanning schemas.

**Rules:**
- Name the formats explicitly. Common set: `-full -split -files -rest -readable`. Skip `-embedded` unless asked, it slows the export.
- `-apexlang` (alias `-apx`) writes the APEXlang folder tree to `apexlang/` in the app folder: `application.apx`, `pages/`, `shared-components/`, `workspace-components/`, `deployments/`, `.apex/`. Members land verbatim, and the folder is recreated per export so deleted components leave no stale `.apx`. Whole-app format: `-page`, `-component`, and `-recent` never filter it and it never advances a watermark. Static-file payloads are skipped on purpose, `-files` stays the single static-file channel, so `apexlang/` is a source and review surface, not a directly importable artifact.
- APEX 26.1 is the version line for both directions: `-apexlang` needs 26.1+ and prints a skip note (never a failure) below it when you named the format yourself, under `-all` the skip is silent, while `-readable` no longer exists at 26.1+ and is skipped silently there. Use `-readable` pre-26.1, `-apexlang` from 26.1 on.
- Check an APEXlang export with `adtai validate` (below), it is the compiler check that makes `.apx` safe to edit.
- The ID-independent SHA-256 application fingerprint is recorded by every export under `checksum:` in `config/internal/apex.db`. It answers "did anything actually change?" without diffing a full export. There is no flag: `-checksum` was removed and is now rejected, no row prints for it, `-page`/`-component`/`-recent` never narrow it, and it never advances a `-recent` watermark. An export also deletes any `checksum.txt` the old format left in an application folder, sparing static files of that name.
- `-recent N`, `-page`, and `-component TYPE:NAME%` filter split/readable/embedded component output. Bare `-recent` means "changed since the last export of this app in this format", a watermark keyed per environment/app/format in the gitignored `config/internal/apex.db`; each exported format advances its own key, report-only `-recent` never does. `-page` or `-component` without an explicit format defaults to `-split`. Add `-deep` only with `-page` when the export should include components recorded for those pages in `config/internal/dependencies.db`, such as LOVs, lists, and authorization schemes. Filtered component exports print affected rows instead of dotted progress and do not update `apex.db`. Full app SQL, REST services, app files, and workspace files stay broad. With `-reveal`, `-recent` filters the listed apps.
- If apps don't appear, the connection's APEX schema likely doesn't match the owner, narrow or widen with `-schema`, or use `-owners` in reveal.
- `-by NAME`/`-my` narrow the `-recent` report (and the split/readable/embedded output it filters) to components changed by one developer or by yourself, the same author filter `export_db -by`/`-my` uses.
- `-rest` runs through SQLcl with a named `ADT_…` connection: registered automatically on first use (password in SQLcl's secure store, wallet included), recorded as `sqlcl:`/`sqlcl_sync:` in the connection YAML, re-registered automatically after a credential change. Opt out with `sqlcl_named_connections: false` in `config.yaml`. Details: `docs/connection.md` §Named SQLcl connections.
- **Asking only for `-rest` and/or `-files_ws` is its own shape.** Both write under `apex/workspace/`, a path with no app id, so a run that names nothing else exports no application: no `APEX APPLICATIONS:` table, no application block, one bare `EXPORTING:` header per schema segment with the progress rows under it, and no per-application work at all. One application is used silently for the workspace security context and never named. Add a per-application format (`-split -rest`) and the ordinary screen is back, with the schema-level row inside the first app's block. Details: `docs/export_apex.md` §Schema-level formats on their own.

## validate: check exported APEXlang source

Runs the APEXlang compiler over exported `apexlang/` folders and reports its errors. **Never connects**, the compiler ships inside SQLcl, so there is no `-env`, no `-schema`, no credentials, and it works in CI or from any checkout. Needs SQLcl 26.1+.

Validate everything exported in the project:

```bash
adtai validate
```

Validate specific applications, or an explicit path:

```bash
adtai validate -app 100
adtai validate -app 100 108
adtai validate -input ~/exports/f100/apexlang
adtai validate -input ~/exports/f100.zip
```

**Rules:**
- **The exit code is the deliverable.** `0` only when every requested folder validated clean, so it chains as a gate: `adtai export_apex -app 100 -apexlang && adtai validate -app 100`.
- Non-zero covers more than compiler errors, on purpose: `EMPTY` (folder holds no `.apx`), `NOT_FOUND` (SQLcl cannot find the path), an `-app` with no export on disk, a bare run that discovers nothing, and `UNRECOGNISED` (output this version cannot parse, reproduced verbatim). SQLcl exits `0` whatever the compiler says, so a run that checked nothing must never report clean.
- `-app` is repeatable and resolves offline through `config/internal/apex.db`, no database round-trip. An app with no export gives a `NOTES:` row naming the expected path, not a traceback.
- With neither `-input` nor `-app`, every `apexlang/` folder under the APEX root is validated, the natural follow-up to an `-all` export.
- One `ERRORS:` section per folder, one stanza per message: `file:line:col`, then the type and the verbatim compiler message indented beneath. Wrapped at 80 columns, never truncated, the message names the missing file, so cutting it would drop the answer. `-silent` keeps the banner, message sections, and timer; `-debug` shows the generated SQLcl script.
- Warnings never fail the run (the compiler still says successful) but are never hidden either: the row reads `OK (n warnings)` and a `WARNINGS:` section lists them. `FILE_IGNORED` matters most, it means that file was not checked at all.
- **Static-file payloads are staged in, so export a `files/` slice alongside `-apexlang`.** The APEXlang export skips the `shared-components/static-files/` payloads by design and `static-files.apx` references each one, so `validate` builds a staging tree under the gitignored `config/temp/apexlang/<app>/` that **hardlinks** the metadata and the `files/` payloads together, one inode per file, no bytes duplicated, nothing in git. Without a `files/` export you get one `REFERENCE_NOT_FOUND` per payload plus a `NOTES:` row naming `export_apex -files`. `-input` is never staged, so it shows the raw committed tree.
- **A zero-byte payload is a trap.** The compiler checks that a referenced path exists, not what is in it, so a tree of empty placeholders validates clean and then imports an app with broken images. Staging never touches a stand-in; if a `static-files/` folder is all-zero, it is not a valid export.
- The compiler validates against the metadata of the APEX version that exported the app, so an old SQLcl against a 26.1 export is not a trustworthy pass.
- Importing `.apx` back into APEX is not part of this command, `apex import` replaces the Builder application wholesale.

## export_data: export table data

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

## discovery: safe read-only SQL exploration

The first-choice command for "what's in this database?" questions. It is read-only by design: a static SELECT-only validator rejects anything else, and statements run under `SET TRANSACTION READ ONLY` which is rolled back. Runs write a Markdown report to `config/discovery/<YYYY-MM-DD--HH-MI>.md` by default (the folder is auto-gitignored); inline `-sql` results also print to the console. Add `-nolog` for throwaway exploration that must not write reports, `.gitignore` changes, or file-mode result blocks.

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

## dependencies: query the object graph

Answers "what uses this?" / "what would I break?" against a single gitignored SQLite mirror of the raw Oracle data dictionary at `config/internal/dependencies.db` (multi-schema `USER_*` tables stamped with `OWNER`, plus the APEX dictionary keyed by application id). Every query mode recomputes its answer from the raw mirrors at query time, day-to-day queries are offline; only a refresh touches the database.

**Name a query and it queries, name none and it refreshes.** The query modes are `-from`, `-to`, `-impact`, `-tree` and `-age`; an invocation carrying none of them rebuilds the mirror, so a bare `adtai dependencies` is a refresh and `-refresh` is just its explicit spelling. `-schema`, `-app`, `-force` and `-recent` scope that rebuild and describe one on their own; the last three steer the refresh, so passing one beside a query is refused.

There is no separate rebuild step, re-running the refresh incrementally patches the mirror: `-schema` detects added/changed/dropped objects by `LAST_DDL_TIME` and removes stale relations to/from dropped objects; object names or SQL wildcards after `-refresh` force a deep refresh for matching objects and dependency rows in both directions; `-force` wipes only the requested schema/app scope before reloading it:

```bash
adtai dependencies
adtai dependencies -env DEV -schema APP
adtai dependencies -refresh -env DEV -schema APP
adtai dependencies -refresh -recent -env DEV -schema APP
adtai dependencies -refresh CORE CORE% -env DEV -schema APP
adtai dependencies -refresh -force -env DEV -schema APP
```

`-refresh -app` patches the APEX dictionary mirror for one or more application ids (repeat the flag or space-separate ids under one `-app`) and ranges, `MIN-MAX` (closed) or `MIN+` (open), resolved against the apps discovered across the configured schemas:

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

On a multi-schema mirror an object name (e.g. `PACKAGE.CORE`) can be ambiguous across owners. Add `-schema OWNER [OWNER ...]` (space- or comma-separated, repeatable) to any query mode as an offline, case-insensitive owner filter that disambiguates by the owner column the mode matches on, `-from` filters the dependent `OWNER`, `-to` filters `REFERENCED_OWNER`, `-impact` constrains only the seed's `REFERENCED_OWNER` (the transitive walk is unchanged). It is parsed locally (no DB connection); omitting it matches every tracked owner. `-schema` is the one flag that reads both ways, an owner filter here and the refresh scope when no query sits beside it; `-app`, `-force` and `-recent` are refresh-only and are rejected in a query.

```bash
adtai dependencies -from "PACKAGE.CORE" -schema APP
```

`-age` reports when each schema and APEX app scope was last refreshed, offline. Check it before trusting a query, a stale mirror answers confidently and wrongly, and this is the supported staleness check rather than reading the file's mtime:

```bash
adtai dependencies -age
```

`-format yaml` or `-format md` moves the chrome to stderr so stdout stays pipeable.

## recompile: recompile invalid objects

Dependency-aware retry built in. Supports PL/SQL compile flags (native/interpreted, optimization level, PL/Scope, warnings) and old-ADT-style overview with PL/Scope gap counts. The OBJECTS OVERVIEW carries `VALIDATED` beside `INVALID`, how many objects the run actually repaired, so a successful fix is visible rather than just an empty `INVALID` column. On a plain run, `-silent` drops the whole `OBJECTS OVERVIEW`/`INVALID OBJECTS`/`ROOT CAUSES` sequence, keeping only the connection block and timer; on a report mode (`-mviews`, `-synonyms`, `-disabled`, `-jobs`, `-trailing`) it keeps that mode's own header and drops only the per-object rows, the same convention `export_db -silent` uses.

Recompile invalid objects:

```bash
adtai recompile -env DEV
```

### Where to start when a schema is full of invalids

A run that leaves invalid objects prints a `ROOT CAUSES` section, always, there is no flag. It splits the leftovers into the objects that are actually broken and the ones that only fell over because something else did, and **only the first group is printed**: the knock-ons are already listed in `INVALID OBJECTS` a few lines above, so they need no section of their own. Roots are ordered by `BLAST`, the number of invalid objects that clear once this one compiles.

The section reads **after** `INVALID OBJECTS` and its `COMPILE ERRORS` list, so the whole run goes `OBJECTS OVERVIEW` → `INVALID OBJECTS` → `COMPILE ERRORS` → `ROOT CAUSES`, scroll to the bottom for the verdict, not the top.

`COMPILE ERRORS` is a list, not a table: `  <OBJECT TYPE>.<OBJECT NAME>`, then `    - <line>.<pos> <message>` per distinct error. Cascade rows (`PL/SQL: Statement ignored`, `PL/SQL: SQL Statement ignored`, `PL/SQL: Compilation unit analysis terminated`) never print, the error explaining them is always beside them, a message repeated at several positions prints once at its lowest `line.pos`, and a long message wraps rather than truncating. Every section names objects as `<OBJECT TYPE>.<OBJECT NAME>`; there is no `ID` column anywhere, and the type is part of the key because a schema can hold a `PACKAGE` and a `PACKAGE BODY` of the same name.

`CAUSE` separates three fixes a flat list blurs together: `SOURCE` (its own text does not parse), `MISSING` (something it needs is gone), `GRANT` (it exists and this schema has no privilege on it, recompiling will never fix that one, so reading it as `MISSING` sends you hunting for something already there), plus `UNKNOWN` for an object invalid with no compile error to explain it. **What to fix is listed under the table, not in it**, grouped by verdict, one `<OBJECT TYPE>.<OBJECT NAME> -> <culprit>` line each, because a qualified culprit runs longer than any 80-column cell. `SOURCE` and `UNKNOWN` name nothing, so their headings carry no list.

The ranking reads the compile errors, the stored source at the failing line for errors that name nothing (`ORA-00942` names no object and leaves no dependency row, so its position in `user_source` is the only pointer), and `config/internal/dependencies.db` for the edges the error text never carries. The mirror is read offline and never refreshed here, keep it current with `adtai dependencies -refresh -schema <SCHEMA>`, and a project without one simply ranks on the compile errors alone.

Force-recompile all with native code + optimization, scoped by type/name:

```bash
adtai recompile -env DEV -force -native -level 3 -type PACKAGE% -name XX%
```

Bare `-force` recompiles every matching object. Combined with a compile modifier (`-native`/`-interpreted`/`-level`/`-scope`/`-warnings`) it instead recompiles **only** the VALID PL/SQL objects whose current settings drift from the requested state (any one axis mismatch selects the object; non-PL/SQL types are skipped). A plain recompile with neither `-native` nor `-interpreted` leaves each object's code type untouched (`REUSE SETTINGS`), so it never flips a native object to interpreted. `-scope`/`-warnings` take `+` as an extra separator (`-warnings PERF+SEVERE`) alongside space/comma/repeated forms.

```bash
adtai recompile -env DEV -force -level 2    # only objects not already at optimize level 2
```

`-type`, `-name`, and `-schema` are shared filters, and every mode below is scoped by them, no mode carries a name pattern of its own, so `-mviews DEP%` is a parser error and `-mviews -name DEP%` is the way. `-type`/`-name` take multiple patterns (`-type PACKAGE VIEW`, `-type PACKAGE,VIEW`, or a repeated flag). `-type` speaks Oracle's vocabulary: bare `PACKAGE` means specifications, `PACKAGE BODY` bodies, `MVIEW`/`MATERIALIZED` both mean `MATERIALIZED VIEW`. `-schema` is repeatable, space- or comma-separated, and pattern-aware (`-schema APP CORE`, `-schema APP,CORE%`); each schema is an independent pass.

### Modes

Each flag below **replaces** the ordinary invalid-object recompile rather than adding to it, so the OBJECTS OVERVIEW does not print.

Report-only, these connect, read, and print, and change nothing:

```bash
adtai recompile -env DEV -synonyms
adtai recompile -env DEV -disabled -type TRIGGER
adtai recompile -env DEV -jobs -name APP%
```

- `-synonyms` maps each synonym to its target object, grouped by target owner, one privilege per row with `PRIV`/`GRNT`/`VALID`.
- `-disabled` lists disabled constraints, invalid/function-disabled indexes, and disabled triggers. It is the one report spanning several object types, so `-type` picks which of `CONSTRAINT`/`INDEX`/`TRIGGER`; bare `-disabled` reports all three.
- `-jobs` lists today's scheduler job runs, grouped by status.

Materialized views, reports, then acts:

```bash
adtai recompile -env DEV -mviews
adtai recompile -env DEV -mviews -name DEP% -force
```

`-mviews` compiles invalid MVs and refreshes stale ones using each view's **own** configured refresh method (a COMPLETE view is never flipped to FAST). With `-force`, every matching view is refreshed regardless of staleness.

**`-trailing` writes to the database.** It strips trailing whitespace from stored source via `CREATE OR REPLACE`, the fix for `export_db` diffing an untouched object on every export, since `export_db` strips trailing whitespace from every line it writes and the database's stored source does not:

```bash
adtai recompile -env DEV -trailing
adtai recompile -env DEV -trailing -type PACKAGE% -name APP%
```

There is **no preview and no dry run**, asking for `-trailing` is asking for the strip, and `-trailing -fix` is a parser error rather than the old spelling. The safety is structural rather than a confirmation prompt: an object with nothing to strip is never touched, and removing trailing whitespace cannot change behaviour. Scope with `-type`/`-name` to narrow the blast radius. Covers `PACKAGE`, `PACKAGE BODY`, `PROCEDURE`, `FUNCTION`, `TRIGGER`, and `VIEW`; wrapped objects, editioning views, and views carrying `WITH READ ONLY` / `WITH CHECK OPTION` are skipped, since none of those survive the rebuild. `CREATE OR REPLACE` invalidates dependents, so follow a sweep with a plain `adtai recompile`.

## ut: run utPLSQL test suites

Runs the utPLSQL (UT) test suites installed in the connected schema. **The exit code is the deliverable**: utPLSQL does *not* raise when a test fails, `ut.run` reports it and returns normally, so a caller that only watches for an exception sees a clean run.

```bash
adtai ut -env DEV
adtai ut -env DEV -name ICT_SEC%
adtai ut -env DEV -name ICT_SEC% ICT_COM%
adtai ut -env DEV -refresh
adtai ut -env DEV -silent
adtai ut -env DEV -verbose
adtai ut -env DEV -gate
adtai ut -env DEV -name ICT_SEC% -gate 90
```

A package is run only when **both** are true: its name matches config `ut_pattern` (default `'_UT$'`), and utPLSQL has parsed it as a `%suite` with at least one `%test`. `ut_pattern` is the selection contract, so production code can never be swept in; `-name` takes repeatable Oracle `LIKE` patterns (`%`, `_`, `\` to escape) and narrows, never widens, it selects **the suites to run**, and names itself in the `RUNNING TESTS FOR <PATTERNS>:` header. No `-name` means everything. `-schema A B` tests several schemas as separate console segments.

Non-zero covers four cases, not just a failed assertion:

- a test failed or errored;
- a suite ran but the reporter returned nothing or unparsable output;
- **nothing ran at all**, a zero-test run is a failure, not an empty pass, because that is exactly what a suite that stopped compiling looks like from outside;
- a tested package is below the `-gate` threshold.

A matched package that is `INVALID`, or that utPLSQL parsed no `%test` for, is **ignored**: no row in either table, no stanza, no effect on the exit code. It is not a suite, and `ut` reports suites; the vanished-suite case is still caught by the zero-test rule.

Output order is the point, and **no wait is spent on a finished screen** (`#359`), but the module opens **no section of its own** to say so (`#372`): the annotation rebuild and discovery run under the connection block's own header, and the coverage read runs with the dotted bar left open on screen, which is an announcement that costs no new string. Between the connection block and `SUMMARY PER SUITE:` the rest of the screen belongs to the mode, and since `#317` there are three: `RUNNING TESTS:` by default (a dotted progress bar, headed `RUNNING TESTS FOR <PATTERNS>:` when `-name` narrowed the run), `UNIT TESTS SUITES:` then `TEST RESULTS:` under `-verbose` (the roll-up, then a row per test), and nothing at all under `-silent`. Everything below is identical in all three. `UNIT TESTS SUITES:` rolls the **runnable** suites up *before* anything runs, two columns, the suite package and its test count, and inside `-verbose` it **always prints, empty list included**: a run that matched nothing reports it in the same shape as one that matched ten, and the exit code carries the failure. **It is `-verbose` output since `#348`**, because it answers "what is about to run" and the bar's own `  1145 TESTS` label answers that in one line, so on a schema of ninety suites the table was ninety rows in front of the report. `TEST RESULTS:` prints as the run proceeds, the package name lands before that suite blocks, its dotted rows once the verdict is known, and each row carries the test's **procedure name**, not the `%test` description utPLSQL reports as the JUnit `testcase name`. **A passing row's own right-hand text is its elapsed seconds, one decimal always present, not the word `PASS`**, since `#315`, `PASS` only ever restated that a row carried none of `FAIL`/`ERROR`/`SKIP`, where the timer is new information; `FAIL`/`ERROR`/`SKIP` still print their status word. Packages print A-Z; tests print in package-specification order (`ALL_PROCEDURES.SUBPROGRAM_ID`), never the reporter's. `ERRORS & FAILURES:` carries a wrapped stanza per non-passing test, headed `<STATUS> > <PACKAGE>.<TEST>`, status first, with a blank line above each (`FAIL` for a refused expectation, `ERROR` for a raised exception), **capped at config `ut_limit_errors` (20; `0` prints all)**, and past the cap the header itself reads `FIRST 20 ERRORS & FAILURES:`, an uncapped run on `ICT_OWNER` printed 397 stanzas over 3060 lines and pushed both tables off the terminal's scrollback, so the report was correct and unreadable. The counts are never capped, so the two disagreeing is the signal that there is more detail than the screen. The four status words are `PASS`, `FAIL`, `ERROR` and `SKIP`, and they are the same words the roll-up columns are headed with, but only `FAIL`/`ERROR`/`SKIP` still print in a result row, so a row can never read one of those three spellings under a header that reads another. `SUMMARY PER SUITE:` is the suites table again plus `PASS` / `FAIL` / `ERROR` / `TIMER` / `COVERAGE`, the verdicts blank wherever a count is zero and with no `TESTS` column, every test lands in exactly one verdict, so the total is derivable from the other three. Its header is a constant in every mode: the `-name` filter is stated once, one section up, by `RUNNING TESTS FOR <PATTERNS>:` (`#349`). `TIMER` is that suite's own wall clock to one decimal (`0.3`, `3.0`), fixtures and round trip included so the column accounts for the run's total, and it is one of the two columns a zero does not blank out of: `0.0` is a measurement, an empty cell would claim there was none. `-silent` drops whatever the mode would have printed there, the bar on a default run and both listings under `-verbose`, and keeps the banner, connection block, `SUMMARY PER SUITE:`, the timer, and `ERRORS & FAILURES:` whenever a run has any: it makes a green run quiet, not a red one unreadable. It **outranks `-verbose`**, so `-silent -verbose` is `-silent`: two flags about one region of the screen, and the one that removes it wins.

**The default `RUNNING TESTS:` bar is one 78-column row, bumped by finished suites and by nothing else**, `  66 TESTS ................ 31%    0:01:38`, the same `DottedProgressBar` `export_apex` uses, opening on the line directly under the dashed rule and labelled and indented two spaces exactly like an `export_apex` action row (`#322`). **The label counts tests while the bar counts suites**, on purpose: a suite is the only unit utPLSQL reports back, so it is the only thing that can move the bar honestly, while a test is the unit a run is sized in and is knowable before anything runs. The number is the `TESTS` column of the table above, summed, so `-name` narrows the label and the axis together. The percentage is suites completed over suites matched, so it moves when a suite returns and never in between: `ut.run` buffers a run's whole reporter output until it returns (measured 2026-08-13, a 12-test suite's `post-test` events all reached the client in a 0.5 s burst 22 s after `execute()`), which makes a suite the smallest unit of progress that exists to report. A per-suite elapsed-seconds ticker shipped for one release under `#301` and Jan rejected it the same day: *"You will print the package name, when you have a result you will print the rest. There is nothing in between."* **The time on the right is what is left**, seeded from `config/internal/ut_timers.yaml`, the previous run of this **schema and `-name` variant**, keyed together because `-name` selects the suites that run and so selects the job being timed, upper-cased and sorted so two spellings of one run share one history, `%` when unfiltered. Once suites have returned, the run's own rate is blended in by the completed fraction: early the stored figure knows more, by the last suite the sample *is* the run. The store is a rolling `(this run + previous) / 2`, the average `config/internal/apex.db` already uses, gitignored beside it; every mode records, and a run that executed nothing records nothing rather than seeding `0:00:00`. A run that matched no suite prints **no bar at all**, an empty table reports the same fact in the same shape as a full one, and a bar has no empty form.

`#317` replaced `-dense`, which is now **rejected outright**: it collapsed `TEST RESULTS:` to one `PASS <passed>/<total>` line per suite, and those counts were `SUMMARY PER SUITE:` one section early.

`-refresh` rebuilds utPLSQL's annotation cache before discovery. A package compiled since the last run is not in that cache yet, so it is not discoverable and is silently ignored, `-refresh` is the first thing to try when a suite you know exists is missing from `ut -verbose`'s `UNIT TESTS SUITES:`. `ut` runs suites; it never installs them.

**The naming convention is four config values, not flags.** All are **Oracle** regular expressions, matched case-insensitively, and Oracle evaluates them: `REGEXP_LIKE` selects the test packages inside the dictionary query, where the old `LIKE` sat, so a schema of thousands of packages is never fetched to find a handful of suites, and `REGEXP_SUBSTR` extracts the capture groups in the same pass. `ut_owner` is the only one that defaults empty. `ut_pattern` (`'_UT$'`) selects test packages. `ut_match` (`'^(.+)_UT$'`) pairs one back to the package it tests through capture group 1, the pairing the `COVERAGE` column is built on, so a project whose suites are `TEST_ABC` sets `'^TEST_'` and `'^TEST_(.+)$'` and everything follows. `ut_owner` names the schema holding the suites when they do not live beside the code; it scopes discovery, the annotation cache, `-refresh` and the `ut.run` path, while coverage is still measured in the schema under test. `ut_module` names the module a suite belongs to (`'^[^_]+_([^_]+)'` reads `SEC` off `ICT_SEC_SECURITY_UT`): the run then prints a `SUMMARY PER MODULE:` table under `SUMMARY PER SUITE:`, `MODULE NAME` / `PACKAGES` / `LINES` / the same verdict, `TIMER` and `COVERAGE` columns, ending on a total row whose module name is blank. `LINES` is the group's size in code: the body-line total of the packages its suites test, each counted once however many suites name it, which is the same deduplicated set `COVERAGE` is computed over and the denominator that figure is scaled by. A group the expression could not name reads `?`, never blank, so it is not read as a second total. It ships set, so the table prints by default; `ut_module: ''` is how a project without a module convention turns it off. **Anchor it to nothing that has to follow the module token**: a trailing `_` cannot read `ICT_VPD`, a module whose whole implementation is one package, which is what put one package of Jan's 58 in the `?` row.

**Coverage is measured on every run** and lands as the `COVERAGE` column, after `TIMER`, in both tables. There is no `-coverage` flag: it was a mode until card `#291`, printing a `CODE COVERAGE:` / `NO CODE COVERAGE:` pair and a roll-up of its own instead of the run report, and Jan folded the figure into the run's own tables. The percentage is right-aligned and rendered to one decimal place with no `%` (`88.0`, `41.9`) so the figures stack under each other. **It is run-scoped**: a `SUMMARY PER SUITE:` row carries the figure for the package that suite tests, paired through `ut_match`. **Three cells, three different reports, split apart by card `#436`**: `?` (`cells.UNPAIRED_COVERAGE`) is a suite that resolved to no package at all, so the run never worked out WHAT to measure; a blank is a suite that did resolve and whose target Oracle never instrumented, natively compiled code carrying none; and `0.0` is reserved for code that *was* instrumented and never entered. The first two were one blank cell until `#436`, which is why a green suite whose target does not exist used to read as a defect. **The derived name is resolved against the schema's own package list before anything is measured** (`ut/coverage.py`, `resolve_targets`), and a name matching no package falls back to the longest existing package it is a prefix of: `ict_int_ariba_pushback_ut` derives `ICT_INT_ARIBA_PUSHBACK`, which is no package, and lands on `ict_int_ariba`. The walk drops one `_` segment at a time and invents nothing when it runs out, so `ICT_TRG_REFACTOR_UT` tries `ICT_TRG_REFACTOR`, then `ICT_TRG`, then `ICT`, matches none and prints `?`. Only `object_type = 'PACKAGE'` is ever a candidate, so a suite exercising **triggers** can only ever read `?`; its blocks are credited to whichever packages the trigger bodies call, under the suites that name those. Two suites testing one package therefore print the same figure, because block coverage records which blocks ran and never which test ran them. A `SUMMARY PER MODULE:` row and the total aggregate that group's target packages through one shared helper, so a group and the total under it can never be two calculations: the pooled block figure is scaled by the share of the group's body lines Oracle measured at all, which means a target nothing reached pulls its module down and a group nothing reached reads `0.0`. **A package no suite pairs to is not in the output at all**, that is the accepted cost of run-scoping, and it is what the removed `NO CODE COVERAGE:` work list used to show. **`-name` narrows the run and the figures follow it** (Jan, `#231`); a package reached only by an excluded suite reads lower than the truth. `PLSQL_OPTIMIZE_LEVEL` is **not** a prerequisite: level 2 is Oracle's default and block coverage is collected there anyway. Collection is utPLSQL's own, `DBMS_PROFILER` and `DBMS_PLSQL_CODE_COVERAGE` in parallel on 12.2+, never hand-rolled, and the percentage is the block figure with `COVERAGE`-pragma `NOT_FEASIBLE` blocks subtracted from the denominator, which makes it read slightly higher than utPLSQL's own HTML report, deliberately.

**`-gate [N]` turns that column into a pass/fail condition.** `-gate 90` sets the threshold for this run, bare `-gate` reads config `ut_coverage_gate` (ships at `80`), and no `-gate` at all gates nothing, the flag is opt-in and its absence is not a threshold of zero. The whole report prints first and a `COVERAGE BELOW <n>:` table closes it, listing the packages under the bar worst first; one is enough to make the run non-zero, and it fails a run whose tests all passed. Only a package with a **measured** figure is compared: a blank cell has nothing to compare, while `0.0` is a real measurement and does gate. At the boundary `>=` passes. There are no per-package thresholds, `-name` already narrows a run, so `-name CORE% -gate 90` sets a stricter bar for one group.

## patch: build and deploy patches from commits

Reads commits, resolves dependencies, orders objects, and generates deployment scripts. Group order follows `patch_map` in `config.yaml`; within a group, objects are ordered from `config/internal/dependencies.db`, **both** halves of it: `USER_DEPENDENCIES` for PL/SQL and views, and `USER_CONSTRAINTS` for the table-to-table foreign keys Oracle does not record there, so a table always follows the table it references.

The graph has to describe the objects it orders. `-create` **refreshes the stale schemas itself** and continues, opening the standard `CONNECTING TO SCHEMA <schema>, <environment>:` block and an `UPDATING DEPENDENCIES:` section over that schema's count rows as it goes; `-install` still **refuses to run** on a `config/internal/dependencies.db` that is missing, unreadable, or older than what it would order, because it orders every install target and so has no narrower scope to refresh. Either way a run that cannot produce a usable graph exits non-zero without writing, naming each stale schema, the stamp it was measured against (the same `last_refresh` rows `dependencies -age` prints), the object that outran it, and a scoped `adtai dependencies -refresh -schema <OWNER>`. Read-only previews are never gated, and a run with no objects to order needs no graph.

A schema is one scope whatever case its name was spelled in, and a mirror written by an older ADT that holds both `ICT_OWNER` and `ict_owner` is folded to the newer of the two on the next `dependencies -refresh`. Until then the gate reads the newer stamp, so a repeat of the same `-create` cannot refuse a graph it just refreshed.

Every `patch` run also levels the branch's commit store from git before it does anything, `-install` and `-archive` included, so the commit NUMBERS it writes into a patch folder always describe the repository you are looking at.

Rebuilding a patch folder that has already been deployed is refused, because its deploy logs record what a database received. `-force` gets past that as a refresh: the install scripts are regenerated and the deploy logs and `patch_scripts/` are kept.

Regenerate the database install script from the exported objects, no patch code, no connection:

```bash
adtai patch -install
```

It writes one `INSTALL.sql` per exported schema at the schema's objects root (`<schema>/database/INSTALL.sql` under the default `path_objects` template), and prints an objects overview plus the path per schema rather than the script body.

Three verbs, one job each: `-name` looks and acts on nothing, `-create` builds, `-deploy` ships, and the name sits on whichever one is acting. Running it bare answers "what is going on", the recent commits and then the patch folders:

```bash
adtai patch
```

Inspect one patch, its commits, its contents and its files, building and deploying nothing:

```bash
adtai patch -target UAT -name TASK_ID
```

Create, then deploy, two runs, never one:

```bash
adtai patch -target UAT -name TASK_ID -create
adtai patch -target UAT -name TASK_ID -deploy
```

Read the patch first and then append `-deploy` to the same line, which is the other half of that habit. `-deploy` takes its name from `-create` or from `-name` when it carries none of its own, so neither review has to be retyped:

```bash
adtai patch -target UAT -name TASK_ID
adtai patch -target UAT -name TASK_ID -deploy
```

`-deploy` ships the patch as it stands on disk: it never creates a folder, rewrites a script or re-orders files, so what deploys is what was reviewed. Pass `-hash`, `-baseline`, `-local`, `-head`, or `-nosnap` alongside it and they are ignored, named under an `IGNORING WITH -deploy:` header rather than silently dropped. `-name NAME -create -deploy` is not refused work either: it is how the name arrives, and an existing folder still deploys unchanged, so only a name with no folder behind it is built first and then deployed. A name on `-deploy` itself always wins over a borrowed one, and `-create` never borrows, so `-name NAME -create` is still an error. A target that already recorded `SUCCESS` for this patch is skipped on redeploy; `-force` re-runs it anyway.

Cherry-pick commits and ignore some. `-commit` and `-ignore` both take a number or hash prefix, an open-ended `20+` (that commit and everything newer), and a closed `1-20` span:

```bash
adtai patch -target UAT -name TASK_ID -create -commit 1-20 -ignore 5
```

The commit scan walks the checked-out branch by default; `-branch NAME` walks a different branch's history instead, an unknown name stops the run rather than falling back to `HEAD`. The run reads its limits from `patch_scan_commits` (how far the scan walks, and the reach of `-commit N`), `patch_show_commits` and `patch_show_patches` (how much prints).

**Three flags narrow the whole preview screen, both tables of it, not the commits alone.** `-my` limits it to your own work and `-by NAME` to one author's, matched as a case-insensitive substring of the commit author **email**, which is the identity the shared commit store records (`-by "Jan Kvetina"` matches nothing; `-by kvetina` matches the address). A patch folder carries no author of its own, so it is attributed through the commit numbers its install script records, and one of your commits is enough; a folder nothing can attribute, because it predates the header or its commits fall outside `patch_scan_commits`, is dropped rather than shown under a filtered heading:

```bash
adtai patch -target UAT -my
```

`-recent` is the third, and it takes the same window `export_db` takes, whole days or a fraction of one. **A whole-day window counts today as one of its days**, so `-recent 1` is today, `-recent 7` is today plus the six before it, and yesterday needs `-recent 2`; bare `-recent` is `1`. Folders are dated by the `yymmdd-` prefix in their name rather than by an mtime, so the window survives a copy; a folder whose name carries no parsable day is kept rather than hidden:

```bash
adtai patch -target UAT -recent 1
adtai patch -target UAT -my -recent 7
```

`patch_commit_pattern` in `config.yaml` is a project-wide subject filter, set it to something like `'([A-Z0-9]+\-[0-9]+\-?[0-9]*)'` and a commit carrying no ticket reference is never patched. Empty is the default. An explicit `-search` or `-commit` bypasses it.

`-create` snapshots the **committed** version of each file, the blob at its newest commit inside the patch window, so an uncommitted working-tree edit cannot leak into a deployment. Three mutually exclusive flags override that: `-local` snapshots the working-tree file, `-head` snapshots the file at git `HEAD` (and suppresses the newer-commit warning), and `-nosnap` writes no snapshots at all, linking each repo file where it already lives. Passing two exits `2`.

`-create` opens with `RELEVANT COMMITS:` (and `RECENT UNPATCHED COMMITS:` when the window still holds unpatched ones), then prints a section per schema as it builds: `ALTER STATEMENTS:` for generated `ALTER` scripts and `DELETED OBJECTS:` for what the window dropped, then `PROCESSED FILES: <s>`, every row a plain dash with nothing trailing it. **A file list groups under its folder**: one `  - <schema>/database/<type>/` line, trailing slash kept, with each file two spaces further in and any `export_db -groups` sub-folder left on the leaf (`    - CORE/core_logs.sql`). Anything hanging off a row sits two spaces further again, so the newer commits under `WARNING - OUTDATED FILES:` land at six. `nested_files: False` in `config.yaml` gives the flat one-path-per-row list instead, and it governs `export_db` and `search_repo` lists too. Each warning is a section of its own: `WARNING - UNCOMMITTED FILES:` only when a listed file genuinely has uncommitted changes in git and never under `-local`, and `WARNING - OUTDATED FILES:` naming any file whose shipped version is older than a commit that already exists, with those newer commits listed under it. Two more sit below the schema loop and describe the patch rather than a schema: `WARNING - OBJECTS CHANGED:` names every object the database has moved past since it was exported, `  - <TYPE> <NAME>` and nothing else, meaning this patch ships the older exported body for it, and `WARNING - NO DATABASE CLOCK:` names an owner whose mirror predates `#394` so that comparison could not be made at all. Neither stops the build. `PATCH FILES:` closes the screen. `-name <name>` and `-deploy` print `PATCH CONTENTS: <SCHEMA>`, one section per schema in install order.

**Every section header on that screen carries exactly two blank lines above it**, whatever printed above it, the command's own `APEX DEPLOYMENT TOOL - PATCH` banner excepted. It is the renderer that guarantees it, so the rule holds for every command, not just `patch`.

Per-patch scripts in `patch_scripts_dir/<CODE>/` **move** into the patch folder on `-create`, generated helpers and hand-written one-offs alike, and each statement is wrapped in an existence check on the way, so a second deploy is a no-op instead of ORA-01430. A later `-create` for the same patch code recovers them from the previous patch folder. A script no selected commit touched stays put under `WARNING - IGNORED SCRIPTS:`; one in a slot no `patch_map` group can produce stays put under `WARNING - UNKNOWN SCRIPTS:`. Templates in `patch_template_dir` are unaffected: they are linked where they live, never moved.

`-archive` zips delivered patch folders into `patch_archive/` and removes them from `patch/`. A ref is the patch's card number when it is all digits, read off the first segment of the patch code and so visible in the `FOLDER` column itself (`260809-1-66_LAYER0_FIX` is patch `66`), and a SQL LIKE pattern otherwise, matched against the folder name, the patch code, and the folder name with its `yymmdd` day rewritten as `YYYYMMDD`, so a whole month goes in one command:

```bash
adtai patch -target UAT -archive 202608%
adtai patch -target UAT -archive 66 67
adtai patch -target UAT -archive %
adtai patch -target UAT -archive
```

**Omitting refs archives nothing** (`#513`): a bare `-archive` only lists what is on disk, so you can read the inventory and then name what should go. `-archive %` is the sweep. Refs that match nothing archive nothing and exit 0, because a sweep over a pattern that is legitimately empty is not a failure (`#355`).

The receipt is one table, `FOLDER | STATUS`, the same columns `ALL PATCH FOLDERS:` prints under it (`#513`, replacing the `ID | PATCH CODE | FOLDER` shape `#346` had restored). That listing holds every folder still on disk, newest first, uncapped and unfiltered, so the next pattern has something to aim at (`#510`). A run whose refs matched nothing therefore answers with the whole inventory, and a run that named nothing prints only the listing.

### Hash mode: patch what no longer matches the baseline

A patch built from what your repo no longer agrees with the target about, rather than from commits. Record a baseline, work for as long as you like, then patch whatever moved. The help screen groups the two flags under their own `HASH MODE:` section, `-hash` first:

```bash
adtai patch -target UAT -baseline
adtai patch -target UAT -hash
adtai patch -target UAT -name TASK_ID -create -hash
adtai patch -target UAT -name TASK_ID -deploy
```

`-baseline` means hash everything: every file the layout resolves, written whole to `patch_hashes/<TARGET_ENV>/baseline.log`, one `file | commit | hash` line each. Keep it in git, its history is the record of what each environment holds. It needs no database and builds nothing.

`-hash` compares the working tree against that file and reports each difference as `MODIFIED`, `NEW` or `DELETED` under `CHANGED FILES:`; `-create ... -hash` builds a patch of exactly those. Nothing is bounded by `patch_scan_commits`, so a file changed long ago and never deployed is still patched, and an uncommitted edit is a change like any other. The mode forces the `local` content mode, so what was compared is what ships and `-head`/`-nosnap` beside it exit `2`.

Both flags take an optional FILE, which is then the whole address and makes `-target` unnecessary:

```bash
adtai patch -target UAT -name TASK_ID -create -hash hashes/alternative.log
```

**A successful deploy advances the baseline, for a hash-built patch only.** `-create -hash` records what it shipped in the patch folder's `hashes.log`, and that file's presence is what marks the patch; the deploy merges only those files, only for install scripts that succeeded, and a commit-built patch advances nothing. Do not mix the two modes. Handing the patch to a DBA instead means running `-baseline` yourself once it is in, and doing it before further work, since a full snapshot records the tree as it stands.

A table whose baseline version is no longer in the scanned history gets no `ALTER` helper and is named under `WARNING: NO TABLE BASELINE`; the column change is yours to write into `patch_scripts/`.

Full flag set in `docs/patch.md`. ADT.ai no longer accepts old placeholder source flags; use the default commit-resolved files, hash mode, or the explicit create/deploy/install/archive actions.

## search_repo: search Git history

Git-only history search for commit summaries, changed file paths, ADT-style database object type/name, authors, dates, numbers, and hashes. It searches the shared `adtai rebuild` commit store at `repo_commits_file` (default `config/commits/<branch>.db`); no Oracle connection is required.

Search changed packages and object names. `-type`, `-name` and `-by` are SQL LIKE patterns, anchored and case-insensitive, exactly as `export_db` reads its own `-type`/`-name`, so `%` covers any run of characters and `_` a single one. `-summary` and `-file` are the exception and search free text for AND-matched words:

```bash
adtai search_repo -file packages -name ORDER_API
adtai search_repo -file packages -name ORDER_API -files
adtai search_repo -type "PACKAGE%"
```

Search author/date scope. A partial address needs its own wildcard:

```bash
adtai search_repo -by bob@example.com -since 2026-06-01 -until 2026-06-10
adtai search_repo -by "bob%"
```

Select commits by git hash prefix, repeatable. `patch -hash` is the other flag of that name and means something else entirely, the baseline file hash mode compares against, so the two never take the same kind of value:

```bash
adtai search_repo -hash a1b2c3 9f8e7d
```

Restore historical versions beside the original file, or stage one version to the original path:

```bash
adtai search_repo -file order_v -commit 42 45 -restore
adtai search_repo -file order_v -commit 42 -restore -stage
```

## rebuild: refresh the commit store

Incremental by default; one store per branch at `config/commits/<branch>.db`, shared with `patch`, `search_repo` and `calendar`. To rebuild a branch from scratch, delete its `.db` and re-run. A commit's number is allocated once and never re-derived, so a merge, a bounded window, or a full rebuild all leave existing numbers where they are. `patch_history_bottom_days` (default 365) bounds how far back a from-scratch build reaches. `-verify` reports a store's numbering read-only; `-reveal` is a read-only remote-branch inspector; `-reveal -switch N` checks out the Nth filtered branch.

```bash
adtai rebuild
adtai rebuild -verify
adtai rebuild -reveal -my
```

## doctor: setup checks, updates, and project bootstrap

Plain `doctor` is read-only: it checks local tools, environment variables, Python dependencies, Instant Client, SQLcl, and online update availability. It does not update ADT.ai, reinstall requirements, download SQLcl, or create files.

The closing `ACTIONS:` section lists only upgrades an online check actually found: `-update` appears when ADT.ai, `oracledb`, or SQLcl is behind, `-sqlcl` only when SQLcl is. With everything current, or under `-offline`, where nothing was checked, the section is absent entirely.

The `ADT.ai` version row reads `<version> + WIP` when you are running from a git checkout: `__version__` is the last released number, and a checkout carries commits that release never shipped. An installed copy shows the bare version.

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

Land one named release instead of the latest one. The version scopes to the ADT.ai step alone, so requirements and SQLcl still follow it, and a version older than the one installed is installed like any other: this is how you step back off a release that broke you, or match the version a colleague is running. A git checkout resolves the release tag in its own `origin`, so the DEV repo, which carries no release tags, refuses the run naming the version and the remote rather than falling back to latest. Going below the release that added the flag leaves you on that release's own `doctor`, which rejects a version argument and cannot pull off its detached HEAD; `git checkout main && git pull && pip install -e .` is the way back and loses nothing:

```bash
adtai doctor -update 0.9.1
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
4. Build the patch: `adtai patch -target UAT -name TASK-123 -create`.
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
adtai patch -target UAT -name TASK-123 -create
adtai patch -target UAT -name TASK-123 -deploy
```
