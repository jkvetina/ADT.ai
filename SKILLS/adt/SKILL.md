---
created: 2026-06-10
updated: 2026-08-08
name: adt
version: 1.7.12
tags: [oracle, apex, deployment, cli, database]
description: "ADT.ai usage guide for Oracle/APEX work: export database objects, APEX apps and data, validate APEXlang source, run utPLSQL test suites, run read-only SQL discovery, search Git history, query the dependency graph, and recompile invalid objects. Use for any ADT.ai command help."
---
# ADT.ai

ADT.ai is a Python CLI that exports, inspects, and deploys Oracle Database objects and APEX applications. It reads from config files, Git, and the database; it never stores its own metadata in the database. Exports work against any ordinary folder — a Git repository is useful but not required.

The command is `adtai` (aliases: `adt`, `python -m adt_ai`). Full argument tables for every command live in per-command files under the repo's `USAGE/`; this skill is the operating cheat-sheet for the common commands, including the full `doctor` module. Lower-frequency commands (`connection`, `calendar`, `flow`) are not expanded here — see their pages under `USAGE/`. The repo-only `adt-setup` skill remains a deeper one-time setup checklist, not a daily runtime skill.

Run commands from the project root (the folder holding `config/` and the export output). Every command prints a standard banner, dashed section headers, and a final `TIMER: Ns` footer. `export_db`, `export_data`, `export_apex`, `recompile`, and `dependencies -refresh` accept a multi-schema `-schema A B` list; a multi-schema run prints the banner once, then executes schema by schema — connect, that schema's full output, its own `TIMER` — before moving to the next, so a `-schema A B` run reads as two single-schema runs back to back, not one interleaved pile.

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

Everything changed since your last export of each schema — bare `-recent` uses the per-schema watermark in the gitignored `config/recent.yaml` (a schema with no recorded export yet exports in full and seeds it; narrowed or dry runs never advance it):

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

Jobs export separately — `JOB` objects have no reliable `last_ddl_time`, so **never** combine `-type JOB` with `-recent`:

```bash
adtai export_db -silent -type JOB
```

Filter by author in a shared schema worked through proxy users — `-by <NAME>` for a specific db user/schema, `-my` for yourself (db schema read from the gitignored `config/IDENTITY.yaml`). Both resolve authorship against the project's configured `audit:` source (a DDL-log table/view), so they need no DBA audit-trail access; without an `audit:` block in `config.yaml` they exit `2`:

```bash
adtai export_db -silent -by SCOTT
adtai export_db -silent -my
```

Add the optional 4th `audit:` key `changed_at` (the DDL timestamp) and the filter becomes time-aware: an object you worked on that somebody else changed **last** is still exported but marked `[OTHER_AUTHOR]` on its row, and `-recent` narrows the DDL log as well as `user_objects`, so `-my -recent 1` means "changed within a day, by me" instead of ANDing two unrelated sources. Without the key the filter can only answer *ever touched* — an unordered log cannot rank.

