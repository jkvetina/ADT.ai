# Recompile Objects (adtai recompile)

`recompile` recompiles invalid PL/SQL objects, views, synonyms, and materialized views in a target environment, in dependency-safe passes, and reports what stayed broken and why. Beyond plain invalid-object repair it can force a full recompile to a named compiler state (`-force` with `-native`/`-interpreted`, `-level`, `-scope`, `-warnings`) and run maintenance actions such as refreshing materialized views or rebuilding synonyms. Use it after deployments or schema changes that leave objects invalid.

Recompile invalid database objects in the current project's environment:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai recompile -env DEV
```

Force-recompile everything with native code and optimize level 3:

```bash
adtai recompile -env DEV -force -native -level 3
```

### Force recompile and drift narrowing

Bare `-force` recompiles **every** matching object, not just the invalid ones, the plain "recompile the world" sweep.

Combined with one or more compile modifiers (`-native`, `-interpreted`, `-level`, `-scope`, `-warnings`), `-force` instead recompiles only the objects whose **current** settings drift from the state you asked for. The tool reads each PL/SQL object's stored compiler settings (`PLSQL_CODE_TYPE`, `PLSQL_OPTIMIZE_LEVEL`, `PLSCOPE_SETTINGS`, `PLSQL_WARNINGS`) and selects an object when it differs on **any** requested axis; the recompile then applies the full requested state, not just the mismatched setting.

```bash
adtai recompile -env DEV -force -level 2          # only objects not already at optimize level 2
adtai recompile -env DEV -force -native           # only objects not already NATIVE
adtai recompile -env DEV -force -scope ALL -level 2 -interpreted -warnings PERF+SEVERE
```

The last example selects any object drifting on **any** of the four axes (code type, level, PL/Scope, warnings). Only VALID PL/SQL objects are considered, invalid objects are already recompiled by the ordinary pass, and non-PL/SQL types (`VIEW`, `SYNONYM`, `MATERIALIZED VIEW`, `TYPE`) carry no settings to drift from, so a modifier-gated `-force` skips them entirely. `-scope`/`-warnings` accept space, comma, `+`, or a repeated flag as separators, so `-warnings PERF+SEVERE`, `-warnings PERF,SEVERE`, `-warnings PERF SEVERE`, and `-warnings PERF -warnings SEVERE` are equivalent.

A plain recompile with neither `-native` nor `-interpreted` leaves each object's code type untouched (`REUSE SETTINGS` preserves it), it never rewrites a natively-compiled object to interpreted.

Scope the recompile by object type and name:

```bash
adtai recompile -env DEV -type PACKAGE% -name XX%
```

`-type` and `-name` accept multiple patterns, like `export_db`, an object matching any of them is in scope, and one matching several is still reported once. Space-separated, comma-separated, and repeated forms are equivalent:

```bash
adtai recompile -env DEV -name APP% CORE%
adtai recompile -env DEV -name APP%,CORE%
adtai recompile -env DEV -name APP% -name CORE%
```

### Schemas

Recompile several schemas in one run, each is an independent pass against its own connection, so a failure in one still leaves the others recompiled:

```bash
adtai recompile -env DEV -schema CORE APP
adtai recompile -env DEV -schema CORE -schema APP
```

Schema values can also be comma-separated or use old ADT `%` patterns, the same as `export_db`:

```bash
adtai recompile -env DEV -schema APP,CORE%
adtai recompile -env DEV -schema %
```

Overlapping patterns are deduplicated, so a schema matched twice is recompiled once. A **space-separated** list (`-schema APP CORE`) is a `-schema` form too, the same four forms `-name`/`-type` accept, matching `export_db` (widened in `#154`). With no `-schema` at all, every default schema configured for the environment is recompiled. Each schema is its own console segment: connect, run its independent pass, print its own `TIMER`, then move to the next schema, the banner prints only once. See `USAGE.md` §Console Output Contract for the full shape.

### Object types

`-type` names an Oracle object type, and Oracle's own vocabulary is the contract. A type without a wildcard is an **exact** match, so `-type PACKAGE` processes package *specifications* only, it never sweeps in bodies. Ask for both with an explicit wildcard:

```bash
adtai recompile -env DEV -type PACKAGE          # specifications only
adtai recompile -env DEV -type PACKAGE BODY     # bodies only
adtai recompile -env DEV -type PACKAGE%         # both
```

