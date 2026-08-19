# Export Database Objects (adtai export_db)

`export_db` exports database objects, tables, views, packages, triggers, grants, and every other configured type, from an Oracle schema into one DDL file per object, laid out in a folder tree your project can commit to Git. It is the core "database as files" command: run it after making database changes and Git shows exactly what changed, per object. Filters narrow the export by type, name, schema, recency, or author; the DDL is normalized so repeated exports of an unchanged object are byte-identical.

Export from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_db
```

During export, ADT.ai prints the connection target, database/APEX version details when available, an object overview by type, and every object as it is exported. Press `Ctrl+C` to stop the export cleanly.

Generated DDL is normalized toward old ADT output where the contract is known: table constraints render as multi-line CHECK, PRIMARY KEY, and FOREIGN KEY blocks with `--` separators, and a CHECK condition keeps its own line breaks and comments (see below); table references and suffixes strip only the exported owner, preserve non-current schemas and FK `ON DELETE` actions, keep trailing `INMEMORY ...` clauses, remove generated `ENABLE` / `USING INDEX`, preserve explicit sequence `MAXVALUE` clauses, and cover old-ADT-aligned `INTERVAL` qualifiers, `XMLTYPE`, `NVARCHAR2`/other Oracle column datatype forms, and quoted schema-qualified object datatypes; simple views drop DBMS_METADATA header column lists and format quoted select-list items as lowercased one-column-per-line output, including `DISTINCT`, mixed quoted/unquoted simple columns, expression/function items, lowercase quoted identifiers, `WHERE` tails, `FROM` on the next line, CTE final `SELECT` lists after `WITH` blocks, compact unquoted simple select lists such as `select BUP_CODE,BUPT_CODE from ...` with one projection item per line, and aliased projections such as `v."COL"`, while preserving expression text and SQL layout from `FROM` onward; simple indexes use `CREATE INDEX IF NOT EXISTS` with schema-free lowercased table and column names, expression indexes unquote simple column references outside string literals, same-schema synonym targets are unqualified and unquoted, TYPE files start with the guarded old ADT `DROP TYPE` block, and TYPE BODY files start with the guarded old ADT `DROP TYPE BODY` block; trigger headers put `FOR EACH ROW` on a line of its own, indented like the line it was split from, with a trailing `WHEN (...)`, `DECLARE`, or `BEGIN` moved to the next line, while the trigger body is preserved verbatim, so the same words in a header comment, in a string literal, or anywhere after `DECLARE`/`BEGIN` are left untouched.

DBMS_METADATA reports a trigger's status as an `ALTER TRIGGER` statement appended *inside* the `CREATE TRIGGER` block. `export_db` drops the `ENABLE` form (it is the default state) and moves the `DISABLE` form below the block's `/` terminator, verbatim and with a blank line on each side, so a disabled trigger exports as a runnable file:

```sql
CREATE OR REPLACE TRIGGER trg_d
    BEFORE INSERT ON tab
    FOR EACH ROW
BEGIN
    NULL;
END;
/