Authorship has to be written at DDL time: Oracle records no actor, and in a proxy session `USER`/`ORA_LOGIN_USER`/`SESSION_USER` all return the *proxied schema*. The project's DDL trigger must record `COALESCE(SYS_CONTEXT('USERENV','CLIENT_IDENTIFIER'), SYS_CONTEXT('USERENV','PROXY_USER'), USER)` — ADT.ai publishes `db_schema` into `CLIENT_IDENTIFIER` on every connection, and `PROXY_USER` is the only place a proxy's real user appears.

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
adtai export_apex -app 100 -page 7 -deep
adtai export_apex -app 100 -component LOV:%
```

Fingerprint an application for a deploy gate:

```bash
adtai export_apex -app 100 -checksum
```

Export APEXlang `.apx` source (APEX 26.1+):

```bash
adtai export_apex -app 100 -apexlang
adtai export_apex -app 100 -apx
```

**Rules:**
- Name the formats explicitly. Common set: `-full -split -files -rest -readable`. Skip `-embedded` unless asked — it slows the export.
- `-apexlang` (alias `-apx`) writes the APEXlang folder tree to `apexlang/` in the app folder: `application.apx`, `pages/`, `shared-components/`, `workspace-components/`, `deployments/`, `.apex/`. Members land verbatim, and the folder is recreated per export so deleted components leave no stale `.apx`. Whole-app format: `-page`, `-component`, and `-recent` never filter it and it never advances a watermark. Static-file payloads are skipped on purpose — `-files` stays the single static-file channel, so `apexlang/` is a source and review surface, not a directly importable artifact.
- APEX 26.1 is the version line for both directions: `-apexlang` needs 26.1+ and prints a skip note (never a failure) below it when you named the format yourself — under `-all` the skip is silent — while `-readable` no longer exists at 26.1+ and is skipped silently there. Use `-readable` pre-26.1, `-apexlang` from 26.1 on.
- Check an APEXlang export with `adtai validate` (below) — it is the compiler check that makes `.apx` safe to edit.
- `-checksum` writes the ID-independent SHA-256 application fingerprint to `checksum.txt` in the app folder. It answers "did anything actually change?" without diffing a full export — `git diff --exit-code` on that file is the CI gate. Whole-app format: `-page`, `-component`, and `-recent` never filter it out, and it never advances a `-recent` watermark.
- `-recent N`, `-page`, and `-component TYPE:NAME%` filter split/readable/embedded component output. Bare `-recent` means "changed since the last export of this app in this format" — a watermark keyed per environment/app/format in the gitignored `config/recent.yaml`; each exported format advances its own key, report-only `-recent` never does. `-page` or `-component` without an explicit format defaults to `-split`. Add `-deep` only with `-page` when the export should include components recorded for those pages in `config/dependencies.db`, such as LOVs, lists, and authorization schemes. Filtered component exports print affected rows instead of dotted progress and do not update `apex_timers.yaml`. Full app SQL, REST services, app files, and workspace files stay broad. With `-reveal`, `-recent` filters the listed apps.
- If apps don't appear, the connection's APEX schema likely doesn't match the owner — narrow or widen with `-schema`, or use `-owners` in reveal.
- `-rest` runs through SQLcl with a named `ADT_…` connection: registered automatically on first use (password in SQLcl's secure store, wallet included), recorded as `sqlcl:`/`sqlcl_sync:` in the connection YAML, re-registered automatically after a credential change. Opt out with `sqlcl_named_connections: false` in `config.yaml`. Details: `USAGE/connection.md` §Named SQLcl connections.

## validate — check exported APEXlang source

Runs the APEXlang compiler over exported `apexlang/` folders and reports its errors. **Never connects** — the compiler ships inside SQLcl, so there is no `-env`, no `-schema`, no credentials, and it works in CI or from any checkout. Needs SQLcl 26.1+.

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
- `-app` is repeatable and resolves offline through `config/apex_apps.yaml` — no database round-trip. An app with no export gives a `NOTES:` row naming the expected path, not a traceback.
- With neither `-input` nor `-app`, every `apexlang/` folder under the APEX root is validated — the natural follow-up to an `-all` export.
- One `ERRORS:` section per folder, one stanza per message: `file:line:col`, then the type and the verbatim compiler message indented beneath. Wrapped at 80 columns, never truncated — the message names the missing file, so cutting it would drop the answer. `-silent` keeps the banner, message sections, and timer; `-debug` shows the generated SQLcl script.
- Warnings never fail the run (the compiler still says successful) but are never hidden either: the row reads `OK (n warnings)` and a `WARNINGS:` section lists them. `FILE_IGNORED` matters most — it means that file was not checked at all.
- **Static-file payloads are staged in, so export a `files/` slice alongside `-apexlang`.** The APEXlang export skips the `shared-components/static-files/` payloads by design and `static-files.apx` references each one, so `validate` builds a staging tree under the gitignored `config/temp/apexlang/<app>/` that **hardlinks** the metadata and the `files/` payloads together — one inode per file, no bytes duplicated, nothing in git. Without a `files/` export you get one `REFERENCE_NOT_FOUND` per payload plus a `NOTES:` row naming `export_apex -files`. `-input` is never staged, so it shows the raw committed tree.
- **A zero-byte payload is a trap.** The compiler checks that a referenced path exists, not what is in it, so a tree of empty placeholders validates clean and then imports an app with broken images. Staging never touches a stand-in; if a `static-files/` folder is all-zero, it is not a valid export.
- The compiler validates against the metadata of the APEX version that exported the app, so an old SQLcl against a 26.1 export is not a trustworthy pass.
- Importing `.apx` back into APEX is not part of this command — `apex import` replaces the Builder application wholesale.

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

## dependencies — query the object graph

Answers "what uses this?" / "what would I break?" against a single gitignored SQLite mirror of the raw Oracle data dictionary at `config/dependencies.db` (multi-schema `USER_*` tables stamped with `OWNER`, plus the APEX dictionary keyed by application id). Every query mode recomputes its answer from the raw mirrors at query time — day-to-day queries are offline; only `-refresh` touches the database.

There is no separate rebuild step — re-running `-refresh` incrementally updates the mirror: `-schema` detects added/changed/dropped objects by `LAST_DDL_TIME` and removes stale relations to/from dropped objects; object names or SQL wildcards after `-refresh` force a deep refresh for matching objects and dependency rows in both directions; `-force` wipes only the requested schema/app scope before reloading it:

```bash
adtai dependencies -refresh -env DEV -schema APP
adtai dependencies -refresh -recent -env DEV -schema APP
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

