# ADT.ai Usage

This is the public usage index for ADT.ai `0.3.0`.

## Commands

| Command | Reference | Purpose |
| ------- | --------- | ------- |
| `export_db` | [USAGE/export_db.md](USAGE/export_db.md) | Export database objects to files. |
| `doctor` | [USAGE/doctor.md](USAGE/doctor.md) | Check local setup and bootstrap project config. |
| `export_apex` | [USAGE/export_apex.md](USAGE/export_apex.md) | Reveal and export APEX workspaces and applications. |
| `export_data` | [USAGE/export_data.md](USAGE/export_data.md) | Export table data as CSV plus generated MERGE SQL. |
| `recompile` | [USAGE/recompile.md](USAGE/recompile.md) | Recompile invalid database objects. |
| `rebuild` | [USAGE/rebuild.md](USAGE/rebuild.md) | Refresh the Git commit cache. |
| `search_repo` | [USAGE/search_repo.md](USAGE/search_repo.md) | Search cached Git commit history. |
| `discovery` | [USAGE/discovery.md](USAGE/discovery.md) | Run read-only SELECT discovery queries. |

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