`SPEC` is the counterpart of `BODY` when you want to be explicit, the bare type name already *is* the specification:

```bash
adtai recompile -env DEV -type PACKAGE SPEC     # same as -type PACKAGE
adtai recompile -env DEV -type TYPE SPEC        # same as -type TYPE
```

**Quoting never changes what a type means.** A multi-word type may be written with a space, an underscore, or quotes, in any case, these are all one filter:

```bash
adtai recompile -env DEV -type PACKAGE BODY
adtai recompile -env DEV -type "PACKAGE BODY"
adtai recompile -env DEV -type PACKAGE_BODY
adtai recompile -env DEV -type package_body
```

Words are rejoined only when they name a real type, so two types side by side stay two filters: `-type PACKAGE TRIGGER` selects packages *and* triggers, because `PACKAGE TRIGGER` is not a type.

Materialized views also accept the short and full spellings, all resolving to Oracle's `MATERIALIZED VIEW`:

```bash
adtai recompile -env DEV -type MVIEW
adtai recompile -env DEV -type MATERIALIZED
adtai recompile -env DEV -type MATERIALIZED VIEW
adtai recompile -env DEV -type MATERIALIZED_VIEW
```

A materialized view **log** is a different object class and keeps its own type, `MVIEW LOG` (also `MVIEW_LOG` or `MATERIALIZED VIEW LOG`); `-type MVIEW` does not report logs, and `-type MVIEW LOG` does not report views. `-type M%` matches both, as any wildcard would.

The one ambiguous input is `-type MATERIALIZED VIEW`: it reads as the single type *materialized view*, not as `MATERIALIZED VIEW` plus `VIEW`. Write `-type MVIEW VIEW` to ask for materialized views and plain views together.

A token that does not name a known type is passed to the database as a LIKE pattern unchanged, so `%BODY` and `PACKAGE%` keep working. This resolution applies only to `-type`; `-name` is an identifier pattern, where `_` stays LIKE's single-character wildcard and a space would never be rejoined.

Also compile invalid and refresh stale materialized views:

```bash
adtai recompile -env DEV -mviews
```

Report only materialized views whose name matches a pattern (like `-name`), and force-refresh every match:

```bash
adtai recompile -env DEV -mviews -name DEP% -force
```

Report SYNONYMS tables grouped by target owner, each synonym mapped to its target object, one privilege per row, grantability, and validity, without recompiling anything:

```bash
adtai recompile -env DEV -synonyms
```

Report only synonyms whose name matches a pattern (like `-name`):

```bash
adtai recompile -env DEV -synonyms -name APP%
```

Report disabled constraints, indexes, and triggers without recompiling anything:

```bash
adtai recompile -env DEV -disabled
```

Report only disabled objects whose constraint/index/trigger name matches a pattern:

```bash
adtai recompile -env DEV -disabled -name APP%
```

Strip trailing whitespace from stored source in the database:

```bash
adtai recompile -env DEV -trailing
```

Strip it only from matching objects:

```bash
adtai recompile -env DEV -trailing -type PACKAGE% -name APP%
```

Report today's scheduler job runs without recompiling anything:

```bash
adtai recompile -env DEV -jobs
```

Report only scheduler jobs whose name matches a pattern:

```bash
adtai recompile -env DEV -jobs -name APP%
```

The command reads the object overview, recompiles invalid (or all, with `-force`) objects, retries failures in reverse order on a fresh connection, then re-checks what is still invalid. The shared Oracle connection bootstrap sets `DDL_LOCK_TIMEOUT = 10` immediately before `STARTUP.sql`, so object-lock waits are bounded by default while still letting project startup override the value. If there is nothing to compile, it keeps the initial overview and skips the final re-check. It prints an OBJECTS OVERVIEW table with columns `OBJECT TYPE`, `TOTAL`, `VALIDATED`, `INVALID`, `MISSING IDENTIFIERS`, and `MISSING STATEMENTS` and, when objects remain invalid, a single INVALID OBJECTS section sourced from `user_errors`, then exits non-zero.

