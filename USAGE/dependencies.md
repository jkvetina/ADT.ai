# Dependencies — query the object graph (adtai dependencies)

`dependencies` answers "what uses this?" and "what would I break?" against a local SQLite mirror of the Oracle data dictionary. The mirror lives at `config/dependencies.db` — a single, gitignored database holding the raw dictionary tables (`USER_OBJECTS`, `USER_DEPENDENCIES`, `USER_CONSTRAINTS`, `USER_IDENTIFIERS`, …), each stamped with an `OWNER` so the model is multi-schema, plus the APEX dictionary (`APEX_USED_DB_OBJECTS`, …) keyed by `APPLICATION_ID`. Every query mode recomputes its answer from those raw mirrors at query time.

The command has two modes:

- **Query** (default): read `config/dependencies.db` and answer one of `-from`, `-to`, `-impact`, `-tree`, or `-age`. `-from OBJ` lists the objects `OBJ` depends on (what `OBJ` uses); `-to OBJ` lists the objects that depend on `OBJ` (what points at it); `-age` reports when each schema/app scope was last refreshed. With no query flag it prints a short hint. If the database does not exist yet it reports `No dependency database found` and exits `1`. On a multi-schema mirror an object name (e.g. `PACKAGE.CORE`) can be ambiguous across owners; add `-schema OWNER[,OWNER ...]` to disambiguate the queried object by the owner column each mode matches it on (see below). Query-mode `-schema` is fully offline — it never connects to the database and is a literal, case-insensitive owner list parsed locally.
- **Refresh** (`-refresh [NAME ...]`): connect to the database and refresh the mirror, one transaction per scope. Refresh has two independent axes, used in any combination: `-schema` pulls the object inventory, uses `LAST_DDL_TIME` to detect added/changed objects, bulk-pulls the `USER_*` detail mirrors, filters those detail rows in SQLite to changed objects only, deletes old outbound rows for each changed object, deletes dropped objects plus relations to/from them from the mirror, and keeps unchanged object rows intact; `-app` sets the APEX workspace/security-group/session context through the same start block used by `export_apex`, re-scans the requested APEX application when the connected APEX release supports it, and updates only changed `APEX_*` rows for that app. Passing object names or SQL wildcards after `-refresh` forces a deep schema refresh for just those objects: ADT.ai pulls matching objects and dependency rows to/from them, deletes matching SQLite rows in both directions first, then inserts the scoped rows. Add `-force` to wipe only the requested schema/app scope before reloading it. This is the only mode that needs `-config-dir`, `-env`, and `-schema`/`-app`. Refresh writes only `config/dependencies.db`; no derived YAML or other artifact is produced.

Every run prints the generic banner and a completion timer. App-only refresh prints `REFRESHING APEX APP: <id>/<alias>` when the application alias is visible in the APEX dictionary (`REFRESHING APEX APPS: <id>, <id>, ...` for several apps); combined schema/app refresh includes `APEX APP <id>/<alias>` in the dependency-database scope line. Immediately before each app's own rows, refresh prints a matching `APP <id>/<alias>, REFRESHING:` section header with a dashed underline — the same per-application section shape `export_apex` uses — so a multi-app or ranged `-app` refresh reads app by app instead of one flat combined listing. APEX refresh reads the APEX version shown in the connection block before choosing its dictionary path: releases before APEX 24.2 print that APEX dependency scanning is unavailable and skip the app refresh; APEX 24.2 uses discovered `APEX_USED_DB_OBJECTS`, `APEX_USED_DB_OBJECT_COMP_PROPS`, and `APEX_USED_DB_OBJ_DEPENDENCIES` columns joined into the mirror shape; APEX 26.1+ uses the full current `USED_DB_OBJECT_*` query path. Supported APEX refreshes run `APEX_APP_OBJECT_DEPENDENCY.SCAN` silently, then drop generated `DEPSCAN$...` helper procedures so scan internals do not clutter the console or exports. Refresh progress uses fixed-width non-timer rows: the table name is printed before the query/upsert starts, and the row is completed with dots once the result is known; if a table query fails, the active row is completed with `FAILED` before the database error block prints the failing SQL. Incremental schema refresh rows show `changed | total`, with `total` left-padded to seven characters, because detail views are bulk-pulled and filtered locally; under `-force` or named-object deep refresh, schema refresh rows show only the fetched count because those scopes are deleted before the fresh rows are inserted. APEX refresh rows use the same two-space indentation and show the updated count for each app table. In the default `table` format these go to stdout like the other commands; with `-format yaml` or `-format md` the chrome moves to stderr so stdout stays clean and pipeable.

`-app` takes one or more application ids — repeat the flag or space-separate them under a single `-app` (`-app 100 200 300`) — and also accepts a range: `MIN-MAX` is the closed range `MIN..MAX` and `MIN+` is every id `>= MIN`. Plain ids are refreshed exactly as given; a range is resolved against the applications discovered across the configured schemas (the same shared APEX app-selection code `export_apex` and `flow` use), so only existing apps inside the range are refreshed. A range that matches no discovered application exits `1`.

