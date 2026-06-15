# Discovery — read-only SELECT (adtai discovery)

`discovery` runs read-only `SELECT` queries against the target database so an AI (or a person) can explore a schema safely. It is intentionally locked down for safe, unsupervised use:

- **SELECT only.** Each statement passes a static validator that rejects anything that is not a single `SELECT` (no DML, DDL, PL/SQL, multiple statements, or comment-smuggled commands). Rejected statements are recorded as errors, never run.
- **Read-only transaction.** Validated statements run through a `SET TRANSACTION READ ONLY` session that is rolled back afterwards, so even a query that tries to mutate state cannot commit anything.
- **Reported by default.** Runs append to a per-minute Markdown report at `config/discovery/<YYYY-MM-DD_HH-MI>.md`, one numbered `## Query N` section per statement, with the SQL and either a rendered result table (capped at `-limit` rows) or the captured error. Inline `-sql` runs also print only the rendered result body to stdout, without repeating the SQL text. Validation and per-query database errors are captured into the report instead of aborting the run, so the command exits `0` even when individual queries fail. Connection/setup failures use the shared `DATABASE CONNECTION FAILED` output and exit non-zero. Use `-nolog` for throwaway runs that should not write a report, update `.gitignore`, or write results back to the source SQL file.
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

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup, and where `config/discovery/` reports are written. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default environment | Connection environment to query, for example `DEV`. |
| `-schema`, `--schema` | No | environment default DB schema | Schema to query. |
| `-sql`, `--sql` | No | none | A single `SELECT` statement to run. Mutually exclusive with `-file`. |
| `-file`, `--file` | No | none | Path to a file of `;`-separated `SELECT` statements. Mutually exclusive with `-sql`. |
| `-limit`, `--limit` | No | `200` | Maximum rows rendered per query in the report. |
| `-nolog`, `--no-log` | No | off | Run and print results without writing a report, touching `.gitignore`, or writing `-file` results back to the source file. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-beep`, `--beep` | No | off | Force the completion chime on for this run, even from a worktree checkout. |

---

← [USAGE.md](../USAGE.md) index