ALTER TRIGGER "DA"."TRG_D" DISABLE;
```

Left inside the block, as it arrives from the database, the whole file is one statement and raises `PLS-00103`. Only a real statement line is matched, so a body that logs those words inside a string literal is untouched.

A CHECK constraint's condition is stored verbatim by Oracle, so a domain list written over several lines, with `--` or `/* */` comments documenting the values, arrives that way and is exported that way. The lines inside the parentheses keep their relative shape, re-hung under the `CHECK (` block; a condition that was one line stays one line. Blank lines are the one exception and are dropped, since an empty line inside a statement ends the buffer in a plain SQLcl session:

```sql
    CONSTRAINT doc_chk_type
        CHECK (
            doc_type in (
                -- customer facing
                'INVOICE',
                'CREDIT_NOTE', -- issued when an invoice is cancelled
                'MEMO'
            )
        )
```

Comments are read as comments everywhere the DDL is scanned, so an apostrophe (`-- don't reorder`) or a parenthesis (`-- (see spec)`) inside one is text rather than SQL. Views follow the same principle from the other direction: a select list carrying a comment is left exactly as the database returned it instead of being reformatted one column per line, because joining those lines would move the following columns, and `from`, behind the comment. A comment after `from` does not affect the select-list formatting.

When a PRIMARY KEY or UNIQUE constraint was added after table creation and tied to a pre-existing index, Oracle exports it as a separate `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX <name>` plus a matching `CREATE [UNIQUE] INDEX <name>`. `export_db` folds that constraint back inline on the table, keeping the constraint name and columns, dropping the index name and the two trailing statements, so the table reads as if it had been created cleanly. When any such fold happens, the table's constraints are reordered PRIMARY KEY first, then UNIQUE, then FOREIGN KEY, then others; column lines keep their source order, and tables with no index-backed constraints keep their existing constraint order untouched. Each affected table also gets a `<table>.fix.sql` companion file beside it holding the `ALTER TABLE ... DROP CONSTRAINT` / `DROP INDEX` / `ALTER TABLE ... ADD CONSTRAINT` recovery script (one `--`-separated block per folded constraint); the companion is removed automatically when the table no longer has any index-backed constraint. Ordinary non-constraint trailing DDL such as plain `CREATE INDEX` is still preserved.

Export into any plain folder, with no Git repository required:

```bash
mkdir -p /private/tmp/adt-ai-export/CORE23
adtai export_db -root /private/tmp/adt-ai-export/CORE23
```

Clean existing object files before export, while keeping `DATA` files:

```bash
adtai export_db -delete
```

Use a project config folder:

```bash
adtai export_db -root ~/Dropbox/PROJECTS/CORE23 -config-dir ~/Dropbox/PROJECTS/CORE23/config
```

Use a specific environment:

```bash
adtai export_db -env DEV
```

Export one or more schemas:

```bash
adtai export_db -schema CORE APP
adtai export_db -schema CORE -schema APP
```

Schema values can also be comma-separated or use old ADT `%` patterns:

```bash
adtai export_db -schema APP,CORE%
```

A multi-schema run executes schema by schema, connect to CORE, export everything for it, print its own `TIMER`, then connect to APP and repeat, exactly as if you had run the command once per schema, with the `APEX DEPLOYMENT TOOL - EXPORT_DB` banner printed only once. See `docs/README.md` §Console Output Contract for the full shape.

Export only objects matching old ADT-style name patterns:

```bash
adtai export_db -name APP_% TMP_%
```

Export only matching object types:

```bash
adtai export_db -type PACKAGE% VIEW%
```

Export objects changed in the last 7 days:

```bash
adtai export_db -recent 7
```

Export objects changed in the past hour, or the past 5 minutes. Oracle counts a DATE in days, so a fraction of a day is a shorter window: the query asks for `SYSDATE - 1/24` and `SYSDATE - 5/1440`:

```bash
adtai export_db -recent 1/24
```

```bash
adtai export_db -recent 5/1440
```

Export only what changed since your last export of each schema (per-schema watermark in `config/internal/recent.yaml`; a schema with no recorded export yet is exported in full and the watermark is seeded):

```bash
adtai export_db -recent
```

`JOB` objects do not have reliable `last_ddl_time` metadata for old ADT-style recent exports. Export jobs separately without `-recent`:

```bash
adtai export_db -type JOB
```

Export only the objects a specific author, or you, has worked on in a shared schema worked through proxy users. Both filters resolve authorship against the project's configured `audit:` source (a DDL-log table or view), so they need no DBA-level audit-trail access:

```bash
# objects the SCOTT proxy user has changed
adtai export_db -by SCOTT

# objects you have changed (db schema read from config/IDENTITY.yaml)
adtai export_db -my
```

`-by`/`-my` require an `audit:` block in `config.yaml` naming the log source and its columns. `changed_at` is optional but strongly recommended, the next section covers what it buys:

```yaml
audit:
  source: APP_DDL_LOG     # a table or view of DDL changes
  object_name: object_name
  changed_by: changed_by
  changed_at: changed_at  # optional; the DDL timestamp
```

### What the changed_at key adds

Without `changed_at` a DDL log has no ordering, so the only question that can be asked of it is *who has ever touched this object*. `-by SCOTT` then matches an object SCOTT edited once last year and a colleague rewrote yesterday, with nothing to tell the two apart.

With `changed_at` configured, two things change and neither adds a flag:

- **Objects someone else changed after you are marked.** They stay in the export, dropping them would silently lose work you really did, and carry the later author in square brackets, the same shape as `[DUPE]`:

	```text
	             PACKAGE | APP_ORDER_PKG [SCOTT]
	           PROCEDURE | APP_SEND_MAIL
	```

	`APP_ORDER_PKG` is yours, but SCOTT changed it last; `APP_SEND_MAIL` is yours and still yours.

- **`-recent` reaches the audit source too.** `-recent N` alone filters `user_objects.last_ddl_time`, which knows *when* an object changed but not *who* changed it, so `-my -recent 1` used to mean "changed within a day AND I once touched it", two unrelated sources silently ANDed. With `changed_at` the same window is applied to the DDL log, so it means what it reads like: changed within a day, by me. The bare `-recent` watermark is applied the same way.

### Populating the log, and why proxy users are not free

Oracle records **no** actor for a DDL change. `USER_OBJECTS` carries `LAST_DDL_TIME` and nothing that names a user, so authorship exists only if the project writes it down at DDL time, normally from an `AFTER CREATE OR ALTER OR DROP ON SCHEMA` trigger.

In a proxy session (`sqlplus scott[app_owner]/...`) the expressions such a trigger would naturally use all return the **proxied schema**, never the proxy user:

| expression | direct session | proxy session |
| --- | --- | --- |
| `USER` | `APP_OWNER` | `APP_OWNER` |
| `ORA_LOGIN_USER` | `APP_OWNER` | `APP_OWNER` |
| `SYS_CONTEXT('USERENV', 'SESSION_USER')` | `APP_OWNER` | `APP_OWNER` |
| `SYS_CONTEXT('USERENV', 'PROXY_USER')` | *(null)* | `SCOTT` |
| `SYS_CONTEXT('USERENV', 'AUTHENTICATION_METHOD')` | `PASSWORD` | `PASSWORD_PROXY` |

So a trigger recording `USER` files every developer's work under the shared schema name, and `-by SCOTT` matches nothing, not because the filter is broken, but because the identity was never written. ADT.ai cannot recover it afterwards. Record the proxy explicitly:

```sql
NVL(SYS_CONTEXT('USERENV', 'PROXY_USER'), USER)
```

If your developers connect directly rather than through a proxy, ADT.ai already publishes their identity for you: every connection runs `DBMS_SESSION.SET_IDENTIFIER(db_schema)` from `config/IDENTITY.yaml`, so a trigger can read `SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER')`. A log that covers both cases records:

```sql
COALESCE(
    SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER'),
    SYS_CONTEXT('USERENV', 'PROXY_USER'),
    USER
)
```

A runnable fixture that installs this trigger and demonstrates all three behaviours ships in `TEST_SCENARIOS/fixtures/`, see `TEST_SCENARIOS/export_db.md` §Author filters.

`-my` additionally reads your identity from the gitignored `config/IDENTITY.yaml` (see the Developer Identity section in [docs/README.md](README.md); there is no committed sample):

```yaml
db_schema: YOUR_SCHEMA
apex_account: FIRST.LAST
email: you@example.com
```

## Permanently excluding objects

`-name` and `-type` narrow a single run. To keep a set of objects out of **every** export, put the pattern in the schema's `export:` block in the connection file, it is a standing filter, not a runtime flag:

```yaml
DEV:
  schemas:
    DA:
      export:
        ignore: 'REST_SAP_INCOMING_RETRY%,TMP_%'   # SQL LIKE, comma-separated
        prefix: ''                                  # inverse: export only matching names
```

Set it with the `connection` command rather than by hand:

```bash
adtai connection -create -env DEV -schema DA -ignore 'REST_SAP_INCOMING_RETRY%' -go
```

The patterns are matched by the discovery query, so ignored objects are never listed, never exported, and, because a config filter is not a runtime filter, count as *missing* on the next full run, so `auto_delete` removes the files a previous export already wrote. That is what makes this the right tool for runtime-generated objects: an application that creates one scheduler job per request (`REST_SAP_INCOMING_RETRY_1000169671`, `..._1000206680`, …) otherwise adds one file per job to the repo forever. `export_data` reads the same `export.ignore` block.

Run without per-object output, useful when an LLM or agent drives the export and object names would flood its console:

```bash
adtai export_db -silent
```

## Watching a long export

The default screen prints a row per exported object, which is what you want while you are watching a handful of them. On a whole schema it is hundreds of rows, and the `OBJECTS OVERVIEW:` table that opened the run has left the terminal's scrollback long before the export ends.

`-compact` keeps the overview and replaces the rows with one line that moves:

```bash
adtai export_db -compact
```

```text
OBJECTS OVERVIEW:
-----------------

  OBJECT TYPE          COUNT
  -------------------  -----
  PACKAGE                 34
  TABLE                   19
  VIEW                     8
  GRANT

EXPORTING 61 OBJECTS:
--------------------
  PACKAGES .................................... 43%  0:00:07
```

**The row names the type in the plural**, `PACKAGES`, `MATERIALIZED VIEWS`, `PACKAGE BODIES`, `INDEXES`, because it heads the whole batch of them rather than naming the one object in flight. Only the label reads that way: `-type` still takes Oracle's own singular spelling, the `OBJECTS OVERVIEW:` table above lists singular values under its `OBJECT TYPE` heading, and the exported files land in the folders that spelling maps to.

The bar advances when an object's DDL comes back, not on a clock, and the time on the right is what is left rather than what has passed. A multi-schema export draws one bar per schema, inside that schema's own section, and a failed DDL pull completes the row with `FAILED` before the error is reported.

**`GRANT` is in the overview and not in the header count**, and the difference is deliberate (`#382`). The four grant artifacts, grants made, grants received, user privileges and directories, are exported under the `GRANT` object type like any other file, so the type belongs in the listing; but they are not schema objects and have no `USER_OBJECTS` row, so counting them would make `EXPORTING <n> OBJECTS:` disagree with the dictionary. Its count is blank for the same reason it is honest: `grants_received` writes one file per owner, and how many that is cannot be known until the reads run, which happens after the table prints.

They run at the end of a schema's export, after the last object, and **`-compact` is where you see them**: the bar takes `GRANTS` as its label while they read, and counts them as one more unit, so the row cannot reach 100% while they are still in flight. The default row-per-object screen lists nothing for them at all (Jan's call, 2026-08-16), which is why the overview row is where that mode says the type exists. A run narrowed past them, `-type VIEW`, names the type in neither place.

