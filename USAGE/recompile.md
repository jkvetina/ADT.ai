# Recompile Objects (adtai recompile)

Recompile invalid database objects in the current project's environment:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai recompile -target DEV
```

Force-recompile everything with native code and optimize level 3:

```bash
adtai recompile -target DEV -force -native -level 3
```

Scope the recompile by object type and name:

```bash
adtai recompile -target DEV -type PACKAGE% -name XX%
```

Also compile invalid and refresh stale materialized views:

```bash
adtai recompile -target DEV -mviews
```

Report only materialized views whose name matches a pattern (like `-name`), and force-refresh every match:

```bash
adtai recompile -target DEV -mviews DEP% -force
```

Report SYNONYMS tables grouped by target owner — each synonym mapped to its target object, one privilege per row, grantability, and validity — without recompiling anything:

```bash
adtai recompile -target DEV -synonyms
```

Report only synonyms whose name matches a pattern (like `-name`):

```bash
adtai recompile -target DEV -synonyms APP%
```

Report disabled constraints, indexes, and triggers without recompiling anything:

```bash
adtai recompile -target DEV -disabled
```

Report only disabled objects whose constraint/index/trigger name matches a pattern:

```bash
adtai recompile -target DEV -disabled APP%
```

Report today's scheduler job runs without recompiling anything:

```bash
adtai recompile -target DEV -jobs
```

Report only scheduler jobs whose name matches a pattern:

```bash
adtai recompile -target DEV -jobs APP%
```

Print the full compile error messages of whatever is still invalid:

```bash
adtai recompile -target DEV -errors
```

The command reads the object overview, recompiles invalid (or all, with `-force`) objects, retries failures in reverse order on a fresh connection, then re-checks what is still invalid. If there is nothing to compile, it keeps the initial overview and skips the final re-check. It prints an OBJECTS OVERVIEW table with invalid-object and missing-PL/Scope counts and, when objects remain invalid, an INVALID OBJECTS table, then exits non-zero. Parity gaps vs old ADT: outside `-mviews`/`-synonyms`/`-disabled`/`-jobs` the OBJECTS OVERVIEW table always prints (old ADT showed it only under `__main__`) and there is no Slack-style team notification.

It also collects current session/object locks from `gv$locked_object` (scoped to the connected schema) and, when any exist, prints a LOCKED OBJECTS table — handy for spotting why an object will not compile. The lock report degrades to empty when the connection lacks `SELECT` on the `gv$` views, so it never breaks a recompile. `-mviews` is a materialized-view-focused run: it **skips the usual invalid-object recompile and the OBJECTS OVERVIEW report** (and the INVALID OBJECTS / COMPILE ERRORS tables) entirely, keeping only the LOCKED OBJECTS report — locks can block an MV refresh — plus the materialized-view sections. With `-mviews`, the command always prints a MATERIALIZED VIEWS table — object name, a combined `STATUS` cell (`<staleness> / <compile state>`, e.g. `FRESH / VALID`), a resolved refresh `TYPE` of `F` (FAST) or `C` (COMPLETE), a `LOG` column, last refreshed at, and a `TIMER`. `TYPE` is derived from the MV's **configured** refresh method (the stable method the view was created with, never the volatile last-refresh type, and the tool never changes it): COMPLETE → `C`, FAST → `F`, and a `FORCE` method resolves to `F` when a usable materialized-view log backs it or `C` when none does — so the column is always a clean F/C. `LOG` shows `Y` when a usable MV log exists on the view's detail (master) tables (`user_mview_detail_relations` joined to `user_mview_logs`) and blank otherwise; this is what resolves a `FORCE` method to F vs C. `TIMER` is Oracle's **own recorded** refresh duration — `ROUND(86400 * (last_refresh_end_time - last_refresh_date))` from the data dictionary, not a tool-measured wall clock — re-read after any action so it reflects the refresh just performed, and rendered as a rounded-up bare `Ns`: any real measurement rounds **up** to whole seconds, so a genuinely sub-second refresh reads as `1s` (never `0`, never a `<`/`>` comparator) and an N-second refresh as `Ns`. (The dictionary times the difference of two `DATE` columns at one-second granularity, so a sub-second refresh records an honest `0`; rounding that 0 up to `1s` shows a real — if brief — measurement instead of a misleading bare `0` or a blank cell.) The cell is blank **only** when the timer is `NULL` — a materialized view that has never been refreshed. The command then acts on the views: invalid/needs-compile MVs get `ALTER MATERIALIZED VIEW … COMPILE` and stale/unusable MVs get `DBMS_MVIEW.REFRESH` using the view's **own** configured method (so a COMPLETE view is never flipped to FAST; a `FORCE` view passes `?` and lets Oracle decide FAST-vs-COMPLETE at runtime). The MATERIALIZED VIEWS table is rendered as a live stream: each view's name prints first, the `COMPILE`/`REFRESH` for that view runs at that point, and only then does the rest of its row (status, type, log, last refreshed at, timer) print — so the visible pause while a view refreshes attaches to the view being worked on, right where its name already sits, instead of stalling on the connection block above the table. When the schema owns no materialized views the table still prints (header only, no rows) so you can see the report ran. Any failed action is listed below the MATERIALIZED VIEWS table as `  <NAME>) <error>` (styled like the COMPILE ERRORS messages) and makes the run exit non-zero. `-mviews` takes an optional name pattern, scoped independently of the `-name` object filter: bare `-mviews` reports all materialized views (`%`), while `-mviews DEP%` reports only those whose name matches `DEP%` (supports `%` wildcards). With `-force`, every matching materialized view is `REFRESH`-ed regardless of staleness, not just the stale/unusable ones.

`-synonyms` is a **read-only** run: like `-mviews` it skips the invalid-object recompile, the OBJECTS OVERVIEW, and the INVALID OBJECTS / COMPILE ERRORS tables, but unlike `-mviews` it takes no action at all (no compile, no refresh, no reconnect, no lock report) and the run always succeeds. It fetches the full synonym report once, ordered by target owner/status, then prints one table per target owner with a header shaped `SYNONYMS TO SCHEMA: <OWNER>` (for example `SYNONYMS TO SCHEMA: CORE`). Row columns stay compact and place the target name before its type: `SYNONYM NAME`, `OBJECT NAME`, `TYPE`, `PRIV`, `GRNT`, and `VALID`. Privileges come from `user_tab_privs_recd`; any comma-separated privilege cell is expanded so each printed row carries at most one privilege, while the existing `ALL` collapse remains one row. `GRNT` shows `Y` when any received privilege carries `WITH GRANT OPTION`. `VALID` shows `Y` only when the target object's status from `all_objects` is `VALID`; invalid, unknown, or missing targets leave it blank. Target type/status resolution comes from `all_objects` even when there is no matching privilege row, so synonyms to views still show `TYPE` as `VIEW`. `-synonyms` takes an optional name pattern scoped to the synonym name and independent of the `-name` object filter: bare `-synonyms` reports all synonyms (`%`), while `-synonyms APP%` reports only those whose name matches `APP%` (supports `%` wildcards). When the schema owns no synonyms the report still prints a header-only `SYNONYMS` table so the run is visibly present. This is a faithful port of CORE23's `core_daily_synonyms_v` dashboard view, rewritten against portable `user_*`/`all_*` dictionary views.


`-disabled` is a **read-only** run: like `-synonyms` it skips the invalid-object recompile, the OBJECTS OVERVIEW, the INVALID OBJECTS / COMPILE ERRORS tables, the lock report, and the materialized-view pass. It ports CORE23's `core_daily_disabled_objects_v` view into portable dictionary SQL against `all_constraints`, `all_indexes`, and `all_triggers`, scoped to the connected schema and the standard `objects_add` / `objects_ignore` patterns. The report prints three dedicated tables: `DISABLED CONSTRAINTS:`, `DISABLED INDEXES:`, and `DISABLED TRIGGERS:`. Each table hides the connected-schema `OWNER` and repeated `TYPE` context, so row columns are only `OBJECT NAME` and `TABLE NAME`. Rows include disabled constraints, indexes whose `STATUS` is not `VALID` or whose `FUNCIDX_STATUS` is not `ENABLED`, and disabled triggers. Bare `-disabled` reports all matching disabled objects (`%`), while `-disabled APP%` filters by the constraint/index/trigger name independently of `-name`. Empty results still print header-only tables for all three types so the report is visibly present.

`-jobs` is a **read-only** run: like `-disabled` it skips the invalid-object recompile, the OBJECTS OVERVIEW, the INVALID OBJECTS / COMPILE ERRORS tables, the lock report, materialized-view pass, synonym pass, and disabled-object pass. It ports CORE23's `core_daily_schedulers_v` view into portable dictionary SQL against `all_scheduler_job_run_details`, scoped to the connected schema, the current day (`TRUNC(SYSDATE)` through tomorrow), and the standard `objects_add` / `objects_ignore` patterns. The report prints one compact table per scheduler status, with headers such as `SCHEDULER JOBS - FAILED:` and `SCHEDULER JOBS - SUCCEEDED:`. Row columns are `JOB NAME`, `LAST START DATE`, `DURAT`, and `CPU`; duration cells are rendered without fractional seconds. `OWNER`, `STATUS`, `COUNT`, and `ERROR` are hidden because owner is the connected schema, status is the section header, and the count/error details are intentionally omitted from the compact health table. Bare `-jobs` reports all matching scheduler jobs (`%`), while `-jobs APP%` filters by job name independently of `-name`. Empty results still print header-only `FAILED` and `SUCCEEDED` tables so the report is visibly present.

The INVALID OBJECTS table only shows a count and the first error code per object. With `-errors`, the command always prints a COMPILE ERRORS table with one row per error line sourced from `user_errors`, so an AI agent or developer can jump straight to the offending location. Each row carries a 1-based `ID` (first column) and the object type, name, line, and position, sorted by object type, name, line, then position; the message text would be too wide for the table, so it is listed below the table as `  <ID>) <text>` lines keyed back to the `ID` column. When nothing is still invalid the table prints empty (header only) so the report is visibly present rather than silently skipped. It uses the same scope and warning filter as the invalid-object summary (PL/SQL warnings are skipped), so the per-object error rows match that summary's counts. Each report header has exactly two blank lines above it.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default environment | Connection environment to recompile in. |
| `-target`, `--target` | No | none | Connection environment (alias of `-env`, for old ADT muscle memory). |
| `-schema`, `--schema` | No | environment default DB schema | Schema to recompile. |
| `-type`, `--type` | No | `%` | Object type pattern to recompile, supports `%` wildcards. |
| `-name`, `--name` | No | `%` | Object name pattern to recompile, supports `%` wildcards. |
| `-force`, `--force` | No | off | Recompile all matching objects, not just invalid ones. |
| `-level`, `--level` | No | none | PL/SQL optimize level (1-3). |
| `-native`, `--native` | No | off | Compile PL/SQL to native code. |
| `-interpreted`, `--interpreted` | No | on | Compile PL/SQL to interpreted code (default; `-native` takes precedence). |
| `-scope`, `--scope` | No | none | PL/Scope settings (`IDENTIFIERS`, `STATEMENTS`, `ALL`). |
| `-warnings`, `--warnings` | No | none | PL/SQL warnings (`SEVERE`, `PERF`, `INFO`). |
| `-mviews`, `--mviews` | No | off | Report materialized views (optionally filtered by an `-mviews NAME` pattern, e.g. `-mviews DEP%`; bare = all), then `COMPILE` invalid ones and `REFRESH` stale ones. With `-force`, `REFRESH` every matching view. |
| `-synonyms`, `--synonyms` | No | off | Report-only: print `SYNONYMS TO SCHEMA: <OWNER>` tables mapping each synonym (optionally filtered by a `-synonyms NAME` pattern, e.g. `-synonyms APP%`; bare = all) to its target object, one privilege per row, `GRNT`, and `VALID`. Skips the object recompile and overview entirely; takes no action. |
| `-disabled`, `--disabled` | No | off | Report-only: print disabled constraints, invalid/function-disabled indexes, and disabled triggers in dedicated type tables (optionally filtered by a `-disabled NAME` pattern, e.g. `-disabled APP%`). Skips the object recompile and overview entirely. |
| `-jobs`, `--jobs` | No | off | Report-only: print today's scheduler job runs from `all_scheduler_job_run_details` in status-grouped compact tables (optionally filtered by a `-jobs NAME` pattern, e.g. `-jobs APP%`). Skips the object recompile and overview entirely. |
| `-errors`, `--errors` | No | off | Print the full per-line compile errors of objects that are still invalid: an ID/object/line/position table with each message listed below as `  <ID>) <text>`. |
| `-silent`, `--silent` | No | off | Suppress object overview details while keeping the standard banner, connection block, and final timer. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values; keep Python tracebacks. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
