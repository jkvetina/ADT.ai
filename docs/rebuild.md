# Rebuild the Local Stores (adtai rebuild)

![Scan once. Everyone else just reads.](images/rebuild.png)

`rebuild` refreshes the local stores the rest of ADT.ai answers from, so [`patch`](patch.md), [`search`](search.md), [`calendar`](calendar.md) and [`recompile`](recompile.md) read a file instead of asking git or the database live. Run it after new commits, a change of branch, or a change in the schema.

Every run brings the branch's commit store up to date, then the object dependencies of the connection's default schema. `-app` also reads an APEX application: what it uses, and how its pages link. Both halves reload only what changed, so a steady-state run costs seconds.

<br>

## Examples

Update the commit store for the branch you are on, and the default schema's object dependencies:

```bash
adtai rebuild
```

Refresh named schemas instead, or wipe their rows and reload them whole:

```bash
adtai rebuild -schema APP CORE
adtai rebuild -force -schema SANDBOX
```

Also read an APEX application, or a range of them:

```bash
adtai rebuild -app 100
adtai rebuild -app 100-200
```

Rebuild a bounded window of commits, by count or by date:

```bash
adtai rebuild -limit 200
adtai rebuild -since 7
adtai rebuild -since 2026-08-01
```

Update named branches rather than the current one:

```bash
adtai rebuild -branch main -branch feat/PROJ-300-currency
```

Look at what the stores hold, and at the branches on the remote:

```bash
adtai rebuild -verify
adtai rebuild -reveal
adtai rebuild -reveal PROJ-300
```

<br>

## Output

The header names the branch and how many commits the run reads, one redrawn bar carries the scan, and the schema's object dependencies follow on their own connection:

```text
APEX DEPLOYMENT TOOL - REBUILD
------------------------------
    BRANCH | main
   COMMITS | 10 + 0

  REBUILDING ................................................... 100%  0:00:00


CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.3.0.0 | FREEPDB1


REFRESHING SANDBOX SCHEMA:
--------------------------
  USER_OBJECTS ................................................. 119 |     127
  USER_DEPENDENCIES ............................................. 51 |      51
  USER_CONSTRAINTS .............................................. 72 |      73
  USER_CONS_COLUMNS ............................................. 75 |      75
  USER_IDENTIFIERS ............................................. 240 |     240
  USER_STATEMENTS ................................................ 9 |       9


TIMER: 0s
```

- `COMMITS | 10 + 0` is an incremental update, the branch total plus the commits missing from the store. A full build prints the total alone, and a bounded window reads `COMMITS | 10 - 5` for a count or `COMMITS | 5 SINCE <date>` for a date.
- The bar advances per commit and the time on the right is what is left. A branch with no new commits leaves its store untouched.
- Each dictionary row reads `changed | total`. The detail views are pulled whole and filtered locally, so the second number is what came back and the first is what mattered. A failing table completes its row with `FAILED` before the error block prints the SQL.

<br>

## One store per branch

Stores go where `repo_commits_file` points, `./config/commits/#BRANCH#.db` by default. One file per branch is deliberate: a branch you no longer care about is one file you can delete, and deleting it costs nothing anywhere else. What a store holds is on [storage_commits.md](storage_commits.md).

`#BRANCH#` becomes one readable filename. Letters, digits, `.`, `_`, `-` and `/` are accepted; `/` is flattened to `-`, so `feat/PROJ-300-currency` writes `feat-PROJ-300-currency.db`. Anything else is rejected rather than escaped. If two accepted names flatten to the same filename, the exact branch recorded inside the store prevents them from sharing it.

Only the branch is rewritten: the separators in your own template are the folder layout you configured. The branch keeps its real name everywhere you read it, the `BRANCH |` header included.

YAML commit history is decommissioned. A configured `repo_commits_file` that does not end in `.db`, or an old `.yaml` cache beside the supported store path, stops the command with remove-and-rebuild guidance. ADT.ai does not convert it: the old format cannot represent the complete file-status data the SQLite store requires.