`VALIDATED` sits immediately before `INVALID` so the two read as a pair, what the run fixed, and what it could not. It counts the objects of that type that were **invalid before the run and are not any more**, and it is a set difference over object identity, not a before/after count delta: recompiling a package spec invalidates its dependents, so a run that fixes one object and breaks another leaves the `INVALID` count unchanged, and a delta would report that repair as nothing happening. Zero renders blank, exactly as `INVALID` already does, so only real repairs draw the eye. Without `-force` the baseline is free, the invalid set is the recompile list itself; `-force` sweeps valid objects too, so it reads the baseline separately, after the force selection. The table lists each invalid object once with `OBJECT TYPE`, `OBJECT NAME`, the final compile-error code in `ERROR`, and the per-object `ERRORS` count. The messages follow it as a `COMPILE ERRORS:` list rather than a second table, one `  <OBJECT TYPE>.<OBJECT NAME>` stanza per object, then one `    - <line>.<pos> <message>` line per distinct error. Parity gaps vs old ADT: outside `-mviews`/`-synonyms`/`-disabled`/`-jobs` the OBJECTS OVERVIEW table always prints (old ADT showed it only under `__main__`) and there is no Slack-style team notification.

There is no lock report. `recompile` used to print a LOCKED OBJECTS table naming the session holding a lock on an object it could not compile, but that read `gv$locked_object`, and the application schema ADT.ai connects as holds no `SELECT` on the `V_$`/`GV_$` views behind those synonyms, that is a DBA grant, not a setting. Oracle offers no unprivileged substitute (`DBA_DDL_LOCKS`, `DBA_BLOCKERS` and plain `V$LOCKED_OBJECT` are all in the same class), so the report was removed rather than kept as a table most connections could never see. Nothing is lost operationally: the shared connection bootstrap sets `DDL_LOCK_TIMEOUT = 10`, so a lock wait is bounded, and an object that stays locked surfaces as its own compile error in INVALID OBJECTS. `-mviews` is a materialized-view-focused run: it **skips the usual invalid-object recompile and the OBJECTS OVERVIEW report** (and the INVALID OBJECTS table) entirely, keeping only the materialized-view sections. With `-mviews`, the command always prints a MATERIALIZED VIEWS table, object name, a combined `STATUS` cell (`<staleness> / <compile state>`, e.g. `FRESH / VALID`), a resolved refresh `TYPE` of `F` (FAST) or `C` (COMPLETE), a `LOG` column, last refreshed at, and a `TIMER`. `TYPE` is derived from the MV's **configured** refresh method (the stable method the view was created with, never the volatile last-refresh type, and the tool never changes it): COMPLETE → `C`, FAST → `F`, and a `FORCE` method resolves to `F` when a usable materialized-view log backs it or `C` when none does, so the column is always a clean F/C. `LOG` shows `Y` when a usable MV log exists on the view's detail (master) tables (`user_mview_detail_relations` joined to `user_mview_logs`) and blank otherwise; this is what resolves a `FORCE` method to F vs C. `TIMER` is Oracle's **own recorded** refresh duration, `ROUND(86400 * (last_refresh_end_time - last_refresh_date))` from the data dictionary, not a tool-measured wall clock, re-read after any action so it reflects the refresh just performed, and rendered as a rounded-up bare `Ns`: any real measurement rounds **up** to whole seconds, so a genuinely sub-second refresh reads as `1s` (never `0`, never a `<`/`>` comparator) and an N-second refresh as `Ns`. (The dictionary times the difference of two `DATE` columns at one-second granularity, so a sub-second refresh records an honest `0`; rounding that 0 up to `1s` shows a real, if brief, measurement instead of a misleading bare `0` or a blank cell.) The cell is blank **only** when the timer is `NULL`, a materialized view that has never been refreshed. The command then acts on the views: invalid/needs-compile MVs get `ALTER MATERIALIZED VIEW … COMPILE` and stale/unusable MVs get `DBMS_MVIEW.REFRESH` using the view's **own** configured method (so a COMPLETE view is never flipped to FAST; a `FORCE` view passes `?` and lets Oracle decide FAST-vs-COMPLETE at runtime). The MATERIALIZED VIEWS table is rendered as a live stream: each view's name prints first, the `COMPILE`/`REFRESH` for that view runs at that point, and only then does the rest of its row (status, type, log, last refreshed at, timer) print, so the visible pause while a view refreshes attaches to the view being worked on, right where its name already sits, instead of stalling on the connection block above the table. When the schema owns no materialized views the table still prints (header only, no rows) so you can see the report ran. Any failed action is listed below the MATERIALIZED VIEWS table as `  <NAME>) <error>` and makes the run exit non-zero. `-mviews` is scoped by the shared `-name` filter: bare `-mviews` reports all materialized views, while `-mviews -name DEP%` reports only those whose name matches `DEP%` (supports `%` wildcards). It carries no name pattern of its own, and `-type` has nothing to select here, the flag already opts in exactly one object class. With `-force`, every matching materialized view is `REFRESH`-ed regardless of staleness, not just the stale/unusable ones.