On a multi-schema mirror an object name (e.g. `PACKAGE.CORE`) can be ambiguous across owners. Add `-schema OWNER [OWNER ...]` (space- or comma-separated, repeatable) to any query mode as an offline, case-insensitive owner filter that disambiguates by the owner column the mode matches on — `-from` filters the dependent `OWNER`, `-to` filters `REFERENCED_OWNER`, `-impact` constrains only the seed's `REFERENCED_OWNER` (the transitive walk is unchanged). It is parsed locally (no DB connection); omitting it matches every tracked owner. `-app`/`-force` stay refresh-only.

```bash
adtai dependencies -from "PACKAGE.CORE" -schema APP
```

`-age` reports when each schema and APEX app scope was last refreshed, offline. Check it before trusting a query — a stale mirror answers confidently and wrongly, and this is the supported staleness check rather than reading the file's mtime:

```bash
adtai dependencies -age
```

`-format yaml` or `-format md` moves the chrome to stderr so stdout stays pipeable.

## recompile — recompile invalid objects

Dependency-aware retry built in. Supports PL/SQL compile flags (native/interpreted, optimization level, PL/Scope, warnings) and old-ADT-style overview with PL/Scope gap counts. The OBJECTS OVERVIEW carries `VALIDATED` beside `INVALID` — how many objects the run actually repaired, so a successful fix is visible rather than just an empty `INVALID` column.

Recompile invalid objects:

```bash
adtai recompile -env DEV
```

### Where to start when a schema is full of invalids

A run that leaves invalid objects prints a `ROOT CAUSES` section, always — there is no flag. It splits the leftovers into the objects that are actually broken and the ones that only fell over because something else did, and **only the first group is printed**: the knock-ons are already listed in `INVALID OBJECTS` a few lines above, so they need no section of their own. Roots are ordered by `BLAST`, the number of invalid objects that clear once this one compiles.

The section reads **after** `INVALID OBJECTS` and its `COMPILE ERRORS` list, so the whole run goes `OBJECTS OVERVIEW` → `INVALID OBJECTS` → `COMPILE ERRORS` → `ROOT CAUSES` — scroll to the bottom for the verdict, not the top.

`COMPILE ERRORS` is a list, not a table: `  <OBJECT TYPE>.<OBJECT NAME>`, then `    - <line>.<pos> <message>` per distinct error. Cascade rows (`PL/SQL: Statement ignored`, `PL/SQL: SQL Statement ignored`, `PL/SQL: Compilation unit analysis terminated`) never print — the error explaining them is always beside them — a message repeated at several positions prints once at its lowest `line.pos`, and a long message wraps rather than truncating. Every section names objects as `<OBJECT TYPE>.<OBJECT NAME>`; there is no `ID` column anywhere, and the type is part of the key because a schema can hold a `PACKAGE` and a `PACKAGE BODY` of the same name.

`CAUSE` separates three fixes a flat list blurs together: `SOURCE` (its own text does not parse), `MISSING` (something it needs is gone), `GRANT` (it exists and this schema has no privilege on it — recompiling will never fix that one, so reading it as `MISSING` sends you hunting for something already there), plus `UNKNOWN` for an object invalid with no compile error to explain it. **What to fix is listed under the table, not in it** — grouped by verdict, one `<OBJECT TYPE>.<OBJECT NAME> -> <culprit>` line each, because a qualified culprit runs longer than any 80-column cell. `SOURCE` and `UNKNOWN` name nothing, so their headings carry no list.

The ranking reads the compile errors, the stored source at the failing line for errors that name nothing (`ORA-00942` names no object and leaves no dependency row, so its position in `user_source` is the only pointer), and `config/dependencies.db` for the edges the error text never carries. The mirror is read offline and never refreshed here — keep it current with `adtai dependencies -refresh -schema <SCHEMA>`, and a project without one simply ranks on the compile errors alone.

Force-recompile all with native code + optimization, scoped by type/name:

```bash
adtai recompile -env DEV -force -native -level 3 -type PACKAGE% -name XX%
```

