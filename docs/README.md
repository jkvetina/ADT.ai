# ADT.ai Documentation

This is the public documentation index for ADT.ai `0.9.1`.

## Commands

| Command | Reference | Purpose |
| ------- | --------- | ------- |
| `export_db` | [export_db.md](export_db.md) | Export database objects to files. |
| `doctor` | [doctor.md](doctor.md) | Check local setup and bootstrap project config. |
| `calendar` | [calendar.md](calendar.md) | Show Git activity across all branches as a calendar. |
| `connection` | [connection.md](connection.md) | Create or edit a project's connection YAML file. |
| `dependencies` | [dependencies.md](dependencies.md) | Query or refresh the dependency mirror. |
| `discovery` | [discovery.md](discovery.md) | Run read-only SELECT discovery queries. |
| `export_apex` | [export_apex.md](export_apex.md) | Reveal and export APEX workspaces and applications. |
| `export_data` | [export_data.md](export_data.md) | Export table data as CSV plus generated MERGE SQL. |
| `flow` | [flow.md](flow.md) | Map APEX page navigation into a queryable flow store. |
| `rebuild` | [rebuild.md](rebuild.md) | Refresh the Git commit cache. |
| `recompile` | [recompile.md](recompile.md) | Recompile invalid database objects. |
| `search_repo` | [search_repo.md](search_repo.md) | Search cached Git commit history. |
| `ut` | [ut.md](ut.md) | Run the schema's utPLSQL test suites; non-zero on failures, and on a zero-test run. |
| `validate` | [validate.md](validate.md) | Validate exported APEXlang folders with the APEXlang compiler. |

## Install

```bash
python3 -m pip install -e .
```

## Help

```bash
adtai --help
adtai <command> --help
```

## Config, Connections, and Wallets

See [SETUP.md](../SETUP.md) for local setup. Keep connection YAML files and Oracle wallets outside Git-tracked content.
