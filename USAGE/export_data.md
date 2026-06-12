# Export Table Data (adtai export_data)

Export named table data from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_data -name APP_SETTINGS
```

When `-name` is omitted, ADT.ai follows old ADT behavior and exports tables that already have DATA `.sql` files in the configured DATA folder. The current implementation writes CSV files and generated MERGE SQL files. Progress is printed as each table starts and completes with a row count, then the command ends with the same `TIMER: Ns` footer as `export_db`. It applies `tables_global.where` plus per-table `tables.<table>.where` filters only when those columns exist in the exported table. Generated MERGE scripts use `tables_global.merge` defaults and optional `tables.<table>.merge` overrides for `delete`, `insert`, and `update` sections.

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
| `-debug`, `--debug` | No | off | Show input parameters, SQL queries with bind values, and full Python tracebacks for troubleshooting. |
| `-h`, `--help` | No | off | Show command help. |

---

← [USAGE.md](../USAGE.md) index
