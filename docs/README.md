# ADT.ai Documentation

This is the public documentation index for ADT.ai `0.9.3`.

<br>

## Commands

| Command | Purpose |
| ------- | ------- |
| [export_db](export_db.md) | Export database objects to files. |
| [doctor](doctor.md) | Check local setup and bootstrap project config. |
| [calendar](calendar.md) | Show Git activity across all branches as a calendar. |
| [connection](connection.md) | Create or edit a project's connection YAML file. |
| [dependencies](dependencies.md) | Query or refresh the dependency mirror. |
| [discovery](discovery.md) | Run read-only SELECT discovery queries. |
| [export_apex](export_apex.md) | Reveal and export APEX workspaces and applications. |
| [export_data](export_data.md) | Export table data as CSV plus generated MERGE SQL. |
| [flow](flow.md) | Map APEX page navigation into a queryable flow store. |
| [rebuild](rebuild.md) | Refresh the Git commit cache. |
| [recompile](recompile.md) | Recompile invalid database objects. |
| [search_repo](search_repo.md) | Search cached Git commit history. |
| [ut](ut.md) | Run the schema's utPLSQL test suites; non-zero on failures, and on a zero-test run. |
| [validate](validate.md) | Validate exported APEXlang folders with the APEXlang compiler. |

<br>

## Topics

| Topic | What it answers |
| ----- | --------------- |
| [Shared arguments](arguments.md) | The flags every command takes, and how `-root`, `-config-dir` and `-env` resolve files. |
| Install and machine setup | Getting `adtai` onto this machine, Instant Client, SQLcl, and what `doctor` checks. |

<br>

## Install

```bash
python3 -m pip install -e .
```

<br>

## Help

```bash
adtai --help
adtai <command> --help
```

<br>

## Config, Connections, and Wallets

See SETUP.md for local setup. Keep connection YAML files and Oracle wallets outside Git-tracked content.