`-synonyms` is a **read-only** run: like `-mviews` it skips the invalid-object recompile, the OBJECTS OVERVIEW, and the INVALID OBJECTS table, but unlike `-mviews` it takes no action at all (no compile, no refresh, no reconnect) and the run always succeeds. It fetches the full synonym report once, ordered by target owner/status, then prints one table per target owner with a header shaped `SYNONYMS TO SCHEMA <OWNER>:` (for example `SYNONYMS TO SCHEMA CORE:`). Row columns stay compact and place the target name before its type: `SYNONYM NAME`, `OBJECT NAME`, `TYPE`, `PRIV`, `GRNT`, and `VALID`. Privileges come from `user_tab_privs_recd`; any comma-separated privilege cell is expanded so each printed row carries at most one privilege, while the existing `ALL` collapse remains one row. `GRNT` shows `Y` when any received privilege carries `WITH GRANT OPTION`. `VALID` shows `Y` only when the target object's status from `all_objects` is `VALID`; invalid, unknown, or missing targets leave it blank. Target type/status resolution comes from `all_objects` even when there is no matching privilege row, so synonyms to views still show `TYPE` as `VIEW`. `-synonyms` is scoped by the shared `-name` filter, applied to the synonym name: bare `-synonyms` reports all synonyms, while `-synonyms -name APP%` reports only those matching `APP%` (supports `%` wildcards). It carries no name pattern of its own, and `-type` has nothing to select here, the flag already opts in exactly one object class. When the schema owns no synonyms the report still prints a header-only `SYNONYMS` table so the run is visibly present. This is a faithful port of CORE23's `core_daily_synonyms_v` dashboard view, rewritten against portable `user_*`/`all_*` dictionary views.


`-disabled` is a **read-only** run: like `-synonyms` it skips the invalid-object recompile, the OBJECTS OVERVIEW, the INVALID OBJECTS table, and the materialized-view pass. It ports CORE23's `core_daily_disabled_objects_v` view into portable dictionary SQL against `all_constraints`, `all_indexes`, and `all_triggers`, scoped to the connected schema and the standard `objects_add` / `objects_ignore` patterns. The report prints three dedicated tables: `DISABLED CONSTRAINTS:`, `DISABLED INDEXES:`, and `DISABLED TRIGGERS:`. Each table hides the connected-schema `OWNER` and repeated `TYPE` context, so row columns are only `OBJECT NAME` and `TABLE NAME`. Rows include disabled constraints, indexes whose `STATUS` is not `VALID` or whose `FUNCIDX_STATUS` is not `ENABLED`, and disabled triggers. `-disabled` is scoped by the shared filters, and is the one report-only flag spanning several object types: `-name` filters the constraint/index/trigger name, and `-type` picks which of `CONSTRAINT`/`INDEX`/`TRIGGER` to report, so `-disabled -type TRIGGER` reports only disabled triggers. Bare `-disabled` reports all three. Empty results still print header-only tables for all three types so the report is visibly present.

`-jobs` is a **read-only** run: like `-disabled` it skips the invalid-object recompile, the OBJECTS OVERVIEW, the INVALID OBJECTS table, materialized-view pass, synonym pass, and disabled-object pass. It ports CORE23's `core_daily_schedulers_v` view into portable dictionary SQL against `all_scheduler_job_run_details`, scoped to the connected schema, the current day (`TRUNC(SYSDATE)` through tomorrow), and the standard `objects_add` / `objects_ignore` patterns. The report prints one compact table per scheduler status, with headers such as `SCHEDULER JOBS - FAILED:` and `SCHEDULER JOBS - SUCCEEDED:`. Row columns are `JOB NAME`, `LAST START DATE`, `DURAT`, and `CPU`; duration cells are rendered without fractional seconds. `OWNER`, `STATUS`, `COUNT`, and `ERROR` are hidden because owner is the connected schema, status is the section header, and the count/error details are intentionally omitted from the compact health table. `-jobs` is scoped by the shared `-name` filter, applied to the job name: bare `-jobs` reports all scheduler jobs, while `-jobs -name APP%` reports only those matching `APP%`. It carries no name pattern of its own, and `-type` has nothing to select here, the flag already opts in exactly one object class. Empty results still print header-only `FAILED` and `SUCCEEDED` tables so the report is visibly present.

