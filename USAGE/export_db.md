# Export Database Objects (adtai export_db)

`export_db` exports database objects — tables, views, packages, triggers, grants, and every other configured type — from an Oracle schema into one DDL file per object, laid out in a folder tree your project can commit to Git. It is the core "database as files" command: run it after making database changes and Git shows exactly what changed, per object. Filters narrow the export by type, name, schema, recency, or author; the DDL is normalized so repeated exports of an unchanged object are byte-identical.

Export from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_db
```

During export, ADT.ai prints the connection target, database/APEX version details when available, an object overview by type, and every object as it is exported. Press `Ctrl+C` to stop the export cleanly.

Generated DDL is normalized toward old ADT output where the contract is known: table constraints render as multi-line CHECK, PRIMARY KEY, and FOREIGN KEY blocks with `--` separators; table references and suffixes strip only the exported owner, preserve non-current schemas and FK `ON DELETE` actions, keep trailing `INMEMORY ...` clauses, remove generated `ENABLE` / `USING INDEX`, preserve explicit sequence `MAXVALUE` clauses, and cover old-ADT-aligned `INTERVAL` qualifiers, `XMLTYPE`, `NVARCHAR2`/other Oracle column datatype forms, and quoted schema-qualified object datatypes; simple views drop DBMS_METADATA header column lists and format quoted select-list items as lowercased one-column-per-line output, including `DISTINCT`, mixed quoted/unquoted simple columns, expression/function items, lowercase quoted identifiers, `WHERE` tails, `FROM` on the next line, CTE final `SELECT` lists after `WITH` blocks, compact unquoted simple select lists such as `select BUP_CODE,BUPT_CODE from ...` with one projection item per line, and aliased projections such as `v."COL"`, while preserving expression text and SQL layout from `FROM` onward; simple indexes use `CREATE INDEX IF NOT EXISTS` with schema-free lowercased table and column names, expression indexes unquote simple column references outside string literals, same-schema synonym targets are unqualified and unquoted, TYPE files start with the guarded old ADT `DROP TYPE` block, and TYPE BODY files start with the guarded old ADT `DROP TYPE BODY` block.

When a PRIMARY KEY or UNIQUE constraint was added after table creation and tied to a pre-existing index, Oracle exports it as a separate `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX <name>` plus a matching `CREATE [UNIQUE] INDEX <name>`. `export_db` folds that constraint back inline on the table — keeping the constraint name and columns, dropping the index name and the two trailing statements — so the table reads as if it had been created cleanly. When any such fold happens, the table's constraints are reordered PRIMARY KEY first, then UNIQUE, then FOREIGN KEY, then others; column lines keep their source order, and tables with no index-backed constraints keep their existing constraint order untouched. Each affected table also gets a `<table>.fix.sql` companion file beside it holding the `ALTER TABLE ... DROP CONSTRAINT` / `DROP INDEX` / `ALTER TABLE ... ADD CONSTRAINT` recovery script (one `--`-separated block per folded constraint); the companion is removed automatically when the table no longer has any index-backed constraint. Ordinary non-constraint trailing DDL such as plain `CREATE INDEX` is still preserved.

Export into any plain folder, with no Git repository required:

```bash
mkdir -p /private/tmp/adt-ai-export/CORE23
adtai export_db -root /private/tmp/adt-ai-export/CORE23
```

Preview the export without writing files:

```bash
adtai export_db -root /private/tmp/adt-ai-export/CORE23 -dry-run
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

A multi-schema run executes schema by schema — connect to CORE, export everything for it, print its own `TIMER`, then connect to APP and repeat — exactly as if you had run the command once per schema, with the `APEX DEPLOYMENT TOOL: EXPORT_DB` banner printed only once. See `USAGE.md` §Console Output Contract for the full shape.

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

Export only what changed since your last export of each schema (per-schema watermark in `config/recent.yaml`; a schema with no recorded export yet is exported in full and the watermark is seeded):

```bash
adtai export_db -recent
```

`JOB` objects do not have reliable `last_ddl_time` metadata for old ADT-style recent exports. Export jobs separately without `-recent`:

```bash
adtai export_db -type JOB
```

Export only objects last changed by a specific author, or by you, in a shared schema worked through proxy users. Both filters resolve authorship against the project's configured `audit:` source (a DDL-log table or view), so they need no DBA-level audit-trail access:

```bash
# objects last changed by the SCOTT proxy user
adtai export_db -by SCOTT

# objects last changed by you (db schema read from config/IDENTITY.yaml)
adtai export_db -my
```

`-by`/`-my` require an `audit:` block in `config.yaml` naming the log source and its columns:

```yaml
audit:
  source: APP_DDL_LOG     # a table or view of DDL changes
  object_name: object_name
  changed_by: changed_by
```

`-my` additionally reads your identity from the gitignored `config/IDENTITY.yaml` (see the Developer Identity section in [USAGE.md](../USAGE.md); there is no committed sample):

```yaml
db_schema: YOUR_SCHEMA
apex_account: FIRST.LAST
email: you@example.com
```

Run without per-object output, useful when an LLM or agent drives the export and object names would flood its console:

```bash
adtai export_db -silent
```

## Object groups

`-groups` is a **move action**, not an export modifier. When you pass it, `export_db` does **not** connect or export anything — it scans the object files you have already exported under `database/<object_type>/` and reorganizes the matching ones into per-group subfolders (`<object_type>/<group>/PREFIX_...`) so a large object-type folder stays navigable. Group folder names are always uppercased.

There are three forms:

