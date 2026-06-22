# Export Database Objects (adtai export_db)

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
adtai export_db -schema CORE -schema APP
```

Schema values can also be comma-separated or use old ADT `%` patterns:

```bash
adtai export_db -schema APP,CORE%
```

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

`JOB` objects do not have reliable `last_ddl_time` metadata for old ADT-style recent exports. Export jobs separately without `-recent`:

```bash
adtai export_db -type JOB
```

Run without per-object output, useful when an LLM or agent drives the export and object names would flood its console:

```bash
adtai export_db -silent
```

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Output root folder. This can be any ordinary folder and does not need to be a Git repository. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes | environment default schema | Schema to export. Pass multiple times, use comma lists, or use `%` patterns such as `CORE%`. |
| `-type`, `--type` | Yes | configured object types | Object type pattern or patterns to export. Supports old ADT SQL-like `%` and `_` wildcards plus comma lists, for example `PACKAGE%,VIEW`. |
| `-name`, `--name` | Yes | all names | Object name pattern or patterns to export. Supports old ADT SQL-like `%` and `_` wildcards plus comma lists, for example `APP_%,TMP_%`. |
| `-recent`, `--recent` | No | all objects | Export objects changed in the last number of days. Do not combine with `-type JOB`. |
| `-dry-run`, `--dry-run` | No | off | Build the export plan without writing files. |
| `-delete`, `--delete` | No | off | Delete existing object files before export, excluding `DATA`. |
| `-silent`, `--silent` | No | off | Suppress per-object names and per-object progress callbacks while keeping the standard banner, connection block, overview, export header, and final timer. Use it when calling `export_db` from an LLM or agent to avoid flooding the console. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
