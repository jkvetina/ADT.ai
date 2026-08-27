# Export Table Data (adtai export_data)

`export_data` exports the *rows* of selected tables as CSV, plus a MERGE script that replays them into any environment. Reach for it when reference data, settings, lookup lists or seed rows should live in git beside the DDL rather than in somebody's memory. It complements [`export_db`](export_db.md), which exports object definitions and never data.

<br>

## Examples

Export one table by name:

```bash
adtai export_data -name ADT_FIXTURE_DDL_LOG
```

Export by pattern, or everything:

```bash
adtai export_data -name ADT_%
adtai export_data -name %
```

Run it quietly, keeping the chrome and dropping the per-table rows:

```bash
adtai export_data -name ADT_% -silent
```

Refresh what you already have. With no `-name`, only tables that already carry a DATA `.sql` file are exported:

```bash
adtai export_data
```

<br>

## Output

One row per table, its label printed before the fetch starts and completed with the row count when the fetch returns:

```text
APEX DEPLOYMENT TOOL - EXPORT_DATA
----------------------------------


CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.1.0.0 | FREEPDB1


EXPORTING 4 TABLES:
-------------------
  ADT_ANNO_DOMAIN_USE ...................................................... 0
  ADT_ANNO_TICKET .......................................................... 0
  ADT_ANNO_TICKET_MV ....................................................... 0
  ADT_FIXTURE_DDL_LOG ..................................................... 12


TIMER: 0s
```

- The number is rows written, so a `0` is an empty table rather than a failure. The CSV is still written, with its header row.
- `-silent` drops these rows and keeps the banner, connection block, header and timer.
- A run selecting nothing prints `EXPORTING 0 TABLES:` and its header, then stops.
- A multi-schema run executes schema by schema, each with its own connection block and its own `TIMER`, and prints the banner once.

<br>

## What lands on disk

Files go under the `DATA` folder of your configured `path_objects` layout, one CSV per table, named after the table in lower case:

```text
sandbox/database/data/adt_anno_ticket.csv
sandbox/database/data/adt_fixture_ddl_log.csv
```

Beside each CSV, `<table>.sql` holds the generated MERGE. **It is written only when the table has key columns to match rows on**, a primary key first and a unique constraint otherwise. A table with neither gets its CSV alone, because a MERGE with nothing to join on would have no way to tell an update from an insert.

Each MERGE statement covers at most `merge_batch_size` rows (project `config.yaml`, default `10000`); a larger export becomes consecutive MERGE statements in the same file.

<br>

## Large column types become sidecars

BLOB, CLOB, XMLTYPE and JSON columns are never dropped and never squeezed into a CSV cell. Each value is written as its own file in a folder named after the table, as `<row-key>.<column>.<ext>`, with the extension the type calls for:

| Column type | Sidecar extension |
| ----------- | ----------------- |
| BLOB | `.bin` |
| CLOB | `.txt` |
| XMLTYPE | `.xml` |
| JSON | `.json` |

A null or empty value writes no file. Every non-empty payload also gets a SQL-only import script beside it, `<key>.<column>.sql`, which stores the value as base64 and decodes it in Oracle, so a payload imports with no Python helper in the loop. The table MERGE prints `PROMPT <filename>` before calling each one with `@"./<filename>";`.

`export_data` writes no `.gitignore` of its own. `adtai doctor -init` puts `**/data/*/*.sql` in the project root's, which keeps the generated payload scripts ignored while the table MERGE stays trackable.

**That pattern is depth-based and was written before groups existed.** A grouped table's own `data/<GROUP>/<table>.sql` sits at the same one-directory depth the pattern ignores. Grouping a table that also has sidecar columns needs its own gitignore exception, `!data/<GROUP>/<table>.sql`, until this is revisited.

<br>

## Groups

Table exports reorganize into `data/<GROUP>/` subfolders the same way `export_db -groups` does: `-groups` previews the moves and makes none, `-force` applies them. A table's CSV, its `.sql` MERGE (when it has one), and its sidecar folder (when it has one) always move together, so a grouped table never strands the BLOB/CLOB payloads beside it.

```bash
adtai export_data -groups INV_
adtai export_data -groups INV_ -force
adtai export_data -groups INV_ INV_ARCHIVE -force INVOICING
```

Bare `-groups` auto-detects groups by prefix across the flat CSVs already on disk, the same `groups_min` config threshold `export_db` reads. Once a table is grouped, later `export_data` runs keep writing it there: the group is re-learned from the folder it is already sitting in, never remembered separately.

<br>

## Narrowing rows and shaping the MERGE

Row filters come from config rather than the command line. Each one names a column and the predicate to apply to it, and it applies only when the table being exported actually has that column:

```yaml
tables_global:
  where:
    app_id: '> 0'
tables:
  APP_SETTINGS:
    where:
      lov_name: "LIKE 'APP_%'"
```

A per-table block replaces the global value for that table rather than adding to it.

Which sections the generated MERGE carries is configured the same way, globally and per table:

```yaml
tables_global:
  merge:
    delete: false
    insert: true
    update: true
tables:
  APP_SETTINGS:
    merge:
      delete: true
      insert: false
      update: false
```

The `export:` block in the connection file applies here too, so a pattern in `ignore:` keeps a table out of `export_data` and [`export_db`](export_db.md#permanently-excluding-objects) alike.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-name`, `--name` | Yes | tables with an existing DATA file | Table name pattern or patterns to export, with SQL-like `%` and `_` wildcards, resolved by table discovery in the database. `\` escapes a literal `_` or `%`, quoted: `-name 'APP\_SETTINGS'`. |
| `-silent`, `--silent` | No | off | Suppress the per-table rows, keeping the banner, connection block, header and timer. |
| `-groups`, `--groups` | No | off | Move action: reorganize already-exported table files into `data/<group>/` subfolders. Never connects or exports, and moves nothing until `-force`. See [Groups](#groups) above. |
| `-force [GROUP]`, `--force [GROUP]` | No | off | With `-groups`, apply the listed moves. `-force GROUP` lands every prefix named in one uppercased folder instead of one per prefix, so it needs named prefixes. Without `-groups` it is an error, exit `2`. |

Shared options (-root, -env, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
