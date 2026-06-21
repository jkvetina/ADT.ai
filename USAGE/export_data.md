# Export Table Data (adtai export_data)

Export named table data from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_data -name APP_SETTINGS
```

When `-name` is omitted, ADT.ai follows old ADT behavior and updates only tables that already have DATA `.sql` files in the configured DATA folder. Use explicit `-name %` to export all matching tables. The current implementation writes CSV files and generated MERGE SQL files. BLOB, CLOB, XMLTYPE, and JSON columns are exported as sidecar files in a table-named folder beside the CSV, for example `eba_stds_standards/<primary-key>.implementation.txt`; extensions are `.bin`, `.txt`, `.xml`, and `.json` respectively. Null or empty sidecar values do not create files. Each non-empty sidecar payload also gets a SQL-only import script named `eba_stds_standards/<primary-key>.<column>.sql`; the table MERGE script prints `PROMPT <filename>` before calling each payload script with `@"./<filename>";`. The payload SQL scripts store the value as base64 and decode it in Oracle, so BLOB/CLOB-style payloads can be imported without a Python helper. ADT.ai writes `<table>/*.sql` to the main DATA folder `.gitignore` so those generated payload scripts stay ignored by default while avoiding `.gitignore` files inside table sidecar folders; the main table MERGE file remains trackable. Progress is printed as soon as each table starts, before column metadata and rows are fetched, then completed with a row count; pass `-silent` to suppress those per-table rows while keeping the banner, connection block, export summary, and final `TIMER: Ns` footer. It applies `tables_global.where` plus per-table `tables.<table>.where` filters only when those columns exist in the exported table. Generated MERGE scripts use `tables_global.merge` defaults and optional `tables.<table>.merge` overrides for `delete`, `insert`, and `update` sections.

Example MERGE section config:

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

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Output root folder. This can be any ordinary folder and does not need to be a Git repository. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes | environment default schema | Schema to export. |
| `-name`, `--name` | Yes | existing DATA files | Table name pattern or patterns to export. Supports old ADT SQL-like `%` patterns through database table discovery. |
| `-silent`, `--silent` | No | off | Suppress per-table progress rows while keeping command chrome, export summary, and timer. |
| `-debug`, `--debug` | No | off | Show input parameters, SQL queries with bind values, and full Python tracebacks for troubleshooting. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