**The countdown is seeded by what your last export of that schema cost.** Every run, `-compact` or not, records how long an object of each type took, per environment and schema, in `config/internal/recent.yaml` beside the `-recent` watermarks. The next run prices itself from those rates for the objects it actually selected, so the figure opens on something real instead of swinging while the run collects its first samples, and the rate this run is measuring is blended in as it proceeds. The unit is per object **type** on purpose: a sequence costs a fiftieth of what a table with constraint blocks costs, so one average across types would be wrong for both, and a `-type SEQUENCE` run would otherwise teach the store something false about a full export.

A first export of a schema, or a run with no environment to key one by, has no history: the row reads `0:00:00` until the first object returns and then projects from the measured rate alone. Deleting `config/internal/recent.yaml` resets the rates and the watermarks together.

`-silent` outranks `-compact`: it removes the very rows the bar stands in for, so passing both gives you the quieter of the two.

## Object groups

`-groups` is a **move action**, not an export modifier. When you pass it, `export_db` does **not** connect or export anything, it scans the object files you have already exported under `database/<object_type>/` and works out which ones belong in per-group subfolders (`<object_type>/<group>/PREFIX_...`) so a large object-type folder stays navigable. Group folder names are always uppercased.

**It lists, it does not move.** `-groups` on its own is a report you read; `-force` beside it is what applies the listing. Nothing on disk changes until you pass it.