<br>

## Commit numbering

A commit's number is its position on the branch's first-parent line, `git log --first-parent --reverse` counted from `1`, so the numbers on your default branch never shift once handed out.

- **A new commit takes the next number.**
- **A merge is one new commit.** It takes the next number and carries every file the merged branch changed. The commits behind its second parent get no number and are not stored.
- **A dropped unpushed commit frees its number**, so the next commit takes it and the store keeps no hole. A rebase or a force-push is followed the same way.
- **A window bounds the work, never the numbering.** `-limit 1000` on a branch of 84,464 commits stores `83465` to `84464`. Widening it later fills in underneath.
- **`-force`, or a deleted store built again, reads the same numbers back.**

A store written by an older ADT.ai moves to this numbering once, on the next `rebuild`, and its merges are read again with their files.

<br>

## How far back a first build reaches

`patch_history_bottom_days` in `config/config.yaml` (default `365`) decides how far a from-scratch build walks. The oldest commit inside that window becomes the bottom of the store and older ones are never read. On a large repository that is the difference between a usable first run and an unusable one, since the expensive half of a rebuild is one file scan per commit.

It is a floor rather than a mode:

- An incremental run never re-cuts an existing store. Commits already below the floor are already numbered, and dropping them would open a hole.
- An explicit `-limit` or `-since` outranks it, and may reach further back than the project default.
- Raising the value pulls the extra commits in underneath, leaving every assigned number where it was.

Set it to `0` to walk the whole history.

<br>

## Incremental, bounded, or full

With no window flag, `rebuild` reads the existing store, takes its highest-numbered commit as the resume point, and fetches only what came after it. Stored records are reused verbatim and never re-hashed.

`-limit N` and `-since WHEN` both switch that branch to a full bounded window instead. They bound the same window, one by count and one by date, so they cannot be combined in normal mode: the pair is refused, exit `2`.

`WHEN` is a `YYYY-MM-DD` date or an integer number of days back. It resolves against the committer date at local midnight, so a commit made on the boundary day is included.

A branch whose store is missing, empty, or whose stored tip no longer exists is rebuilt in full. That fallback is per branch, so one stale branch in a `-branch` list does not force the others to re-scan.

<br>

## Object dependencies

The dependency mirror, `config/internal/dependencies.db`, is a local copy of the dictionary tables stamped with an owner, so one file holds many schemas. `search` answers graph questions from it, `patch` orders a release by it, and `recompile` ranks root causes with it. Its tables are on [storage_dependencies.md](storage_dependencies.md).

A refresh covers the connection's default schema, or the schemas `-schema` names. It reads `USER_OBJECTS`, uses `LAST_DDL_TIME` to spot what was added or changed, and reloads only those objects' rows. An object that no longer exists is dropped with its relations, and unchanged rows are left alone.

The mirror keeps no copy of the schema's source. [`search TERM`](search.md) reads the object files [`export_db`](export_db.md) wrote.

`-force` deletes the scope first and reloads all of it, so each row shows the fetched count alone. Commit numbering is never touched:

```text
REFRESHING SANDBOX SCHEMA:
--------------------------
  USER_OBJECTS ........................................................... 128
  USER_DEPENDENCIES ....................................................... 51
  USER_CONSTRAINTS ........................................................ 72
  USER_CONS_COLUMNS ....................................................... 74
  USER_IDENTIFIERS ....................................................... 240
  USER_STATEMENTS .......................................................... 9
```

