# ADT.ai Documentation

This is the public documentation index for ADT.ai `0.9.5`.

<br>

## Commands

| Command | Purpose |
| ------- | ------- |
| [export_db](export_db.md) | Export database objects to files. |
| [export_db / where files land](export_db_layout.md) | The path template, object groups, duplicate files, and measuring what an environment holds. |
| [doctor](doctor.md) | Check local setup and bootstrap project config. |
| [calendar](calendar.md) | Show Git activity across all branches as a calendar. |
| [connection](connection.md) | Create or edit a project's connection YAML file. |
| [connection / where the password lives](connection_passwords.md) | The stored format, the vault and no-password modes, masking, and changing the key. |
| [connection / security](connection_security.md) | How a stored password is protected, written to hand to a security reviewer. |
| [dependencies](dependencies.md) | Query or refresh the dependency mirror. |
| [discovery](discovery.md) | Run read-only SELECT discovery queries. |
| [export_apex](export_apex.md) | Reveal and export APEX workspaces and applications. |
| [export_apex / the formats](export_apex_formats.md) | What each format flag writes, the APEX version gates, and the schema-level shapes. |
| [export_data](export_data.md) | Export table data as CSV plus generated MERGE SQL. |
| [flow](flow.md) | Map APEX page navigation into a queryable flow store. |
| [rebuild](rebuild.md) | Refresh the Git commit cache. |
| [recompile](recompile.md) | Recompile invalid database objects. |
| [recompile / trailing whitespace](recompile_trailing.md) | What `-trailing` rewrites, the separate path a view takes, and what the sweep guarantees. |
| [search_repo](search_repo.md) | Search cached Git commit history. |
| [ut](ut.md) | Run the schema's utPLSQL test suites; non-zero on failures, and on a zero-test run. |
| [ut / coverage](ut_coverage.md) | The coverage column, the module figure, the gate, and what moved since the last run. |
| [ut / choosing what runs](ut_discovery.md) | The naming convention, the `-name` patterns, and utPLSQL's annotation cache. |
| [validate](validate.md) | Validate exported APEXlang folders with the APEXlang compiler. |

<br>

## Topics

| Topic | What it answers |
| ----- | --------------- |
| [Project configuration](config.md) | Where config and connection files are looked for, the identity and timeout keys, line endings, `STARTUP.sql`, environment variables. |
| [Console output](console.md) | How to read `--help`, the banner and section shape, the failure screens, completion beeps, and the flags every command shares. |
| [Install and machine setup](../SETUP.md) | Getting `adtai` onto this machine, Instant Client, SQLcl, and what `doctor` checks. |

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

See [SETUP.md](../SETUP.md) for local setup, and [config.md](config.md#config-connections-and-wallets) for where config, connection files and wallets are looked for. Keep connection YAML files and Oracle wallets outside Git-tracked content.