`-trailing` fixes the export noise `export_db` creates. `export_db` strips trailing whitespace from every line it writes, so an untouched 10k-line package still differs from the database's stored source on every single export. `-trailing` repairs the *source* side once per schema: it finds objects whose stored source carries trailing whitespace and rewrites them without it, so the database matches what `export_db` writes and the noise is gone for good. Like `-synonyms`/`-disabled`/`-jobs` it skips the invalid-object recompile, the OBJECTS OVERVIEW, the INVALID OBJECTS table, and the materialized-view pass.

**`-trailing` strips.** There is no preview mode and no second flag to confirm with: asking for `-trailing` is asking for the fix. It **lists each rewritten object as it goes**, the same way `export_db` lists the objects it acts on: an `UPDATED <n> OBJECTS:` header, then one `TYPE | NAME` row per object with the type cell printed only when it changes, so a run of one type reads as a group. Each row prints before that object's rewrite runs, so a visible pause sits on the object being worked on. A clean schema prints `UPDATED 0 OBJECTS:`, which is the proof the pass ran. `-silent` suppresses the per-object rows while keeping the header, it drops per-row detail only, never required chrome.

The safety here is structural rather than a confirmation prompt: an object with nothing to strip is never touched, and stripping trailing whitespace cannot change what the code does. Scope the run with `-type`/`-name` if you want a smaller blast radius.

Like every other action, **`-trailing` takes no name pattern of its own**, it is scoped by the standard `-type` and `-name` filters. So `adtai recompile -env DEV -trailing -type PACKAGE% -name APP%` strips only matching packages, and a stray `-trailing APP%` is a parser error rather than a silently ignored filter.

Scope is `PACKAGE`, `PACKAGE BODY`, `PROCEDURE`, `FUNCTION`, `TRIGGER`, and `VIEW`. Types and type bodies are deliberately out of scope.

