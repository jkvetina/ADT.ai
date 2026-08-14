# Discovery, read-only SELECT (adtai discovery)

`discovery` runs read-only `SELECT` queries against the target database so an AI (or a person) can explore a schema safely. It is intentionally locked down for safe, unsupervised use:

- **SELECT only.** Each statement passes a static validator that rejects anything that is not a single `SELECT` (no DML, DDL, PL/SQL, multiple statements, or comment-smuggled commands). Rejected statements are recorded as errors, never run.
- **Read-only transaction.** Validated statements run through a `SET TRANSACTION READ ONLY` session that is rolled back afterwards, so even a query that tries to mutate state cannot commit anything.
- **Reported by default.** Runs append to a per-minute Markdown report at `config/discovery/<YYYY-MM-DD--HH-MI>.md`, one numbered `## Query N` section per statement, with the SQL and either a rendered result table (capped at `-limit` rows) or the captured error. Inline `-sql` runs also print only the rendered result body to stdout, without repeating the SQL text. Validation and per-query database errors are captured into the report instead of aborting the run, so the command exits `0` even when individual queries fail. Connection/setup failures use the shared `DATABASE CONNECTION FAILED` output and exit non-zero. Use `-nolog` for throwaway runs that should not write a report or update `.gitignore`. `-nolog` suppresses report logging only, it does **not** disable `-file` write-back: when `-file` is passed, the `/* ADT-RESULT */` blocks are written back into the source SQL file regardless of `-nolog` (see [Result write-back](#result-write-back)).
- **Git-ignored output.** The first run ensures `config/discovery/` is listed in the project's `.gitignore`, keeping discovery transcripts out of version control.

Run a single inline query from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai discovery -env DEV -sql "SELECT table_name FROM user_tables ORDER BY table_name"
```

Run several queries from a file of `;`-separated statements:

```bash
adtai discovery -env DEV -file ./explore.sql
```

Run a throwaway query without writing a report:

```bash
adtai discovery -env DEV -sql "SELECT COUNT(*) FROM user_objects" -nolog
```

Query a specific schema and raise the per-query row cap:

```bash
adtai discovery -env DEV -schema APP -sql "SELECT * FROM app_settings" -limit 500
```

Exactly one of `-sql` or `-file` must be provided; passing both or neither exits with an argument error.

### One schema per run

`-schema` takes a single schema here, unlike the repeatable `-schema` on `export_db`, `export_data`, `export_apex`, `dependencies`, and `recompile`. That is deliberate, not an oversight: discovery runs *your* SQL and prints one result per statement, so N schemas would mean N result sets per query, a different output contract, not just a second connection. Oracle already spans schemas from inside a single statement, which is the tool you want here:

```bash
adtai discovery -env DEV -sql "SELECT owner, object_type, COUNT(*) FROM all_objects WHERE owner IN ('APP','CORE') GROUP BY owner, object_type"
```

To run the same query against several schemas separately, run discovery once per schema.

## Result write-back

When `-file` is used, discovery writes each query's rendered result back into the source SQL file as a `/* ADT-RESULT */` block beneath the statement it belongs to, so the file becomes a self-contained record of the queries and their latest output. This write-back is a core part of `-file` mode and happens on every `-file` run.

`-nolog` does **not** turn this off. `-nolog` only suppresses report logging, it skips the `config/discovery/` report and the `.gitignore` update, but the `-file` write-back still occurs. To run a file of statements without modifying it, do not pass `-file`; run the statements another way (for example inline via `-sql`).

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup, and where `config/discovery/` reports are written. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default environment | Connection environment to query, for example `DEV`. |
| `-schema`, `--schema` | No | environment default DB schema | Schema to query. Single-valued by design, see [One schema per run](#one-schema-per-run). |
| `-sql`, `--sql` | No | none | A single `SELECT` statement to run. Mutually exclusive with `-file`. |
| `-file`, `--file` | No | none | Path to a file of `;`-separated `SELECT` statements. Mutually exclusive with `-sql`. |
| `-limit`, `--limit` | No | `200` | Maximum rows rendered per query in the report. |
| `-nolog`, `--no-log` | No | off | Run and print results without writing a report or touching `.gitignore`. Does **not** affect `-file` write-back, `/* ADT-RESULT */` blocks are still written to the source file (see [Result write-back](#result-write-back)). |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