When you refresh by application id (`-app <id>`, without `-schema`), `dependencies -refresh` first reads the cached `config/apex_apps.yaml` (written by earlier `export_apex` runs) to pick the schema it connects through. The refresh runner pulls every `APEX_*` view over a single `app_schema` connection, so when all requested apps share one recorded non-default `owner` schema it connects straight to that owner schema and skips the wasted default-schema connection — for example `-refresh -app 160` connects to GSN instead of the default DA. Apps not yet recorded in the file, a missing file, apps recorded against the default schema, several `-app` ids that map to different owners (one connection cannot serve both), or an explicit `-schema` all fall back to connecting to the environment default schema.

The SQLite mirror creates persistent lookup indexes for the hot query paths: reverse dependency traversal, object source lookup, constraint/table lookup, table-column lookup, object-type cleanup scans, and APEX object joins. Owner/object and `APPLICATION_ID` lookups use the mirror tables' primary-key prefixes, so refresh avoids duplicate secondary indexes.

Refresh the schema mirror, the APEX mirror, or both in one connection:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai dependencies -refresh -env DEV -schema APP
adtai dependencies -refresh -env DEV -app 100
adtai dependencies -refresh -env DEV -app 100 200 300
adtai dependencies -refresh -env DEV -app 100-200
adtai dependencies -refresh -env DEV -app 300+
adtai dependencies -refresh -env DEV -schema APP -app 100
adtai dependencies -refresh CORE CORE% -env DEV -schema APP
adtai dependencies -refresh CORE,API_% -env DEV -schema APP
adtai dependencies -refresh -force -env DEV -schema APP
```

List what an object depends on, and what depends on it:

```bash
adtai dependencies -from "PACKAGE BODY.CORE"
adtai dependencies -to "TABLE.CORE_LOGS"
```

Show the transitive reverse impact of a change:

```bash
adtai dependencies -impact "TABLE.CORE_LOGS"
```

The transitive walk stops at `dependencies_max_depth` levels (project `config.yaml`, default `20`); objects reachable only beyond that depth are omitted from the impact list.

Check how fresh the mirror is, per scope, without connecting:

```bash
adtai dependencies -age
```

`-age` is an offline query mode: every `-refresh` stamps the completion time of each refreshed scope into the `dependencies.db` `_meta` table (`last_refresh:schema:<OWNER>` and `last_refresh:app:<ID>` keys), and `-age` reads them back. The default `table` format prints one row per scope with `SCOPE TYPE` (`SCHEMA`/`APP`), `SCOPE`, and `LAST REFRESH` (a sortable `YYYY-MM-DD HH:MM:SS` local timestamp), schemas first then apps. `-format yaml` emits an `age:` list and `-format md` an `## Age` list. Like the other query modes it never connects, and it reports `No dependency database found` and exits `1` when the database is absent. This lets an agent check staleness by scope instead of guessing from the database file's mtime.

In the default `table` format every listed object is rendered as two columns — `OBJECT TYPE` and `OBJECT NAME` — split from the `TYPE.NAME` node on its first dot, so a multiword type like `PACKAGE BODY` stays intact in `OBJECT TYPE`. `-impact` keeps its `DEPTH` column alongside the split. There is no count column: `USER_DEPENDENCIES` is distinct per object→object pair, so a per-row reference count is always 1. The `-format yaml` and `-format md` outputs are unchanged and still emit the dotted `TYPE.NAME` node form for backward compatibility.

### Disambiguate a query by owner with -schema (offline)

When the mirror tracks more than one owner, the same object name can exist in several schemas. Pass `-schema` on any query mode to pin the queried object to one or more owners. It is offline (no connection), case-insensitive, and accepts a comma-separated list; omit it and every tracked owner is matched (the historical behavior). An empty or whitespace value behaves as absent. Each mode matches `-schema` against the owner column it keys on:

- `-from OBJ -schema S` — the dependent side: keep only edges whose `OWNER` is in `S` (the `OBJ` instances owned by `S`).
- `-to OBJ -schema S` — the referenced side: keep only edges whose `REFERENCED_OWNER` is in `S`.
- `-impact OBJ -schema S` — constrains the seed object to `REFERENCED_OWNER` in `S`; the transitive walk outward is unchanged.

`-schema` narrows within the tracked-owner set; it never widens to untracked owners.

```bash
adtai dependencies -from "PACKAGE.CORE" -schema APP
adtai dependencies -to "TABLE.SHARED" -schema APP,OPS
adtai dependencies -impact "TABLE.SHARED" -schema OPS
```

Show a foreign-key cascade around a specific constraint:

```bash
adtai dependencies -tree "ORDER_ITEMS_ORDER_FK"
```