The first five come from `user_source`, whose stored source round-trips faithfully through `CREATE OR REPLACE`. **Views take a separate path** because they have no `user_source` rows at all: their only faithful source is `user_views.text`, a LONG holding just the `SELECT` without the `CREATE OR REPLACE VIEW ... AS` wrapper. So a view is rebuilt as `CREATE OR REPLACE FORCE VIEW <name> (<columns>) AS <text>`, with the column list re-read from `user_tab_columns` in `column_id` order. The list is not optional: a view created as `CREATE VIEW v (a, b) AS SELECT x+1, y FROM t` stores no aliases, so without it the rebuild would fail outright or silently rename the columns. `FORCE` means a view that is already invalid for an unrelated reason comes back exactly as invalid instead of failing the sweep. (`export_db`'s own view export goes through `DBMS_METADATA.GET_DDL`, which *regenerates* DDL rather than returning stored source, so it cannot be reused for a whitespace-only rewrite.)

Because a LONG cannot be `RTRIM`-ed or compared in SQL, view detection is the one part that does not run in the database: each in-scope view's text is fetched and tested in Python, against the same `rstrip()` rule `export_db` applies. Two classes of view are skipped rather than rebuilt, since neither property survives a rebuild from `user_views.text`:

- **Views carrying a constraint.** `WITH READ ONLY` records a constraint of type `O` and `WITH CHECK OPTION` type `V`; a rebuild would silently drop the clause. Any view with a `user_constraints` row is left alone.
- **Editioning views.** They belong to Edition-Based Redefinition and are not maintained through a plain `CREATE OR REPLACE VIEW`.

A view whose column list is not a plain unquoted identifier is reported as a failed object rather than guessed at, quoting it wrong would rename a column.

What `-trailing` guarantees, and why:

- **Nothing else changes.** Each line is stripped exactly the way `export_db` strips it; blank lines stay blank, indentation is untouched, and the object is rebuilt from its own stored source. Trailing spaces, tabs, and stray CRs go; the line terminator stays.
- **An object with nothing to strip is never touched.** No `CREATE OR REPLACE`, so no `LAST_DDL_TIME` churn and no needless dependent invalidation. A re-run on a cleaned schema is a no-op.
- **One object at a time.** The source is fetched, rewritten, and finished per object before the next one is read, never every object read up front and written back afterwards, which on a live database would clobber a colleague's change made in that window.
- **Disabled triggers stay disabled.** `CREATE OR REPLACE TRIGGER` silently re-enables a trigger, so the status is captured first and restored after. (The trigger is briefly enabled between the two statements.)
- **Wrapped objects are skipped.** `user_source` returns their obfuscated blob rather than recoverable source, so rewriting one would destroy it.
- **A view's grants survive.** `CREATE OR REPLACE VIEW` preserves them, which is why the view is replaced rather than dropped and recreated.

`CREATE OR REPLACE` invalidates dependents, so follow a `-trailing` sweep with a plain `adtai recompile` pass. Any failed rewrite is listed below the table as `  <NAME>) <error>` and makes the run exit non-zero, matching the `-mviews` failed-action shape.

Whenever a normal recompile run leaves invalid objects, ADT.ai prints an INVALID OBJECTS table and a COMPILE ERRORS list below it, so an AI agent or developer can see the invalid object and jump to the offending line without passing a separate flag. The table has one row per invalid object; the list carries the messages, one stanza per object. It uses the same scope and warning filter as the invalid-object summary did (PL/SQL warnings are skipped). Each report header has exactly two blank lines above it.

The messages are a list because a compiler message is prose and a table column is not. Until `#212` they were a fourth column in an `ID | LINE | POS | ERROR MESSAGE` table, where the widest column went to an `ID` that existed only to point back at the listing above and the message got whatever was left, live, that truncated `insufficient privilege to access object SSO_AUTH.SSO` to `…object SSO...`, which names nothing to grant. Keyed on the object instead, the cross-reference disappears, the `ID` column leaves the whole report with it, and the width goes to the text:

```text
COMPILE ERRORS
--------------

  PROCEDURE.CREATE_APEX_SESSION
    - 19.14 PLS-00904: insufficient privilege to access object SSO_AUTH.SSO
    - 20.14 PLS-00904: insufficient privilege to access object SSO_AUTH.AUTHMAN

  PACKAGE BODY.UT_UTILS
    - 0.0 PL/SQL: Compilation unit analysis terminated
    - 1.14 PLS-00304: cannot compile body of 'UT_UTILS' without its
           specification
    - 1.14 PLS-00905: object DBADMIN.UT_UTILS is invalid
```

Four rules shape the list, all of them measured against a live 17-invalid schema:

- **The heading is `<OBJECT TYPE>.<OBJECT NAME>`, never the bare name.** That schema carries `UT_UTILS` as both a `PACKAGE` and a `PACKAGE BODY`, two objects with different errors, which a name-keyed stanza would merge.
- **Cascade rows never print.** Oracle files a `PL/SQL: Statement ignored`, `PL/SQL: SQL Statement ignored` or `PL/SQL: Compilation unit analysis terminated` beside every statement the real error killed, so one missing grant can contribute ten. `CREATE_APEX_SESSION2` filed 20 error rows, 7 of them cascades, and reads as 3 lines. All three are restatements of a failure reported next to them, and the same predicate that keeps them out of the root-cause ranking keeps them off the screen.
- **A repeated message prints once, at its lowest `line.pos`.** A missing grant referenced eight times is one thing to fix, not eight. Deduping compares the **full** message, the old table compared what it rendered, where `…object SSO...` and `…object SER...` had already been truncated to the same prefix and would have collapsed into one.
- **Dedupe is per object.** Two objects failing on the same grant both say so; only a repeat *within* one object is a duplicate.

A message too long for one line wraps under a hanging indent aligned to the message start, and is never cut, outside a table column there is no cell to fit, and `PLS-00103: Encountered the symbol "-" when expecting o...` names no symbol the reader can act on. Each message is still stripped on both sides with interior line breaks collapsed first: `user_errors.text` keeps whatever whitespace the compiler wrote, which is what makes the dedupe key stable and lets the wrapper own every line break.

### Root causes, where to start

A flat list stops helping once it is long. Drop a sequence and the package that used it goes invalid, then the trigger and the view that used *that*: twenty rows where three are the actual damage. So a run that leaves invalid objects prints a **ROOT CAUSES** section, separating the objects that are broken from the ones that are merely downstream. There is no flag, the same reasoning that makes INVALID OBJECTS unconditional applies here.

It reads **after** INVALID OBJECTS and its compile-error list, not before. The verdict belongs under the evidence it is drawn from. A normal run's section order is therefore `OBJECTS OVERVIEW:` → `INVALID OBJECTS:` → `COMPILE ERRORS:` → `ROOT CAUSES:`, which is the last section of the report.

This is a real run against a schema carrying 17 invalid objects, trimmed to the interesting rows:

```text
ROOT CAUSES
-----------

  OBJECT TYPE    OBJECT NAME                    CAUSE     BLAST
  ------------   ----------------------------   -------   -----
  LIBRARY        XMLTYPE_LIB                    SOURCE        1
  PACKAGE        UT_COVERAGE_HELPER_BLOCK       MISSING       1
  PACKAGE        UT_UTILS                       MISSING       1
  PROCEDURE      CREATE_APEX_SESSION2           GRANT
  PACKAGE BODY   A_GEN_SOAP                     MISSING
  PROCEDURE      CHECK_ITEMS_TO_SUBMIT          MISSING
  VIEW           V_DATAPUMP_EXPORT_GROUPS       MISSING

  MISSING - not there; restore it, then recompile:
    PACKAGE.UT_COVERAGE_HELPER_BLOCK -> UT_COVERAGE_HELPER.T_UNIT_LINE_CALLS
    PACKAGE.UT_UTILS -> UT_OBJECT_NAMES
    PROCEDURE.CHECK_ITEMS_TO_SUBMIT -> apex_200100.wwv_flow_page_da_events
    VIEW.V_DATAPUMP_EXPORT_GROUPS -> WM_CONCAT
    PACKAGE BODY.A_GEN_SOAP -> Z_SOAP_OUTPUT

  GRANT - it exists and this schema has no privilege on it:
    PROCEDURE.CREATE_APEX_SESSION2 -> SSO_AUTH.SSO

  SOURCE - its own source does not parse; nothing upstream to fix.
```

The knock-ons are **not** listed. They are still classified, that is what keeps them out of the ranked table and what `BLAST` counts, but they get no section of their own. `#205` gave them a `DERIVED - DO NOT START HERE` list on the reasoning that an object vanishing from a report reads as fixed; INVALID OBJECTS above already names every one of them, so the section restated a list the reader had just finished. On the run above, `TYPE.SFM_STR_AGG_TYPE` and the two package bodies are absent here and present there.

Every verdict heading carries **exactly one** empty line above it, the first included, so three stanzas read as three answers rather than one block. The ranked table already closes with a blank line, so the first heading takes that one and only the headings after it emit their own, `#209` emitted one unconditionally, which gave the first heading two.

Each entry names its own object as `<OBJECT TYPE>.<OBJECT NAME>` and points at what it needs. There is no `ID` column anywhere in the report: it existed only to join these sections to the INVALID OBJECTS listing, and the compile-error list is keyed on the object itself, so the cross-reference is gone. `BLAST` counts the invalid objects that clear transitively once this one compiles, blank for none, and orders the table: most downstream damage first, ties broken by error count.

What to fix is listed **below** the table, grouped by verdict, rather than in a column: a qualified culprit such as `UT_COVERAGE_HELPER.T_UNIT_LINE_CALLS` is 36 characters, and with a 28-character object name in the table there are nine left at 80 columns, a truncated `Z_SOAP...` answers nothing. Each heading doubles as the verdict's definition:

- **MISSING**, an object or identifier it needs is not there. The listed name is what to restore.
- **GRANT**, the object exists and this schema has no privilege on it (`PLS-00904`, `ORA-01031`). No amount of recompiling fixes this one; it needs a `GRANT`, and reading it as MISSING sends you hunting for something that is already there.
- **SOURCE**, the object's own text does not parse (`PLS-00103`). It names nothing because there is nothing upstream: the heading is the whole answer, so no list follows it.
- **UNKNOWN**, invalid with no compile error to explain it (a view invalidated by a dropped dependency can carry no `user_errors` row at all). Also listless.

The ranking reads three sources, because no one of them is enough:

- **The compile errors.** Oracle usually names the culprit outright, and whether that culprit is *itself* invalid is what separates a knock-on from a root. `PLS-00905: object DBADMIN.UT_UTILS is invalid` on a package body, where the spec is also in the list, is derived, fix the spec. `PLS-00201: identifier 'UT_OBJECT_NAMES' must be declared`, where nothing by that name is invalid, means it is not there at all, which *is* the root. An owner prefix is stripped only when it is the connected schema's own: `SYS.XMLTYPE_LIB` is a different object from a local `XMLTYPE_LIB`, and collapsing the two would report a separate root as a knock-on. Cascade rows (`PL/SQL: Statement ignored`, `Compilation unit analysis terminated`) are excluded from the error count, Oracle emits one per statement it gave up on, so counting them ranks the noisiest object above the most upstream one.
- **The stored source**, for the errors that name nothing. `ORA-00942: table or view does not exist` reports no object, and Oracle records no `USER_DEPENDENCIES` row for a reference that never resolved, so the graph cannot name it either. The error's own line and position can: that is where the compiler was looking, and the identifier is read back from `user_source` for exactly those lines, never the whole object.
- **The dependency mirror**, `config/internal/dependencies.db`. Read offline and never refreshed by `recompile`, it supplies the edges no error text carries, the invalid package that breaks two other invalid objects without any of their messages naming it. This is what makes `BLAST` meaningful. A project with no mirror, or one that predates the schema, ranks on the compile errors alone; it is never an error. Keep it current with `adtai dependencies -refresh -schema <SCHEMA>`.

Derived objects are listed rather than hidden: an object that vanishes from a report reads as fixed. Each line names what it waits on, so the chain is visible without re-reading every message.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default environment | Connection environment to recompile in. |
| `-type`, `--type` | Yes | `%` | Object type pattern(s) to recompile, repeatable, comma- or space-separated, supports `%` wildcards. Oracle type names, so a bare `PACKAGE` means specifications only; `PACKAGE BODY`, `PACKAGE_BODY`, `PACKAGE SPEC`, and `MVIEW`/`MATERIALIZED` are accepted spellings, quoted or not. See [Object types](#object-types). |
| `-name`, `--name` | Yes | `%` | Object name pattern(s) to recompile, repeatable, comma- or space-separated, supports `%` wildcards. |
| `-schema`, `--schema` | Yes | environment default DB schemas | Schema(s) to recompile, one pass each. Pass multiple times, space-separate (`-schema DA GSN`), use comma lists, or use `%` patterns such as `CORE%`. |
| `-force`, `--force` | No | off | Recompile all matching objects, not just invalid ones. Combined with a compile modifier (`-native`/`-interpreted`/`-level`/`-scope`/`-warnings`) it narrows to only the objects whose settings drift from the requested state, see [Force recompile and drift narrowing](#force-recompile-and-drift-narrowing). |
| `-level`, `--level` | No | none | PL/SQL optimize level (1-3). |
| `-native`, `--native` | No | off | Compile PL/SQL to native code. |
| `-interpreted`, `--interpreted` | No | off | Compile PL/SQL to interpreted code (`-native` takes precedence). A plain recompile with neither flag leaves the code type untouched (`REUSE SETTINGS`). |
| `-scope`, `--scope` | Yes | none | PL/Scope settings (`IDENTIFIERS`, `STATEMENTS`, `ALL`); space-, comma-, `+`-, or repeated-flag-separated. |
| `-warnings`, `--warnings` | Yes | none | PL/SQL warnings (`SEVERE`, `PERF`, `INFO`); space-, comma-, `+`-, or repeated-flag-separated. |
| `-mviews`, `--mviews` | No | off | Report materialized views (scoped by `-name`), then `COMPILE` invalid ones and `REFRESH` stale ones. With `-force`, `REFRESH` every matching view. |
| `-synonyms`, `--synonyms` | No | off | Report-only: print `SYNONYMS TO SCHEMA <OWNER>:` tables mapping each synonym (scoped by `-name`) to its target object, one privilege per row, `GRNT`, and `VALID`. Skips the object recompile and overview entirely; takes no action. |
| `-disabled`, `--disabled` | No | off | Report-only: print disabled constraints, invalid/function-disabled indexes, and disabled triggers in dedicated type tables (scoped by `-name`, and by `-type` to one of `CONSTRAINT`/`INDEX`/`TRIGGER`). Skips the object recompile and overview entirely. |
| `-jobs`, `--jobs` | No | off | Report-only: print today's scheduler job runs from `all_scheduler_job_run_details` in status-grouped compact tables (scoped by `-name`). Skips the object recompile and overview entirely. |
| `-trailing`, `--trailing` | No | off | Strip trailing whitespace from stored source in the database via `CREATE OR REPLACE`, scoped by `-type`/`-name`. Skips the object recompile and overview entirely. |
| `-silent`, `--silent` | No | off | Suppress object overview details while keeping the standard banner, connection block, and final timer. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values; keep Python tracebacks. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