There are three forms:

1. **Auto-detect, bare `-groups`.** Clusters the flat (ungrouped) files in each object-type folder by their leading prefix. A cluster of at least `groups_min` files (config key, default `5`) becomes a group named after the detected prefix, first by the two-word prefix (`INV_BILLING`), then falling back to the one-word prefix (`INV`) for leftovers.
2. **Single prefix, `-groups INV_BILLING`.** Routes only the files whose name starts with that prefix; everything else stays where it is.
3. **Prefix list, `-groups INV_BILLING ORD, AP`.** Takes a space- and/or comma-separated list of prefixes and routes only those.

Naming the prefixes also narrows what the listing prints. Bare `-groups` proposes a layout for the whole export, so the files it decided to leave flat are part of the proposal and it lists them under `UNMATCHED (LEFT IN PLACE):`. `-groups INV_BILLING` asks about INV_BILLING, so the listing shows that group and nothing else.

```bash
# List the groups auto-detected at the default minimum cluster size (groups_min)
adtai export_db -groups

# List what would move into an INV_BILLING/ subfolder
adtai export_db -groups INV_BILLING

# Apply it: move the INV_BILLING objects, leave every other group alone
adtai export_db -groups INV_BILLING -force

# Several prefix groups at once (space and/or comma separated)
adtai export_db -groups INV_BILLING ORD, AP -force
```