`-tree` looks up the named constraint in the SQLite mirror. For a foreign key, the `REFERENCES` table starts with that FK, prints the referenced parent PK/UK row, then keeps walking toward higher parent tables through their own FKs. The `DEPENDENCIES` table walks the other way from the named constraint's table, printing key rows before child FK rows when children exist. The visible columns are `TABLE NAME`, `COLUMN NAME`, `CONSTRAINT NAME`, and `TYPE`, sorted by traversal path. The dependency table is omitted when no child FK rows exist.

When the APEX mirror has been refreshed with `-app`, `-impact` also appends an `APEX CALLERS` section showing which application, page/shared component, and component property uses the impacted database object. If PL/Scope can trace an impacted table column through a view and the APEX component property references that view column, the APEX caller row carries the rendered column and its source `TABLE.COLUMN`. The same data is emitted under `apex:` for `-format yaml` and under `## APEX callers` for `-format md`. This Phase 2 lineage uses the `APEX_USED_DB_OBJECT*` dictionaries already captured by `-refresh -app`; the APEX 26.1+ `APEX_DB_DICTIONARY` package is an optional future annotation source, not a dependency for the core blast radius.

Emit machine-readable output for piping:

```bash
adtai dependencies -from "PACKAGE BODY.CORE" -format yaml
```

There is no separate "rebuild cache" step — re-running `-refresh` is the update path. Object names use the `TYPE.NAME` form shown in the graph (for example `PACKAGE BODY.CORE`, `TABLE.CORE_LOGS`). Empty results print an explicit `(none)` rather than nothing. A referenced object is treated as internal only when its owner is present in the tracked mirror; references to schemas that were not refreshed are external and are dropped from the graph.

## Column-level dependencies (PL/Scope)

When the schema carries PL/Scope data, the `USER_IDENTIFIERS` and `USER_STATEMENTS` mirrors let `-impact` resolve column-level lineage: per program unit, the exact table/view columns it touches, plus view-column → source `TABLE.COLUMN` lineage. Lineage is resolved only when exactly one of the view's sources exposes that column; ambiguous or unknown columns are kept with a `null` source, never dropped. `-impact` then appends an `AFFECTED COLUMNS` section listing view columns sourced from the impacted table (also in `yaml`/`md` output as `columns:` / `## Affected columns`). When no PL/Scope rows exist the section is simply omitted — nothing fails.

PL/Scope is a prerequisite that `-refresh -schema` now satisfies automatically, on the same Oracle connection: it sets `PLSCOPE_SETTINGS='IDENTIFIERS:ALL,STATEMENTS:ALL'` on the session, then recompiles added/changed VALID PL/SQL objects that are still missing that scope (reusing the recompile module's compile statement). The shared Oracle connection bootstrap sets `DDL_LOCK_TIMEOUT = 10` immediately before `STARTUP.sql`, and `STARTUP.sql` can override that value; if a compile hits `ORA-04021` because another session holds the object lock, refresh reports `SKIPPED LOCKED <TYPE>.<NAME>` and continues. No separate flag, no full missing-scope sweep, and no second connection are involved.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder holding `config/dependencies.db` and used for config and connection lookup. |
| `-from`, `--from` | No | none | List the objects that the given object depends on (what `OBJ` uses). |
| `-to`, `--to` | No | none | List the objects that depend on the given object (what points at `OBJ`). |
| `-impact`, `--impact` | No | none | List the transitive reverse impact (everything affected if the given object changes). |
| `-tree`, `--tree` | No | none | Show foreign-key reference and dependency cascade paths around a named constraint. |
| `-age`, `--age` | No | off | Offline: list when each schema/app scope was last refreshed, read from the `_meta` last-refresh stamps. No connection. |
| `-refresh [NAME ...]`, `--refresh [NAME ...]` | No | off | Connect to the database and rebuild the local mirror. Optional object names or SQL wildcards trigger a deep schema refresh for matching objects and dependency rows to/from them. Required for `-schema` and `-app`. |
| `-force`, `--force` | No | off | Refresh-only: delete the requested schema/app rows from the SQLite mirror before reloading that scope. |
| `-schema`, `--schema` | Yes | refresh: environment default DB schema; query: all tracked owners | With `-refresh`, the schema(s) whose `USER_*` dictionary to mirror. On a query mode (`-from`/`-to`/`-impact`), an offline, case-insensitive, comma-separated owner filter that disambiguates the queried object by the owner column that mode matches on. |
| `-app`, `--app` | Yes | none | APEX application id(s) whose `APEX_*` dictionary to mirror (refresh only). Repeat or space-separate for multiple ids, or pass a range — `MIN-MAX` (closed) or `MIN+` (open), resolved against the discovered applications. |
| `-format`, `--format` | No | `table` | Output format: `table`, `yaml`, or `md`. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML (refresh only). ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default environment | Connection environment to refresh from, for example `DEV` (refresh only). |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords (refresh only). |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
