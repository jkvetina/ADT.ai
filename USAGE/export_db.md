# Export Database Objects (adtai export_db)

Export from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_db
```

During export, ADT.ai prints the connection target, database/APEX version details when available, an object overview by type, and every object as it is exported. Press `Ctrl+C` to stop the export cleanly.

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
| `-h`, `--help` | No | off | Show command help. |

---

← [USAGE.md](../USAGE.md) index