The listing is a `PLANNED MOVES:` section, one line per target group with its files under it, each file shown as the folder it lives in plus its own name:

```text
PLANNED MOVES:
--------------

  AP
    - tables/ap_invoice.sql
    - tables/ap_payment.sql

  INV_BILLING
    - tables/inv_billing_header.sql
    - views/inv_billing_summary.sql
```

Groups sort A to Z and so do the files inside each one. A group gathers what it takes from every object type, which is why `tables/` and `views/` rows sit side by side under one name.

Add `-force` and the same run moves those files and reports `Moved <n> file(s).` **`-force` moves only what the listing showed**, so `-groups INV_BILLING -force` cannot touch, flatten or rename a group you did not name, including one you arranged by hand. `-force` without `-groups` is an error rather than a flag that quietly does nothing.

Before moving, `export_db` enforces **per-object-type filename uniqueness**: if applying the plan would put the same object name in more than one place under a `<object_type>/` subtree (the root plus every `<group>/` subfolder), it reports the collisions and aborts without moving rather than overwriting or duplicating a file.

Hand-arranged subfolders still work on every plain `export_db` run: move some exported files into a `<object_type>/<group>/` subfolder by hand and the folder name becomes the group; on the next export ADT.ai learns the shared prefix of those files and routes new matching objects into the same subfolder automatically.

## Duplicate object files

Moving files by hand can leave the same filename in two places under one `<object_type>/` subtree, typically a stale copy in the type folder root plus the live one in a `<group>/` subfolder. `export_db` exports into whichever copy it finds first, so the other silently rots.

The export does **not** abort on this. It runs to completion and marks the affected object on its own row, replacing the plain object name with one row per location:

```text
               TABLE | INV_BILLING_HEADER | core/tables/billing/inv_billing_header.sql [DUPE]
                     | INV_BILLING_HEADER | core/tables/inv_billing_header.sql [DUPE]
```

Paths are shown relative to the export root with the leading `database/` folder dropped, so the row names the schema, the group subfolder, and the file. Delete the copies you do not want and re-run; the marker disappears once one location is left. Objects with a single file are printed exactly as before.

The scan is per schema subtree and case-insensitive, and `.fix.sql` sidecars never count as duplicates. The same object name exported from two schemas is not a collision, each schema owns its own subtree, but a collision present in several schemas is marked in every one of them.

## Where files land (path_objects)

`path_objects` in `config.yaml` is the export path **template**, not a literal folder. It resolves exactly two placeholders:

| Placeholder | Resolves to |
| ----------- | ----------- |
| `<schema>` | The schema/owner name, lowercased. |
| `<object_type>` | The per-type folder from `object_types` (`views/`, `packages/`, …). Appended automatically when the template omits it, which is the legacy `'database/'` layout. |

Old ADT's `{$NAME}` substitution syntax means nothing here. A template such as `'{$INFO_SCHEMA}/database/'`, the value old ADT ships as a commented example, so it is the natural copy-paste when migrating a project, is **rejected**:

```text
CONFIGURATION INVALID
---------------------
Unresolved placeholder in config path_objects: {$INFO_SCHEMA}
  Value: {$INFO_SCHEMA}/database/
  ADT.ai substitutes only <schema> and <object_type> in path_objects; '{$NAME}' is old ADT syntax and would be written out as a literal folder name.
  Fix path_objects in config.yaml (e.g. '<schema>/database/<object_type>/').
```

The run stops before writing anything, and the same rejection applies to every command that renders the template, `export_db`, `export_data`, and `patch -install`, so a guarded command cannot leave an unguarded one exporting into the placeholder folder. Until this guard the export simply created a directory named `{$INFO_SCHEMA}` and reported success.

