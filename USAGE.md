# ADT.ai Usage

This is the public usage index for ADT.ai `0.8.4`.

## Commands

| Command | Reference | Purpose |
| ------- | --------- | ------- |
| `export_db` | [USAGE/export_db.md](USAGE/export_db.md) | Export database objects to files. |
| `doctor` | [USAGE/doctor.md](USAGE/doctor.md) | Check local setup and bootstrap project config. |
| `calendar` | [USAGE/calendar.md](USAGE/calendar.md) | Show Git activity across all branches as a calendar. |
| `connection` | [USAGE/connection.md](USAGE/connection.md) | Create or edit a project's connection YAML file. |
| `dependencies` | [USAGE/dependencies.md](USAGE/dependencies.md) | Query or refresh the dependency mirror. |
| `discovery` | [USAGE/discovery.md](USAGE/discovery.md) | Run read-only SELECT discovery queries. |
| `export_apex` | [USAGE/export_apex.md](USAGE/export_apex.md) | Reveal and export APEX workspaces and applications. |
| `export_data` | [USAGE/export_data.md](USAGE/export_data.md) | Export table data as CSV plus generated MERGE SQL. |
| `flow` | [USAGE/flow.md](USAGE/flow.md) | Map APEX page navigation into a queryable flow store. |
| `rebuild` | [USAGE/rebuild.md](USAGE/rebuild.md) | Refresh the Git commit cache. |
| `recompile` | [USAGE/recompile.md](USAGE/recompile.md) | Recompile invalid database objects. |
| `search_repo` | [USAGE/search_repo.md](USAGE/search_repo.md) | Search cached Git commit history. |
| `ut3` | [USAGE/ut3.md](USAGE/ut3.md) | Run the schema's utPLSQL test suites; non-zero on failures, and on a zero-test run. |
| `validate` | [USAGE/validate.md](USAGE/validate.md) | Validate exported APEXlang folders with the APEXlang compiler. |

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

See [SETUP.md](SETUP.md) for local setup. Keep connection YAML files and Oracle wallets outside Git-tracked content.
