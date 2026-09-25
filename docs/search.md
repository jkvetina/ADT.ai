# Search History and the Object Graph (adtai search)

![Two matches, and the old file back.](images/search.png)

`search` finds the commit that touched a database object, by object name, type, file path, author, or date. Reach for it when you know what changed but not when, and a `git log` over a repository of exported DDL would be too blunt.

It also answers what is connected to what: what an object uses, what uses it, what a change would break, which database objects an APEX application uses, and which of its pages lead into a page and where it leads next.

And it finds where a piece of text lives, a JavaScript function, a plugin name, a column, across the APEX components, the static files, the database source, the commit history and the project's own files, in one run. With `-data` it finds a text or a number in the database's rows instead, on search_data.md.

Every answer comes from a local store [`rebuild`](rebuild.md) keeps: history from the commit store, the graph and the APEX text from the dependency mirror ([storage_dependencies.md](storage_dependencies.md)) and the page links ([storage_flow.md](storage_flow.md)). A graph question refreshes a missing or stale store first ([below](#when-a-store-is-missing-or-stale)). The database source is read from the files [`export_db`](export_db.md) wrote.

<br>

## Examples

Find where a piece of text lives. The text goes first, before any flag:

```bash
adtai search calcOrderTotal
adtai search calc_order_total -layer DB
adtai search "order total" -layer APEX,STATIC -app 100 -page 10
adtai search SHOP_CART -layer GIT -branch release/2.0
```

List the newest commits in the branch store:

```bash
adtai search
adtai search -limit 50
```

Search commit subjects, or the paths a commit touched. Terms inside one flag are AND-matched and case-insensitive:

```bash
adtai search -summary currency
adtai search -file order_api
adtai search -file workflow -files 50
```

Search by database object, resolved from the exported path layout. `-type` and `-name` are SQL LIKE patterns, the same language `export_db` reads, so `%` stands for any run of characters and `_` for a single one:

```bash
adtai search -type VIEW -name MONTHLY_REPORT_V
adtai search -type PACKAGE,VIEW
adtai search -type "PACKAGE%"
adtai search -name "SHOP%"
```

A literal `_` or `%` in a name is escaped with `\`, the same character SQL LIKE always has: `-name 'CORE\_LOCK'` matches only that name, where `-name CORE_LOCK` would also match `COREXLOCK`. Quote the flag, or the shell eats the backslash before ADT.ai ever sees it.

Search by author, branch, commit reference, hash prefix, or date. `-by` is a pattern too, so a partial address needs its own `%`:

```bash
adtai search -by bob@example.com
adtai search -by "bob%"
adtai search -branch feat/PROJ-300-currency -commit 5+
adtai search -hash 565fcf1a
adtai search -since 2026-08-01 -until 2026-08-20
adtai search -recent 7
```

A commit made under a personal address listed in `repo_authors` (`config/config.yaml`, `personal_address: company_address`) shows the company address, and `-by` and `-my` match it under either one.

Put a file's historical version back in place, for `git diff` to show:

```bash
adtai search -file monthly_report_v -commit 7 -restore
```

Ask what an object uses, what uses it, and what a change to it would reach:

```bash
adtai search -from "PACKAGE BODY.MART_ADMIN"
adtai search -to EMP -schema SANDBOX
adtai search -impact EMP
adtai search -constraint FK_DEPTNO
```

Ask which pages link into a page, and where a page leads, naming the page as `APP.PAGE`:

```bash
adtai search -to 100.1
adtai search -from 100.1
```

List the database objects an APEX application uses, on every page or on some:

```bash
adtai search -app 100
adtai search -app 100 -page 1-2 -type TABLE
```

Emit a graph answer as data rather than a table:

```bash
adtai search -to EMP -format yaml
adtai search -from 100.1 -format md
adtai search -app 100 -format yaml
```

<br>

## Output

One block per commit, newest first: the store's own commit number and the subject, then the author, the commit timestamp and the short hash. A file selector adds the changed-file rows under each commit:

```text
APEX DEPLOYMENT TOOL - SEARCH
-----------------------------

COMMITS:
--------
7) PROJ-300: monthly_report_v groups by currency
  dev@example.com | 2026-08-20 09:14 | 565fcf1a
    - SANDBOX/database/views/
      - M | monthly_report_v.sql

2) PROJ-101: widen the report to carry the order total
  dev@example.com | 2026-08-12 09:14 | 19915eed
    - SANDBOX/database/views/
      - M | monthly_report_v.sql

