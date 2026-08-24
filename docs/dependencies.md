# Query the Object Graph (adtai dependencies)

`dependencies` answers "what uses this?" and "what would I break?" against a local mirror of the Oracle data dictionary. Reach for it before changing a table or a package, when you need the blast radius rather than a guess.

The mirror is a single gitignored SQLite file at `config/internal/dependencies.db`, holding the raw dictionary tables stamped with an owner, so it is multi-schema, plus the APEX dictionary keyed by application id. Every query recomputes its answer from those raw mirrors.

<br>

## Examples

Refresh the mirror. Naming no query flag is what makes a run a refresh:

```bash
adtai dependencies -env DEV -schema SANDBOX
adtai dependencies -refresh -env DEV -schema APP CORE
adtai dependencies -refresh -env DEV -app 100 200
adtai dependencies -refresh -recent -env DEV -schema APP
adtai dependencies -refresh -force -env DEV -schema APP
```

Ask what an object uses, and what uses it:

```bash
adtai dependencies -from "VIEW.ADT_ANNO_TICKET_V"
adtai dependencies -to "TABLE.ADT_ANNO_TICKET"
```

Ask for the transitive blast radius, or walk a foreign key:

```bash
adtai dependencies -impact "TABLE.ADT_ANNO_TICKET"
adtai dependencies -tree "ORDER_ITEMS_ORDER_FK"
```

Check how stale the mirror is, or emit machine-readable output:

```bash
adtai dependencies -age
adtai dependencies -to "TABLE.ADT_ANNO_TICKET" -format yaml
```

<br>

## Output

A refresh prints one row per dictionary table, its name before the query starts and its counts after:

```text
APEX DEPLOYMENT TOOL - DEPENDENCIES
-----------------------------------


CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.1.0.0 | FREEPDB1


REFRESHING SANDBOX SCHEMA:
--------------------------
  USER_OBJECTS .................................................. 17 |      17

  RECOMPILING DUE TO WRONG PL/SCOPE ............................ 100%  0:00:00
  USER_DEPENDENCIES .............................................. 9 |       9
  USER_CONSTRAINTS .............................................. 10 |      10
  USER_CONS_COLUMNS ............................................. 10 |      10
  USER_IDENTIFIERS .............................................. 20 |      20
  USER_STATEMENTS ................................................ 1 |       1


TIMER: 3s
```

- The two numbers are `changed | total`. Detail views are bulk-pulled and filtered locally, so the second is what came back and the first is what mattered. Under `-force` or a named deep refresh only the fetched count shows, because those scopes are deleted before the fresh rows go in.
- A failing table completes its row with `FAILED` before the database error block prints the SQL that failed.
- A multi-schema refresh reads schema by schema, each with its own connection block and its own `TIMER`, banner printed once.
- With `-format yaml` or `-format md` this chrome moves to stderr, so stdout stays clean and pipeable.

A query prints one table and opens no connection:

```text
USED BY TABLE.ADT_ANNO_TICKET (2):
----------------------------------

  OBJECT TYPE         OBJECT NAME
  -----------------   ------------------
  MATERIALIZED VIEW   ADT_ANNO_TICKET_MV
  VIEW                ADT_ANNO_TICKET_V
```

- Objects render as two columns, split from the `TYPE.NAME` node on its **first** dot, so `PACKAGE BODY` stays intact. `-impact` adds a `DEPTH` column.
- There is no count column. `USER_DEPENDENCIES` is distinct per object pair, so a per-row reference count is always one.
- An empty result prints `(none)` rather than nothing. A missing mirror reports `No dependency database found` and exits `1`.
- `yaml` and `md` output keeps the dotted `TYPE.NAME` node form.

<br>

## Query or refresh, decided by one rule

**Name a query and it queries; name none and it refreshes.** A bare `adtai dependencies` refreshes, and so does any run carrying no query flag, so `-schema APP`, `-app 100`, `-force` and `-recent` each describe a refresh on their own.

Those last three steer the rebuild, so passing one beside a query is refused with `steers -refresh and cannot be combined with a query` and exit `2`.

Refresh is the only mode that connects, and the only one that reads `-config-dir` and `-env`. Query modes are entirely offline. There is no separate cache-rebuild step: re-running the refresh is the update path.