Bare `-force` recompiles every matching object. Combined with a compile modifier (`-native`/`-interpreted`/`-level`/`-scope`/`-warnings`) it instead recompiles **only** the VALID PL/SQL objects whose current settings drift from the requested state (any one axis mismatch selects the object; non-PL/SQL types are skipped). A plain recompile with neither `-native` nor `-interpreted` leaves each object's code type untouched (`REUSE SETTINGS`), so it never flips a native object to interpreted. `-scope`/`-warnings` take `+` as an extra separator (`-warnings PERF+SEVERE`) alongside space/comma/repeated forms.

```bash
adtai recompile -env DEV -force -level 2    # only objects not already at optimize level 2
```

`-type`, `-name`, and `-schema` are shared filters, and every mode below is scoped by them — no mode carries a name pattern of its own, so `-mviews DEP%` is a parser error and `-mviews -name DEP%` is the way. `-type`/`-name` take multiple patterns (`-type PACKAGE VIEW`, `-type PACKAGE,VIEW`, or a repeated flag). `-type` speaks Oracle's vocabulary: bare `PACKAGE` means specifications, `PACKAGE BODY` bodies, `MVIEW`/`MATERIALIZED` both mean `MATERIALIZED VIEW`. `-schema` is repeatable, space- or comma-separated, and pattern-aware (`-schema APP CORE`, `-schema APP,CORE%`); each schema is an independent pass.

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

## ut3 — run utPLSQL test suites

Runs the utPLSQL (UT3) test suites installed in the connected schema. **The exit code is the deliverable**: utPLSQL does *not* raise when a test fails — `ut.run` reports it and returns normally — so a caller that only watches for an exception sees a clean run.

```bash
adtai ut3 -env DEV
adtai ut3 -env DEV -name ICT_SEC%
adtai ut3 -env DEV -name ICT_SEC% ICT_COM%
adtai ut3 -env DEV -refresh
adtai ut3 -env DEV -silent
```