1) PROJ-101: add the monthly report view
  dev@example.com | 2026-08-12 09:14 | f8b645c0
    - SANDBOX/database/views/
      - A | monthly_report_v.sql

TIMER: 0s
```

- The leading number is the commit's position in the branch store, which is what `-commit` takes. It is stable for a given store and unrelated to the hash.
- `A`, `M` and `D` are git's own status letters, stored per commit at rebuild time. Timestamps read `YYYY-MM-DD HH:MI`, never ISO with a `T`.
- The files group under their folder, one row per directory below it and two spaces further in each time, the same shape every file list on `adtai` prints. `nested_files: False` in `config.yaml` gives the flat `    - M | <path>` rows instead; the rule is on [config.md](config.md).
- `-file`, `-type` and `-name` turn the file rows on by themselves, capped at 20 per commit. `-files N` sets another cap and `-files 0` turns them off.
- A search that matches nothing prints `No commits found.` and exits `0`.

A graph question prints one table instead of the commit list:

```text
APEX DEPLOYMENT TOOL - SEARCH
-----------------------------

USED BY EMP (5):
----------------

  OBJECT TYPE         OBJECT NAME
  -----------------   -------------------
  FUNCTION            MART_SALARY_GRADE
  MATERIALIZED VIEW   MART_EMP_BY_DEPT_MV
  PACKAGE BODY        MART_ADMIN
  TRIGGER             MART_EMP_SALARY_TRG
  VIEW                ADT_PATCH_PROBE_V


TIMER: 0s
```

- An object renders as two columns, split from its `TYPE.NAME` node on the first dot, so `PACKAGE BODY` stays whole. `-impact` adds a `DEPTH` column.
- There is no count column. `USER_DEPENDENCIES` holds one row per object pair, so a count per row would always read one.
- `-from` titles the table `USES <OBJ>` and `-impact` `IMPACT OF <OBJ>`. `-constraint` prints two tables of its own, under [Walking a foreign key](#walking-a-foreign-key).
- An empty answer prints `(none)` rather than nothing.

A page answer lists one link per row:

```text
APEX DEPLOYMENT TOOL - SEARCH
-----------------------------

LINKS INTO APP 100 PAGE 1 (4):
------------------------------

  FROM APP   FROM PAGE   SRC TYPE     COMPONENT        FLAG
  --------   ---------   ----------   --------------   ----
       100   2           BRANCH       Back to Orders   PAGE
       100   4           BRANCH       Back to Orders   PAGE
       100   3           BUTTON       BACK_TO_ORDERS   PAGE
       100   shared      LIST_ENTRY   Orders           PAGE


TIMER: 0s
```

- `-from` prints `LINKS FROM APP <id> PAGE <n>` with `TO APP` and `TO PAGE` in the first two columns. The number in the header is how many rows follow it.
- `FROM PAGE` reads `shared` for a list entry, tab or navigation-bar entry, which belongs to the application rather than to one page. Only links that resolve to a page are listed; the flags are under [What counts as a link](#what-counts-as-a-link).
- Component text is cut at 30 characters so the width stays stable. A report column link stored before the label fix can still hold heading text where a column name belongs, and reads `COL_<component_id>` until `rebuild -app` runs again.

An application answer lists the objects it uses, one table per application:

```text
APEX DEPLOYMENT TOOL - SEARCH
-----------------------------