1. **Auto-detect — bare `-groups`.** Clusters the flat (ungrouped) files in each object-type folder by their leading prefix. A cluster of at least `groups_min` files (config key, default `5`) becomes a group named after the detected prefix — first by the two-word prefix (`INV_BILLING`), then falling back to the one-word prefix (`INV`) for leftovers.
2. **Single prefix — `-groups INV_BILLING`.** Routes only the files whose name starts with that prefix; everything else stays where it is.
3. **Prefix list — `-groups INV_BILLING ORD, AP`.** Takes a space- and/or comma-separated list of prefixes and routes only those.

```bash
# Auto-detect groups using the default minimum cluster size (groups_min)
adtai export_db -groups

# Move only the INV_BILLING objects into an INV_BILLING/ subfolder
adtai export_db -groups INV_BILLING

# Move several prefix groups at once (space and/or comma separated)
adtai export_db -groups INV_BILLING ORD, AP
```

The action always **previews then confirms**: it prints every planned move as `source -> dest` plus any unmatched files, then asks you to proceed before moving anything. Answer `y` to apply, anything else to abort. Pass `-dry-run` to preview only — it never prompts and never moves.

Before moving, `export_db` enforces **per-object-type filename uniqueness**: if applying the plan would put the same object name in more than one place under a `<object_type>/` subtree (the root plus every `<group>/` subfolder), it reports the collisions and aborts without moving rather than overwriting or duplicating a file.

Hand-arranged subfolders still work on every plain `export_db` run: move some exported files into a `<object_type>/<group>/` subfolder by hand and the folder name becomes the group; on the next export ADT.ai learns the shared prefix of those files and routes new matching objects into the same subfolder automatically.

## Duplicate object files

Moving files by hand can leave the same filename in two places under one `<object_type>/` subtree — typically a stale copy in the type folder root plus the live one in a `<group>/` subfolder. `export_db` exports into whichever copy it finds first, so the other silently rots.

The export does **not** abort on this. It runs to completion and marks the affected object on its own row, replacing the plain object name with one row per location:

```text
               TABLE | INV_BILLING_HEADER | core/tables/billing/inv_billing_header.sql [DUPE]
                     | INV_BILLING_HEADER | core/tables/inv_billing_header.sql [DUPE]
```

Paths are shown relative to the export root with the leading `database/` folder dropped, so the row names the schema, the group subfolder, and the file. Delete the copies you do not want and re-run; the marker disappears once one location is left. Objects with a single file are printed exactly as before.

The scan is per schema subtree and case-insensitive, and `.fix.sql` sidecars never count as duplicates. The same object name exported from two schemas is not a collision — each schema owns its own subtree — but a collision present in several schemas is marked in every one of them.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Output root folder. This can be any ordinary folder and does not need to be a Git repository. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes | environment default schema | Schema(s) to export, one pass each. Pass multiple times, space-separate (`-schema DA GSN`), use comma lists, or use `%` patterns such as `CORE%`. |
| `-type`, `--type` | Yes | configured object types | Object type pattern or patterns to export. Supports old ADT SQL-like `%` and `_` wildcards plus comma lists, for example `PACKAGE%,VIEW`. Oracle type names, resolved exactly as on `recompile`: a bare `PACKAGE` exports specifications only, `PACKAGE BODY` (quoted or not) bodies only, `PACKAGE SPEC` the specification, and `MVIEW`/`MATERIALIZED` both mean `MATERIALIZED VIEW`. See [recompile → Object types](recompile.md#object-types). |
| `-name`, `--name` | Yes | all names | Object name pattern or patterns to export. Supports old ADT SQL-like `%` and `_` wildcards plus comma lists, for example `APP_%,TMP_%`. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | all objects | Export objects changed in the last DAYS days. Bare `-recent` exports everything changed since that schema's last successful covering export — the per-schema watermark in `config/recent.yaml` — shown as `CHANGED SINCE <timestamp> (LAST EXPORT)`; a schema with no watermark yet is exported in full and seeded, with a visible `RECENT: no previous export recorded` note. Narrowed runs (`-name`/`-type`/`-by`/`-my`) and `-dry-run` never advance the watermark. Do not combine with `-type JOB`. |
| `-by`, `--by` | No | all authors | Export only objects last changed by `AUTHOR` (a db user/schema), resolved by joining the export set against the project's configured `audit:` source. Lets a shared schema worked by several developers via proxy users still resolve authorship. Requires an `audit:` block (`source`/`object_name`/`changed_by`) in `config.yaml`. |
| `-my`, `--my` | No | off | Export only objects last changed by the current user, taking the db schema from the gitignored `config/IDENTITY.yaml` (`db_schema`). Same audit resolution as `-by`; requires both the `audit:` block and `config/IDENTITY.yaml`. |
| `-groups`, `--groups` | No (move action) | off | Move action: reorganize already-exported files into `<object_type>/<group>/` subfolders. Never connects or exports. Bare `-groups` auto-detects groups by prefix (cluster ≥ `groups_min`, default `5`); `-groups PREFIX ...` takes a space- and/or comma-separated prefix list and moves only those. Group folder names are uppercased. Previews then prompts for confirmation; with `-dry-run` it previews only. Aborts on per-object-type filename collisions. |
| `-dry-run`, `--dry-run` | No | off | Build the export plan without writing files. |
| `-delete`, `--delete` | No | off | Delete existing object files before export, excluding `DATA`. |
| `-silent`, `--silent` | No | off | Suppress per-object names and per-object progress callbacks while keeping the standard banner, connection block, overview, export header, and final timer. Use it when calling `export_db` from an LLM or agent to avoid flooding the console. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