Several schemas are refreshed one after another, each under its own connection block and its own `TIMER`, the way every [multi-schema run](console.md#multi-schema-runs) reads. A schema is one scope however you spell it, so `-schema app` and `-schema APP` refresh the same rows.

Two commands also keep the mirror level on their own, each under an `UPDATING DEPENDENCIES:` section with the same rows. [`export_db`](export_db.md) refreshes exactly the objects it just exported, or the whole schema when no mirror exists yet. [`patch`](patch_install.md#the-graph-gate) refreshes a schema the mirror is stale for before ordering it.

<br>

## PL/Scope

`USER_IDENTIFIERS` and `USER_STATEMENTS` are what let `search -impact` name the view columns a change reaches, and they need PL/Scope data. A schema refresh sets the session's `PLSCOPE_SETTINGS` and recompiles the added or changed valid PL/SQL objects still missing it, on the same connection.

On a first refresh that is the slowest thing the run does, so it crawls on one redrawn `RECOMPILING DUE TO WRONG PL/SCOPE` row. The row prints only when something is being recompiled, which a warm schema, or a refresh whose changed objects carry no PL/SQL, never needs.

A compile blocked by another session's lock reports `SKIPPED LOCKED <TYPE> <NAME>` under the row it interrupted, and the refresh continues. That object keeps its old PL/Scope data, so its edges stay stale while the run exits `0`. Run it again once the lock clears.

<br>

## The clock and the session

Beside each schema's refresh stamp the mirror records that database's own UTC offset. `LAST_DDL_TIME` is read off the database server's clock and `patch -create` compares it with file times taken here, so the offset keeps a database in another timezone from shifting every comparison. A mirror that predates it is refused by that gate until one refresh fixes it.

The refresh runs the ordinary session setup, `DDL_LOCK_TIMEOUT`, the identifier block, then `STARTUP.sql`. It issues session `ALTER` statements and compiles of its own, so on a schema whose DDL trigger requires a client identifier a sessionless connection would fail the whole run.

<br>

## An APEX application

`-app` adds three application caches to the run, on request only, since each reads the whole application:

- **What it uses.** The APEX dependency scan fills `APEX_USED_DB_OBJECTS` and `APEX_USED_DB_OBJECT_COMP_PROPS` in the mirror, so `search -impact` can name the page, component and property behind a database object. The tables are on [storage_dependencies_apex.md](storage_dependencies_apex.md).
- **What its code says.** Component text and static files, for `search TERM`.
- **How its pages link.** The application, its pages and its links go into `config/internal/flow.db` for `search -to` and `-from` on a page, and Mermaid, Graphviz DOT and JSON diagrams land under `config/flow/`. The tables are on [storage_flow.md](storage_flow.md), and what counts as a link is on [search.md](search.md#what-counts-as-a-link).

The application folds into its owner schema's segment when that schema is refreshed too:

```text
REFRESHING SANDBOX SCHEMA:
--------------------------
  USER_OBJECTS ................................................... 2 |     128
  RECOMPILING DUE TO WRONG PL/SCOPE ............................ 100%  0:00:00
  USER_DEPENDENCIES .............................................. 7 |      51
  USER_CONSTRAINTS ............................................... 0 |      73
  USER_CONS_COLUMNS .............................................. 0 |      75
  USER_IDENTIFIERS .............................................. 31 |     240
  USER_STATEMENTS ................................................ 4 |       9


APP 100/ORDERS, REFRESHING:
---------------------------
  SCANNING COMPONENTS .......................................... 100%  0:00:01
  APEX_USED_DB_OBJECTS ..................................................... 1
  APEX_USED_DB_OBJECT_COMP_PROPS ........................................... 1
  APEX_COMPONENT_SOURCE ................................................... 43
  APEX_STATIC_FILES ........................................................ 7


APP 100/ORDERS, REFRESHED:
--------------------------

  PAGES   EDGES   DIAGRAMS
  -----   -----   --------
      4       9          3


TIMER: 3s
```

- `SCANNING COMPONENTS` counts down from the application's last scan time.
- `APP 100/ORDERS, REFRESHING:` opens the application, once. The two counts under it are the rows the dependency scan stored, and the `DEPSCAN$<n>#<n>` helper procedures the scan generates are dropped again, so they never reach an export.
- The page links are read under the same header, and `REFRESHED:` closes it with the pages, links and diagrams stored. The application's rows are rewritten in one transaction, and every other application in the file is left alone.
- An application owned by a schema this run does not refresh gets a segment of its own, opening on an `APEX APPLICATIONS: <workspace> | <SCHEMA>` table.
- Before APEX 24.2 there is no scan to run, so the application is listed under `WARNING - APEX TOO OLD, SKIPPED:` as `APP 100 needs APEX 24.2 or newer`, and its page links open its header themselves.
- A whole-application scan the database refuses closes its row on `FAILED`, and the application is then scanned one page at a time, page 0 included, on a second `SCANNING COMPONENTS` row. The views are read after each page that scans, and the application's rows are written once from all of them, so one broken page costs that page rather than the application.
- When every page scans, the run carries on as if the first scan had worked. A page that fails as well is listed under `WARNING - COMPONENT SCAN FAILED, BROKEN PAGES:`, one row per page as `  2110 Opportunity Search`: the page id, right aligned so the names line up, and the page's own name from the APEX dictionary, or the id alone when there is no name. Each keeps the rows its last good scan wrote, and the run exits `1`. Only a page whose OWN data breaks the scan is ever listed: a crashed scan leaves the session unable to scan the next thing asked of it, so the session is reset before the first page and after any page the walk names, and a page that fails is scanned once more on a session of its own before it is named at all. A page that comes back clean from that second scan is collateral, stores its rows from it, and is never named.
- When no page scans, the application keeps all its rows and is named under `WARNING - COMPONENT SCAN FAILED, DEPENDENCIES KEPT:` as `APP 100/ORDERS kept its previous dependencies:` with the error that stopped the scan. APEX reports `ORA-01086: savepoint 'START_SCAN' never established` on top of that error, so that line and its `ORA-06512` frames are skipped. The component source and page links are refreshed either way, and every other application still scans.
- Either block prints below the application's whole section, under the `REFRESHED:` table, so the progress rows read as one uninterrupted list. Fixing the pages it names is the only way to restore the whole-application scan; APEX gives the scan no way to narrow further than a page, its `p_options` argument picks how the scan analyzes (all sources, dependencies only, PL/Scope identifiers, or errors only), never which component.

`-app` takes ids, a repeated flag, a list, or a range: `MIN-MAX` is closed and `MIN+` is open. A range resolves against the applications the configured schemas can see, and one matching none exits `1` on `-app RANGE MATCHED NO APPLICATIONS`.

The page links are read through the owner schema, or a configured schema that reaches it when your file does not list the owner; that route is silent, and the run exits `0`.

One no configured schema sees is named under `WARNING - APP NOT FOUND:`, one whose owner none reaches under `WARNING - SCHEMA NOT CONFIGURED:`, and either exits `1` with the rest still refreshing.

<br>

## With no connection

A bare `rebuild` walks the commits first and then needs its connection. With no connection file, no default environment or no default schema, the commit store is written and the run stops on `ERROR - CONFIGURATION NOT FOUND:`, exit `1`.

A run naming `-schema` or `-app` asked for the database by name, so the same gap refuses under `ERROR - CONFIGURATION NOT FOUND:` before any work, and exits `1`. The one exception is an `-app` run on a file with no default schema, which has no schema half and refreshes the application alone.

<br>

## Verifying a store

`rebuild -verify` reports what each store holds and changes nothing:

```text
COMMIT STORES:
--------------

  main                                 8 commits, 1-8, CONTIGUOUS
```

`CONTIGUOUS` means floor to ceiling with nothing missing. `rebuild` fills every position from the floor to the tip, so `BROKEN` means something outside ADT.ai wrote the file. A store bounded by `patch_history_bottom_days` starts above `1` and is still contiguous: the range below it is simply not stored. Exit code is `1` when any branch reports a problem.

<br>

## Inspecting and switching branches

`-reveal` lists branches without touching any store. It reads the remote refs (`refs/remotes/origin/*`) rather than your local heads, after a best-effort `git fetch --prune`, so the list is right whatever you have checked out and a failed fetch falls back to the cached refs:

```text
RECENT BRANCHES: (3)
----------------

  feat/PROJ-300-currency
  main
  fix/PROJ-204-trigger-order
```

- Rows are newest-first by committer date, clipped to 78 characters, with the count folded into the header. A truncated list reads `(20/1958)`, shown out of total.
- Filter words are AND-matched against the branch name and retitle the list `BRANCHES MATCHING <words>`. Each word is a case-insensitive contains-glob, so `feat 4995` keeps a branch holding both and `feat*4995` still works.
- `-limit` caps the rows here (default `20`, `0` lists all), and `-my` keeps branches whose tip-commit author email is your own.
- `-since WHEN` keeps branches whose tip commit is on or after `WHEN`, and composes with the rest: the date filter runs first, then `-limit` caps the survivors.
- `-reveal` and `-verify` never touch a database, so `-schema`, `-app`, `-force`, `-env` or `-key` beside either is refused, exit `2`.

Add `-switch [N]` to check the tree out to the Nth branch in the filtered order, 1-based, bare `-switch` meaning the first. The branch list is replaced by the branch you landed on and its own commits:

```text
BRANCH SWITCHED:
----------------

  feat/PROJ-300-currency

COMMITS:
--------

  2026-08-23 09:14 | PROJ-300: settle the rounding on the currency column
```

- Only commits made **on** the branch are listed, so the ones it inherited at creation are excluded. Switching to the default branch lists all of its commits.
- `-limit` caps this section rather than the branch list, since the rank resolves against the full filtered set, and `-my` keeps only your own commits.
- Already being on the target branch runs no git operations at all, so work in progress is left exactly where it is. Otherwise `git checkout` creates a local tracking branch when none exists, non-conflicting work rides along, and a checkout git refuses is shown verbatim.
- A rank outside the filtered range errors without switching, and `-switch` without `-reveal` is refused, exit `2`.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-app`, `--app` | Yes | none | APEX application ids whose dependency scan and page links to refresh as well. Repeat, space- or comma-separate, or pass a range, `MIN-MAX` closed or `MIN+` open, resolved against the discovered applications. |
| `-force`, `--force` | No | off | Delete the dependency and page-link rows in scope before reloading them, instead of reloading only what changed. Commit numbering is never touched. |
| `-branch`, `--branch` | Yes | current branch | Branch name or names to include. Any commit-ish git accepts works: a local branch, `origin/<name>`, a tag, or a SHA. A name git cannot resolve fails fast and names the flag that lists the branches. |
| `-reveal [WORD ...]`, `--reveal [WORD ...]` | No | off | Read-only branch inspector: list the remote branches with no store change, newest-first. Optional filter words are AND-matched against the branch name, each a case-insensitive contains-glob. |
| `-limit`, `--limit` | No | mode-dependent | **Normal mode:** maximum commits to read per branch, running a full bounded window. Absent, the run is an incremental update. **`-reveal`:** maximum branch rows (default `20`, `0` lists all). **`-reveal -switch`:** maximum commits for the switched branch (default `20`, `0` all); the rank is resolved against the full list, so this does not bound it. |
| `-since`, `--since` | No | off | A `YYYY-MM-DD` date or an integer number of days back. **Normal mode:** rebuild a full bounded window of every commit since that date; mutually exclusive with `-limit`. **`-reveal`:** keep only branches whose tip commit is on or after it, composing with `-limit` and the word filters. |
| `-my`, `--my` | No | off | In `-reveal`, keep branches whose tip-commit author email equals `git config user.email`. Under `-switch`, also keep only your own commits in the listing. |
| `-verify`, `--verify` | No | off | Read-only check: each branch store's commit count, its floor-to-ceiling range, and whether the numbering is `CONTIGUOUS`. Never scans git and never writes. Exits `1` when any branch reports a problem. |
| `-switch [N]`, `--switch [N]` | No | `1` when given | With `-reveal`, check the tree out to the Nth branch in the filtered order and print that branch and its own commits instead of the list. Errors without `-reveal`, and errors on a rank outside the range without switching. |

Shared options (-root, -env, -schema, -config-dir, -key, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