OBJECTS USED BY APP 100 (1):
----------------------------

  OBJECT TYPE   OBJECT NAME   PAGES   COMPS
  -----------   -----------   -----   -----
  SCHEMA        SANDBOX           0       0


TIMER: 0s
```

- Rows sort by type, then name. `PAGES` counts the distinct pages whose components use the object and `COMPS` the components, so a shared component adds to `COMPS` alone.
- APEX 24.2 and later give a component no id, so there `COMPS` tells components apart by page, type and name.
- The parsing schema is a row of its own, used by the application rather than by a page, so it reads `0` and `0`.

A text search lists its hits, then what each database object hit uses and is used by:

```text
APEX DEPLOYMENT TOOL - SEARCH
-----------------------------

DB HITS (2):
------------

  OBJECT      TYPE           LINE   FLAG
  ---------   ------------   ----   -------
  ORDER_API   PACKAGE BODY      2   DEFINED
    FUNCTION calc_order_total (p_order_id NUMBER) RETURN NUMBER IS
  ORDER_API   PACKAGE BODY      5
    END calc_order_total;


USES / USED BY:
---------------

  OBJECT      RELATION   TYPE        NAME
  ---------   --------   ---------   --------
  ORDER_API   USES       TABLE       ORDERS
  ORDER_API   USED BY    VIEW        ORDERS_V
  ORDER_API   USED BY    APEX APP    100
  ORDER_API   USED BY    APEX PAGE   100.10