<br>

## The five query modes

| Flag | Answers |
| ---- | ------- |
| `-from OBJ` | What `OBJ` uses. |
| `-to OBJ` | What points at `OBJ`. |
| `-impact OBJ` | Everything affected if `OBJ` changes, walked transitively. |
| `-tree CONSTRAINT` | The foreign-key cascade around a named constraint. |
| `-age` | When each schema and application scope was last refreshed. |

Object names use the `TYPE.NAME` form the graph itself uses, for example `PACKAGE BODY.CORE` or `TABLE.CORE_LOGS`.

The `-impact` walk stops at `dependencies_max_depth` levels (project `config.yaml`, default `20`), and objects reachable only beyond that are left out. A referenced object counts as internal only when its owner is in the mirror, so references into schemas nobody refreshed are external and dropped from the graph.

`-tree` looks the constraint up in the mirror. For a foreign key, the `REFERENCES` table starts there, prints the referenced parent key row, and keeps walking toward higher parents. The `DEPENDENCIES` table walks the other way from the constraint's own table, printing key rows before child foreign-key rows, and is omitted when there are no children.

Its columns are `TABLE NAME`, `COLUMN NAME`, `CONSTRAINT NAME` and `TYPE`, sorted by traversal path.

`-age` reads the completion stamps every refresh writes, and prints `SCOPE TYPE`, `SCOPE` and a sortable `LAST REFRESH` timestamp, schemas first then applications. It is how an agent checks staleness per scope rather than guessing from a file's modification time.

<br>

## Steering a refresh

The two axes combine freely:

- **`-schema`** pulls the object inventory, uses `LAST_DDL_TIME` to spot what was added or changed, bulk-pulls the detail mirrors, filters them locally to the changed objects, deletes the old outbound rows for each, drops objects that no longer exist along with their relations, and leaves unchanged rows alone.
- **`-app`** sets the APEX workspace and security context through the same start block `export_apex` uses, rescans the application, and patches only the changed rows for it.

`-app` takes ids, a repeated flag, a space-separated list, or a range: `MIN-MAX` is closed and `MIN+` is open. Plain ids refresh exactly as given; a range resolves against the applications discovered across the configured schemas, and a range matching none exits `1`.

Object names or SQL wildcards after `-refresh` force a deep schema refresh for just those objects, deleting the matching rows in both directions first, and `-force` wipes the whole requested scope before reloading it.

`-recent` reloads only what changed, either in a window of days you name or, bare, since that scope's own last-refresh stamp. `-force` and named deep refreshes ignore it. Only a full refresh detects dropped objects.

The `-app` axis folds into its owning schema's segment when that schema is refreshed too, reading `REFRESHING <SCHEMA> SCHEMA AND APEX APP <id>/<alias>:`. Otherwise it becomes its own final segment, which is always the case for an app-only run.

<br>

## Disambiguating a query by owner

When the mirror tracks more than one owner, the same object name can exist in several schemas. `-schema` on a query mode pins it. Here the flag is offline, case-insensitive, and takes a repeatable space- or comma-separated list; omit it and every tracked owner matches.

Each mode matches it against the owner column it keys on:

- `-from OBJ -schema APP` keeps edges whose `OWNER` is `APP`, the instances that schema owns.
- `-to OBJ -schema APP` keeps edges whose `REFERENCED_OWNER` is `APP`.
- `-impact OBJ -schema APP` constrains the seed object by `REFERENCED_OWNER`; the walk outward is unchanged.

It narrows within the tracked set and never widens to untracked owners. An empty or whitespace value behaves as absent.

**A schema is one scope however you spell it.** Oracle owners are uppercase, but ADT reads the name from your argument or a connection-file key, so `-schema app` and `-schema APP` refresh the same scope and write the same rows. A mirror written before that held both, two complete copies under two stamps. The next refresh folds any such pair away, keeping the newer stamp and dropping the other rather than merging, since the older copy can still carry objects the newer refresh saw dropped.

<br>

## Column-level lineage from PL/Scope

Where the schema carries PL/Scope data, the identifier and statement mirrors let `-impact` resolve column lineage: per program unit, the exact table and view columns it touches, plus view-column to source `TABLE.COLUMN` lineage. `-impact` then appends an `AFFECTED COLUMNS` section listing view columns sourced from the impacted table.