If an earlier run already built such a folder, it is left on disk untouched: delete it yourself once you have confirmed nothing you need is only in there.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Output root folder. This can be any ordinary folder and does not need to be a Git repository. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes | environment default schema | Schema(s) to export, one pass each. Pass multiple times, space-separate (`-schema DA GSN`), use comma lists, or use `%` patterns such as `CORE%`. |
| `-type`, `--type` | Yes | configured object types | Object type pattern or patterns to export. Supports old ADT SQL-like `%` and `_` wildcards plus comma lists, for example `PACKAGE%,VIEW`. Oracle type names, resolved exactly as on `recompile`: a bare `PACKAGE` exports specifications only, `PACKAGE BODY` (quoted or not) bodies only, `PACKAGE SPEC` the specification, and `MVIEW`/`MATERIALIZED` both mean `MATERIALIZED VIEW`. See [recompile → Object types](recompile.md#object-types). |
| `-name`, `--name` | Yes | all names | Object name pattern or patterns to export. Supports old ADT SQL-like `%` and `_` wildcards plus comma lists, for example `APP_%,TMP_%`. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | all objects | Export objects changed in the last DAYS days. DAYS may be a fraction of a day for a shorter window, `1/24` for the past hour and `5/1440` for the past 5 minutes: the window reaches the query as `SYSDATE - DAYS`, and Oracle counts a DATE in days. A whole-day window keeps its `CHANGED SINCE <date>` header; a shorter one reports the instant it starts at, read off the database clock rather than yours, so the header names the cutoff the query actually used even when the server keeps a different timezone. Bare `-recent` exports everything changed since that schema's last successful covering export, the per-schema watermark in `config/internal/recent.yaml`, shown as `CHANGED SINCE LAST EXPORT AT <timestamp>`; a schema with no watermark yet is exported in full and seeded, with a visible `NO PREVIOUS EXPORT RECORDED:` note. Narrowed runs (`-name`/`-type`/`-by`/`-my`) never advance the watermark. Do not combine with `-type JOB`. |
| `-by`, `--by` | No | all authors | Export only objects `AUTHOR` (a db user/schema) has changed, resolved by joining the export set against the project's configured `audit:` source. Lets a shared schema worked by several developers via proxy users still resolve authorship. Requires an `audit:` block (`source`/`object_name`/`changed_by`) in `config.yaml`. With the optional `audit.changed_at` column configured, an object someone else changed *after* the author is marked `[OTHER_AUTHOR]` on its export row, and `-recent` narrows the audit source as well as `user_objects`. |
| `-my`, `--my` | No | off | Export only objects the current user has changed, taking the db schema from the gitignored `config/IDENTITY.yaml` (`db_schema`). Same audit resolution as `-by`; requires both the `audit:` block and `config/IDENTITY.yaml`. |
| `-groups`, `--groups` | No | off | Move action: reorganize already-exported files into `<object_type>/<group>/` subfolders. Never connects or exports. Bare `-groups` auto-detects groups by prefix (cluster ≥ `groups_min`, default `5`) and reports what it left flat; `-groups PREFIX ...` takes a space- and/or comma-separated prefix list and lists only those. Group folder names are uppercased. Prints the listing under `PLANNED MOVES:` and moves nothing; `-force` is what applies it. Aborts on per-object-type filename collisions. |
| `-force`, `--force` | No | off | With `-groups`, apply the listed moves instead of only listing them. Moves exactly what the listing showed, so a group you did not name is never touched. Without `-groups` it is an error, exit `2`. |
| `-delete`, `--delete` | No | off | Delete existing object files before export, excluding `DATA`. |
| `-silent`, `--silent` | No | off | Suppress per-object names and per-object progress callbacks while keeping the standard banner, connection block, overview, export header, and final timer. Use it when calling `export_db` from an LLM or agent to avoid flooding the console. |
| `-compact`, `--compact` | No | off | Replace the per-object rows with one dotted progress bar per schema, drawn under the export header, labelled with the plural of the object type being pulled right now (`PACKAGES`, `MATERIALIZED VIEWS`), and advanced as each object's DDL comes back. Everything else, banner, connection block, overview, deleted-object and duplicate reporting, and the timer, is unchanged. `-silent` outranks it. See [Watching a long export](#watching-a-long-export). |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [docs/README.md](README.md) index