TIMER: 0s
```

- Each layer with a hit gets a table of its own, `APEX HITS`, `STATIC HITS`, `DB HITS`, `GIT HITS` and `FILES HITS`, with only the columns that layer fills. With no hit anywhere, `HITS (0):` reads `(none)`.
- One row per matching line, and the line itself under it, indented. `LINE` counts from 1 within the object, the component property or the file. A whole-file row has no line and nothing under it. `FLAG` reads `DEFINED` when the line defines something whose name holds the text. A column no row fills is left out, so `FLAG` shows only when a line defines the text.
- Every column is as wide as its longest value; the names are cut only when one value alone would carry the row past 78 columns, and a path is cut from the left so its file name stays. The line under a row has its whitespace collapsed, and when it is longer than 74 columns it is cut around the text, with `...` where it was cut.
- `PAGE LINKS` follows for the pages with an APEX hit, and `WARNING - NOT SEARCHED` closes the report; both are under [Finding a piece of text](#finding-a-piece-of-text).

<br>

## History or the graph, decided by one rule

**A text before any flag makes the run a text search**, covered under [Finding a piece of text](#finding-a-piece-of-text). Otherwise, **a graph flag makes the run a graph question; without one it searches history.** The graph flags are `-from`, `-to`, `-impact`, `-constraint` and `-app`, and a run asks one of them. Two in one run are refused with `-from / -to CANNOT BE COMBINED`, exit `2`.

A history filter beside a graph question does nothing there, so it is refused rather than ignored: `-summary CANNOT BE COMBINED WITH -to`, exit `2`. That covers every history flag, `-type`, `-name` and `-limit` included, except that `-app` reads `-type` and `-name` as filters of its own.

The other way round, `-schema` and a `yaml` or `md` `-format` belong to the graph, so a history search refuses them with `NEEDS A GRAPH QUERY`. `-page` narrows `-app` and is refused without it.

<br>

## Finding a piece of text

`adtai search TERM` looks for the text in five layers and lists the hits in a table per layer. The match is a case-insensitive substring, and `%` and `_` are the characters they are, never wildcards.

| Layer | Where it looks | A hit is |
| ----- | -------------- | -------- |
| `APEX` | The text properties of every component of the refreshed applications: code, SQL, attributes, templates. | A line of one property: `APP`, `PAGE`, `COMPONENT` (leading with the component type), and the property in front of the line under the row, `JAVASCRIPT_CODE: function orderBadge(count) {`. |
| `STATIC` | The text static files of the applications, their plugins and their workspace. | A line of the file, or the whole file when it is minified or over 512 KB: `APP`, `FILE`, `SCOPE`. |
| `DB` | The object files [`export_db`](export_db.md) wrote for `-schema`, or the configured default schemas, `DATA` excluded. Nothing connects. | A line of the object: `OBJECT`, `TYPE`. |
| `GIT` | The branch's commit store. | A commit whose subject, or a path it changed, holds the text: `COMMIT`, `FOUND IN` (`SUMMARY` or `FILE`), and the subject or path under the row. |
| `FILES` | The project's working tree, untracked files included, through `git grep`. | A line of the file, or the whole file when it is minified or over 512 KB: `FILE`. Binary files are skipped. |

**Write TERM first.** `-app`, `-page`, `-schema` and `-layer` each take a list, so a word typed after one of them is read as one more value of it. Quote a text holding spaces.

`-layer` names the layers to search, `-layer DB` or `-layer APEX,STATIC`, and all five are searched without it. `-app` narrows `APEX` and `STATIC` to those applications, while workspace files are always searched. `-page` narrows `APEX` and needs `-app`. `-schema` narrows `DB` and `-branch` picks the commit store `GIT` reads.

Any other flag beside a text is refused before the run starts, and so is a narrowing flag whose layer `-layer` leaves out: `-layer LEAVES OUT WHAT -branch NARROWS`, exit `2`.

**A layer is brought up to date before it is read.** `APEX` and `STATIC` read the source [`rebuild -app`](rebuild.md#an-apex-application) stores, and `GIT` the branch's commit store. One missing, or older than the application's last change in the database, is refreshed first, on the same screen. `DB` reads the object files [`export_db`](export_db.md) wrote and never connects. What still could not be read, an exported file `DB` could not open included, is named under `WARNING - NOT SEARCHED:` at the end:

```text
WARNING - NOT SEARCHED:
-----------------------
  APEX / STATIC: APP 200 is not mirrored, and could not be refreshed
  DB: SCHEMA HR has no exported files
  DB: sandbox/database/views/orders_v.sql: Permission denied
  GIT: no commit store for branch dev, and git could not build it
```

Without a connection, or with a database that does not answer, the layers are searched as they stand and the rest named there. The run exits `0` when it searched at least one layer, `1` when it could search none.

After the hits, `USES / USED BY` lists what each database object hit uses and is used by, the APEX pages using that owner's object included, and `PAGE LINKS` lists the links into and out of each page with an APEX hit, both ends written `APP.PAGE`. Each is omitted when it has nothing to say.

<br>

## An object or a page, told by its first character

`-from` and `-to` take one value either way. **A value that opens on a digit is a page**, written `APP.PAGE`: `122.50` is page 50 of application 122. Oracle names cannot open on a digit, so no object is ever mistaken for one.

A value like `100` with no page is refused as `PAGE MUST BE APP.PAGE, LIKE 122.50`.

Anything else is an object, in the `TYPE.NAME` form the graph uses (`PACKAGE BODY.CORE`, `TABLE.CORE_LOGS`) or as a bare name, which matches every type of that name.

| Flag | On an object | On a page |
| ---- | ------------ | --------- |
| `-from` | What it uses. | The pages it links to. |
| `-to` | What uses it. | The pages linking into it. |
| `-impact` | Everything affected if it changes, walked transitively. | |
| `-constraint` | The foreign-key cascade around a named constraint. | |

<br>

## How far an impact walk reaches

The walk stops at `dependencies_max_depth` levels (project `config.yaml`, default `20`), and an object reachable only beyond that is left out. A referenced object counts only when its owner is in the mirror, so a reference into a schema nobody refreshed is external and dropped from the graph.

Two sections can follow the `IMPACT OF` table. Each is omitted when the mirror holds nothing for it, and nothing fails:

- **`AFFECTED COLUMNS`** lists the view columns sourced from the impacted table, as `VIEW`, `COLUMN` and `SOURCE`. It reads the PL/Scope data a schema refresh collects. A column resolves only when exactly one of the view's sources exposes it; an ambiguous or unknown one is kept with a null source rather than dropped.
- **`APEX CALLERS`** names the application, page, component and property that use the impacted object, once [`rebuild -app`](rebuild.md#an-apex-application) has scanned that application. Where PL/Scope traces an impacted column through a view that the property references, the row carries the column, and `yaml` adds its source `TABLE.COLUMN`.

<br>

## Walking a foreign key

`-constraint` looks the constraint up in the mirror. For a foreign key, `REFERENCES TO` starts there, prints the parent key it references, and keeps walking toward higher parents.

`DEPENDENCIES OF` walks the other way, from the constraint's own table, printing key rows before child foreign-key rows, and is omitted when there are no children. Both tables carry `TABLE NAME`, `COLUMN NAME`, `CONSTRAINT NAME` and `TYPE`, sorted by traversal path.

<br>

## What counts as a link

A link is any component that sends a user to a page: page branches, buttons, list entries, tabs, navigation-bar entries and report column links. A report column link is labelled with the dictionary's `COLUMN_ALIAS`, since that is the field the report-column views expose.

Every link carries a flag saying how far its target resolved:

| Flag | Meaning |
| ---- | ------- |
| `PAGE` | A page in the same application: the link names no application, a substitution string such as `&APP_ID.`, or the application's own id or alias. The common case. |
| `CROSS_APP` | A link into another application by its id or its alias (`f?p=HR:5`), indexed by the resolved target application and page. An alias is looked up in the linking application's workspace. |
| `DYNAMIC` | The target page is computed at runtime, from a substitution string or an item value, and cannot be resolved statically. |
| `NONE` | The link leaves APEX entirely, or carries no page target at all. |

`-to` and `-from` list only `PAGE` and `CROSS_APP`, and so do the Mermaid and DOT diagrams `rebuild -app` writes. The JSON diagram keeps every link, `DYNAMIC` and `NONE` included, so other tooling can decide for itself what to draw.

<br>

## What an application uses

`-app` reads the objects [`rebuild -app`](rebuild.md#an-apex-application) stored for an application in the dependency mirror. It takes ids, a list, or a range, `MIN-MAX` closed or `MIN+` open, resolved against the applications the mirror holds, and prints them in id order.

`-page` keeps the objects used on those pages, in the same id and range forms, and counts only them. `-type` and `-name` are SQL LIKE patterns on the object, and `-schema` pins its owner.

An id the mirror holds no scan of is named under `WARNING - APP NOT LOADED:` as `APP 999 is not loaded, and could not be refreshed`, above the tables of the ones it does hold, and the run exits `1`. A range matching none exits `1` on `-app RANGE MATCHED NO APPLICATIONS`.

<br>

## Narrowing an object by owner

When the mirror holds several schemas, one object name can exist in more than one. `-schema` pins the owner, case-insensitively, as a repeatable space- or comma-separated list: `-from` keeps the instances that schema owns, `-to` keeps the objects that use that schema's one, and `-impact` roots its walk there, leaving the walk outward unchanged.

It narrows within the owners the mirror tracks and never widens to one it does not, and `-impact` lists only the APEX callers of the pinned object. A page has no owner and a constraint lookup is by name alone, so `-schema` beside either is refused rather than ignored.

<br>

## Machine-readable answers

`-format yaml` and `-format md` render the same answer as data. Stdout then carries the document and nothing else; the banner, any warning and `TIMER` go to stderr, so the output stays pipeable.

An object answer keeps the dotted `TYPE.NAME` form, under `uses:`, `used_by:` or `impact:`. `-impact` adds `columns:` and `apex:` in `yaml`, and `## Affected columns` and `## APEX callers` in `md`, whenever those sections print. A page answer is the table's rows as data, under `links_into:` or `links_from:`, with the page named as `APP.PAGE`.

An application answer is one `yaml` document per application, `app:` and its `objects:`, each with `type`, `name`, `owner`, `pages` and `comps`. In `md` it is a `## Objects used by APP 100 (1)` heading over `- SCHEMA.SANDBOX (pages 0, comps 0)` rows.

<br>

## When a store is missing or stale

A graph question never hands you a rebuild to run. It refreshes what it needs first, on the same screen, under `-format yaml`/`md` on stderr, then answers:

- A page question, `-to 122.20`, whose application the navigation store lacks runs the refresh `adtai rebuild -app 122` runs, then prints the links.
- `-app` does the same for every id the dependency mirror lacks; an object question with no mirror builds it for `-schema`, or the default schemas.
- A held application is refreshed first when the database says it changed since this checkout last refreshed it; otherwise no refresh runs.

Refreshing needs a connection. With none, or a database that does not answer, a store holding the application answers as it stands, naming any `-app` id it lacks under `WARNING - APP NOT LOADED:`. A question no store can answer stops on the shared `CONFIGURATION NOT FOUND` screen.

<br>

## How filters combine

Terms inside one flag are AND-matched, and different flags are AND-matched with each other, so `-commit 5+ -hash 565fcf1a` keeps only the commit that satisfies both. The exceptions are `-commit` and `-hash`, whose own multiple values are OR-matched.

`-commit` takes a number, a hash, or a range: `7` is that commit, `5+` is that one and everything newer, `2-6` is the inclusive span. A range needs digits on both sides, so a hash prefix is never misread as one.

<br>

## Finding an object rather than a file

`-type` and `-name` read the object out of the exported path rather than out of the file. The layout comes from your configured `path_objects`, so both the shipped `<schema>/database/<object_type>/` and the older `database/<schema>/<object_type>/` resolve.

- `-type` is spelled the way Oracle spells it: `-type "PACKAGE BODY"`, `-type "MATERIALIZED VIEW"`. Several values may be space-separated, comma-separated, or the flag repeated.
- `-name` is the object name, read through the file's own configured extension, so `packages/core.spec.sql` is `CORE` rather than `CORE.SPEC`.
- Both are matched as SQL LIKE patterns, case-insensitively, the way `export_db -type` and `-name` are matched. The pattern is anchored, so `-type PACKAGE` is the spec alone and `-type "PACKAGE%"` is the spec and the body; a partial name is written `-name "SHOP%"` rather than as a bare fragment.

<br>

## Restoring an old version

`-restore` writes each matching historical version over its original path, so `git diff` shows what it changes: `-file monthly_report_v -commit 7 -restore` puts commit 7's `monthly_report_v.sql` back where it lives. Restore is the one mode that reads live git, and only to fetch the payloads of versions already selected from the store.

Anything uncommitted is saved first, as one local commit named `WIP`, untracked files included. It is never pushed and nothing is staged. A clean checkout, or a restore that changes no file, gets no such commit. If git refuses it, the run prints `ERROR - GIT COMMIT FAILED:` with git's own message, writes nothing, and exits `1`.

When a restore matches more than one version of one file, the newest match wins, so name a specific `-commit` or `-hash` to put an older one back. A commit that deleted the file holds no version of it, so the newest match that still has the file is the one restored.

`WARNING - COULD NOT RESTORE:` reports one thing: a version git could not resolve, which is what a stale commit store looks like after history was rewritten. Run `adtai rebuild` and try again.

<br>

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `TERM` | No | none | Text search: the text to find in every layer, written before any flag. |
| `-layer`, `--layer` | Yes | all five | With `TERM`, the layers to search: `APEX`, `STATIC`, `DB`, `GIT` or `FILES`, case-insensitive, space- or comma-separated. |
| `-data`, `--data` | No | off | With `TERM`, search the rows of the tables, views, materialized views and synonyms instead of the five layers, narrowed by `-schema`, `-name` and `-limit`. See search_data.md. |
| `-from`, `--from` | No | none | Graph question: the objects an object depends on, or the pages a page written `APP.PAGE` links to. |
| `-to`, `--to` | No | none | Graph question: the objects that depend on an object, or the pages linking into a page written `APP.PAGE`. |
| `-impact`, `--impact` | No | none | Graph question: everything affected if the object changes, walked transitively. |
| `-constraint`, `--constraint` | No | none | Graph question: the foreign-key reference and dependency cascade around a named constraint. |
| `-app`, `--app` | Yes | none | Graph question: the database objects the APEX applications use, one table each. Ids, a list, or a range, `MIN-MAX` closed or `MIN+` open, resolved against the applications the mirror holds. With `TERM`, the applications `APEX` and `STATIC` search. |
| `-page`, `--page` | Yes | none | With `-app`, only the objects used on these page ids or ranges, or with `TERM` only the `APEX` hits on them. |
| `-format`, `--format` | No | `table` | Output of a graph answer: `table`, `yaml` or `md`. `yaml` and `md` keep stdout pure data. |
| `-branch`, `--branch` | No | current branch | Branch store to search, at the `repo_commits_file` path (default `config/commits/<branch>.db`). With `TERM`, the store `GIT` reads. |
| `-limit`, `--limit` | No | `20` | Maximum commits to print, newest first. `0` prints all matching commits. With `-data`, the rows each table returns. |
| `-files [N]`, `--files [N]` | No | auto with file selectors | Print changed-file rows. `-file`, `-type` or `-name` prints the first 20 per commit automatically; bare `-files` also prints 20; `-files 50` prints 50; `-files 0` prints none. Rows carry `A`, `M` or `D`. |
| `-summary`, `--summary` | No | none | Commit-subject terms; every word given must match. |
| `-file`, `--file` | No | none | Changed-file path terms; every word given must match. |
| `-type`, `--type` | Yes | none | Object type, resolved through your `object_types` config against the `path_objects` layout. Oracle's own spelling: `-type "PACKAGE BODY"`. A SQL LIKE pattern, so `-type "PACKAGE%"` takes both halves of the pair. Space-separated, comma-separated and repeated forms are equivalent. With `-app`, the type of an object the application uses. |
| `-name`, `--name` | Yes | none | Object name, read through the file's own configured extension, so `packages/core.spec.sql` is `CORE`. A SQL LIKE pattern like `-type`, and takes multiple values the same way. With `-app`, the name of an object the application uses. With `-data`, the tables to search. |
| `-by`, `--by` | Yes | none | Author email, as a SQL LIKE pattern: `-by bob@example.com` or `-by "bob%"`. Repeatable. |
| `-my`, `--my` | No | off | Keep commits whose author email equals `git config user.email`. |
| `-commit`, `-commits`, `--commit`, `--commits` | Yes | none | Commit numbers or hashes. `N` is that commit, `N+` is that one and newer, `N-M` is the inclusive span. Several values inside this flag are OR-matched. |
| `-hash`, `--hash` | Yes | none | Commit hash prefixes, OR-matched. Combined with `-commit`, both filters must match. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | none | Keep commits newer than today minus DAYS. DAYS may be a fraction of a day, `1/24` for the past hour. A whole-day window compares dates, so `-recent 1` keeps a commit made at 23:00 yesterday; a shorter one compares the commit's own timestamp. Bare `-recent` means one day. |
| `-since`, `--since` | No | none | Oldest commit date, `YYYY-MM-DD`, or a number of days back. |
| `-until`, `--until` | No | none | Newest commit date, `YYYY-MM-DD`, or a number of days back. |
| `-restore`, `--restore` | No | off | Write each matching historical version over its original path, the newest match per file, over a `WIP` commit of any uncommitted work. |

Shared options (-root, -schema, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