Lineage resolves only when exactly one of a view's sources exposes that column. Ambiguous or unknown columns are kept with a null source rather than dropped. With no PL/Scope rows the section is simply omitted and nothing fails.

PL/Scope is a prerequisite `-refresh -schema` satisfies itself, on the same connection: it sets the session's `PLSCOPE_SETTINGS`, then recompiles the added or changed valid PL/SQL objects still missing that scope. A compile blocked by another session's lock reports `SKIPPED LOCKED <TYPE> <NAME>`, indented under the row it interrupted, and the refresh continues.

That line is worth reading. A skipped object keeps its old PL/Scope data, so its edges stay stale even though the run exits `0`. Re-run the refresh once the lock clears.

On a first refresh this is the slowest thing the command does, so it crawls rather than holding the header still: one redrawn `RECOMPILING DUE TO WRONG PL/SCOPE` row, opened in front of the first compile and closed at 100%.

**The row prints only when something is being recompiled.** A warm schema recompiles nothing, and neither does any incremental refresh whose changed objects carry no PL/SQL, which is the common case.

<br>

## APEX callers

When the APEX mirror has been refreshed with `-app`, `-impact` also appends an `APEX CALLERS` section: which application, page or shared component, and which component property uses the impacted database object.

Where PL/Scope can trace an impacted column through a view and the component property references that view column, the caller row carries the rendered column and its source `TABLE.COLUMN`. The same data appears under `apex:` in yaml and `## APEX callers` in md.

APEX refresh reads the release from the connection block before choosing its dictionary path. Releases before APEX 24.2 report that dependency scanning is unavailable and skip the app refresh. Supported releases run the APEX dependency scan silently, then drop the helper procedures it generates so scan internals never reach the console or an export.

<br>

## The clock the mirror records

Alongside each schema's refresh stamp, the mirror records that database's own UTC offset, read once per scope. `LAST_DDL_TIME` is a wall-clock reading taken on the database server, and `patch -create` compares it against repository file times taken here, so without the offset a database in another timezone shifts every one of those comparisons.

Nothing configures it. A mirror that predates it is refused by that gate rather than read on the wrong clock, which one refresh fixes.

The refresh connection is an ordinary ADT.ai connection and runs the ordinary session setup, `DDL_LOCK_TIMEOUT`, the identifier block, then `STARTUP.sql`. That is not cosmetic here: refresh issues session `ALTER` statements and compiles of its own, so on a schema whose DDL trigger requires a client identifier, a sessionless connection would fail the whole command rather than degrade.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-from`, `--from` | No | none | List the objects the given object depends on. |
| `-to`, `--to` | No | none | List the objects that depend on the given object. |
| `-impact`, `--impact` | No | none | List the transitive reverse impact of changing the given object. |
| `-tree`, `--tree` | No | none | Show the foreign-key reference and dependency cascade around a named constraint. |
| `-age`, `--age` | No | off | Offline: list when each schema and application scope was last refreshed. |
| `-refresh [NAME ...]`, `--refresh [NAME ...]` | No | on when no query flag is given | Connect and rebuild the mirror. This is what the command does with no query flag, so the flag is the explicit spelling of the default. Object names or SQL wildcards trigger a deep refresh for matching objects and the dependency rows on both sides of them. |
| `-force`, `--force` | No | off | Refresh only: delete the requested schema and application rows before reloading that scope. Rejected beside a query flag. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | off | Refresh only: reload just what changed in the last DAYS days, where DAYS may be a fraction of a day (`1/24` is the past hour). Bare `-recent` scopes each refresh to that scope's own last-refresh stamp, selected server-side and patched per object, so unchanged rows stay intact; a scope with no stamp is refreshed in full and one with no changes skips the detail pulls. Rejected beside a query flag. |
| `-app`, `--app` | Yes | none | APEX application ids whose dictionary to mirror, refresh only. Repeat, space-separate, or pass a range, `MIN-MAX` closed or `MIN+` open, resolved against the discovered applications. |
| `-format`, `--format` | No | `table` | Output format: `table`, `yaml` or `md`. |

Shared options (-root, -env, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [arguments.md](arguments.md).