A package is run only when **both** are true: its name matches config `ut_pattern` (default `'_UT$'`), and utPLSQL has parsed it as a `%suite` with at least one `%test`. `ut_pattern` is the selection contract, so production code can never be swept in; `-name` takes repeatable Oracle `LIKE` patterns (`%`, `_`, `\` to escape) and narrows, never widens — it selects **the suites to run**, and names itself in the `SUMMARY FOR <PATTERNS>:` header. No `-name` means everything. `-schema A B` tests several schemas as separate console segments.

Non-zero covers three cases, not just a failed assertion:

- a test failed or errored;
- a suite ran but the reporter returned nothing or unparsable output;
- **nothing ran at all** — a zero-test run is a failure, not an empty pass, because that is exactly what a suite that stopped compiling looks like from outside.

A matched package that is `INVALID`, or that utPLSQL parsed no `%test` for, is **ignored**: no row in either table, no stanza, no effect on the exit code. It is not a suite, and `ut3` reports suites; the vanished-suite case is still caught by the zero-test rule.

Output order is the point. `UNIT TESTS SUITES:` rolls the **runnable** suites up *before* anything runs — two columns, the suite package and its test count. Every section header **always prints, empty list included**: a run that matched nothing reports it in the same shape as one that matched ten, and the exit code carries the failure. `TEST RESULTS:` prints as the run proceeds — the package name lands before that suite blocks, its dotted rows once the verdict is known — and each row carries the test's **procedure name**, not the `%test` description utPLSQL reports as the JUnit `testcase name`. Packages print A-Z; tests print in package-specification order (`ALL_PROCEDURES.SUBPROGRAM_ID`), never the reporter's. `ERRORS & FAILURES:` carries a wrapped stanza per non-passing test, headed `<STATUS> > <PACKAGE>.<TEST>` — status first — with a blank line above each (`FAIL` for a refused expectation, `ERROR` for a raised exception). The four status words are `PASS`, `FAIL`, `ERROR` and `SKIP`, and they are the same words the roll-up columns are headed with: one constant is both, so a row can never read one spelling under a header that reads another. `SUMMARY:` is the suites table again plus `PASS` / `FAIL` / `ERROR` / `TIMER` / `COVERAGE`, the verdicts blank wherever a count is zero and with no `TESTS` column — every test lands in exactly one verdict, and the roll-up above already carries the count. Under `-name` the header reads `SUMMARY FOR <PATTERNS>:`, upper-cased and comma-joined. `TIMER` is that suite's own wall clock to one decimal (`0.3`, `3.0`), fixtures and round trip included so the column accounts for the run's total, and it is one of the two columns a zero does not blank out of: `0.0` is a measurement, an empty cell would claim there was none. `-silent` drops the two listings — `UNIT TESTS SUITES:` and `TEST RESULTS:` — and keeps the banner, connection block, `SUMMARY:`, the timer, and `ERRORS & FAILURES:` whenever a run has any: it makes a green run quiet, not a red one unreadable.

`-refresh` rebuilds utPLSQL's annotation cache before discovery. A package compiled since the last run is not in that cache yet, so it is not discoverable and is silently ignored — `-refresh` is the first thing to try when a suite you know exists is missing from `UNIT TESTS SUITES:`. `ut3` runs suites; it never installs them.

**The naming convention is four config values, not flags.** All are **Oracle** regular expressions, matched case-insensitively, and Oracle evaluates them: `REGEXP_LIKE` selects the test packages inside the dictionary query — where the old `LIKE` sat, so a schema of thousands of packages is never fetched to find a handful of suites — and `REGEXP_SUBSTR` extracts the capture groups in the same pass. `ut_owner` is the only one that defaults empty. `ut_pattern` (`'_UT$'`) selects test packages. `ut_match` (`'^(.+)_UT$'`) pairs one back to the package it tests through capture group 1 — the pairing the `COVERAGE` column is built on — so a project whose suites are `TEST_ABC` sets `'^TEST_'` and `'^TEST_(.+)$'` and everything follows. `ut_owner` names the schema holding the suites when they do not live beside the code; it scopes discovery, the annotation cache, `-refresh` and the `ut.run` path, while coverage is still measured in the schema under test. `ut_module` names the module a suite belongs to (`'^[^_]+_([^_]+)'` reads `SEC` off `ICT_SEC_SECURITY_UT`): the run then prints a `MODULES:` table under `SUMMARY:` — `MODULE NAME` / `PACKAGES` / the same verdict, `TIMER` and `COVERAGE` columns — ending on a total row whose module name is blank. A group the expression could not name reads `?`, never blank, so it is not read as a second total. It ships set, so the table prints by default; `ut_module: ''` is how a project without a module convention turns it off. **Anchor it to nothing that has to follow the module token**: a trailing `_` cannot read `ICT_VPD`, a module whose whole implementation is one package, which is what put one package of Jan's 58 in the `?` row.

**Coverage is measured on every run** and lands as the `COVERAGE` column, after `TIMER`, in both tables. There is no `-coverage` flag: it was a mode until card `#291`, printing a `CODE COVERAGE:` / `NO CODE COVERAGE:` pair and a roll-up of its own instead of the run report, and Jan folded the figure into the run's own tables. The percentage is right-aligned and rendered to one decimal place with no `%` (`88.0`, `41.9`) so the figures stack under each other. **It is run-scoped**: a `SUMMARY:` row carries the figure for the package that suite tests, paired through `ut_match`, so a suite the expression cannot pair — or one whose target Oracle never instrumented — reads blank rather than `0.0`, which is reserved for code that *was* instrumented and never entered. Two suites testing one package therefore print the same figure, because block coverage records which blocks ran and never which test ran them. A `MODULES:` row and the total aggregate that group's target packages through one shared helper, so a group and the total under it can never be two calculations: the pooled block figure is scaled by the share of the group's body lines Oracle measured at all, which means a target nothing reached pulls its module down and a group nothing reached reads `0.0`. **A package no suite pairs to is not in the output at all** — that is the accepted cost of run-scoping, and it is what the removed `NO CODE COVERAGE:` work list used to show. **`-name` narrows the run and the figures follow it** (Jan, `#231`); a package reached only by an excluded suite reads lower than the truth. `PLSQL_OPTIMIZE_LEVEL` is **not** a prerequisite: level 2 is Oracle's default and block coverage is collected there anyway. Collection is utPLSQL's own — `DBMS_PROFILER` and `DBMS_PLSQL_CODE_COVERAGE` in parallel on 12.2+, never hand-rolled — and the percentage is the block figure with `COVERAGE`-pragma `NOT_FEASIBLE` blocks subtracted from the denominator, which makes it read slightly higher than utPLSQL's own HTML report, deliberately.

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

The closing `ACTIONS:` section lists only upgrades an online check actually found: `-update` appears when ADT.ai, `oracledb`, or SQLcl is behind, `-sqlcl` only when SQLcl is. With everything current — or under `-offline`, where nothing was checked — the section is absent entirely.

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
