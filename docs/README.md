# ADT.ai Documentation

This is the public documentation index for ADT.ai `1.0.0`.

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
| [Local stores / dependencies](storage_dependencies.md) | The dictionary mirror: the schema tables, the refresh stamps and the database clock offset. |
| [Local stores / dependencies, APEX](storage_dependencies_apex.md) | The APEX usage mirror: which components use which database objects. |
| [discovery](discovery.md) | Run read-only SELECT discovery queries. |
| [export_apex](export_apex.md) | Reveal and export APEX workspaces and applications. |
| [export_apex / the formats](export_apex_formats.md) | What each format flag writes, the APEX version gates, and the schema-level shapes. |
| [Local stores / apex](storage_apex.md) | The APEX cache: applications, developers, timers and watermarks. |
| [export_data](export_data.md) | Export table data as CSV plus generated MERGE SQL. |
| [flow](flow.md) | Map APEX page navigation into a queryable flow store. |
| [Local stores / flow](storage_flow.md) | The navigation store: applications, pages, link sources and edges. |
| [patch](patch.md) | Build and deploy release patches from committed repo changes. |
| [patch / what goes in](patch_install.md) | Which files a patch picks up, their order, the two gates, and the project SQL around them. |
| [patch / which version ships](patch_content.md) | Committed, newest, local and live source choices for the selected files. |
| [patch / templates and scripts](patch_templates.md) | The session defaults, the template slots, the per-patch scripts, and the generated helpers. |
| [patch / deploying](patch_deploy.md) | The build and deploy reports, the grant and view checks, and where the logs land. |
| [patch / verification](patch_verify.md) | Checking the deployed application's SQL and reading the verification result. |
| [patch / application targets](patch_app.md) | Selecting applications and resolving their sandbox targets. |
| [patch / application imports](patch_import.md) | Importing APEXlang applications from their source folders. |
| [patch / dropping sandbox applications](patch_drop.md) | Ownership checks, explicit targets and receipts for sandbox removal. |
| [patch / archiving](patch_archive.md) | What `-archive` takes, how a pattern selects folders, and where the zips are filed. |
| [patch / hash mode](patch_hash.md) | Building from what the repository no longer agrees with the target about. |
| [rebuild](rebuild.md) | Refresh the Git commit cache. |
| [Local stores / commits](storage_commits.md) | The per-branch commit store: its tables, indexes and diagram. |
| [recompile](recompile.md) | Recompile invalid database objects. |
| [recompile / trailing whitespace](recompile_trailing.md) | What `-trailing` rewrites, the separate path a view takes, and what the sweep guarantees. |
| [search_repo](search_repo.md) | Search cached Git commit history. |
| [ut](ut.md) | Run the schema's utPLSQL test suites; non-zero on failures, and on a zero-test run. |
| [ut / coverage](ut_coverage.md) | The coverage column, the module figure, the gate, and what moved since the last run. |
| [ut / choosing what runs](ut_discovery.md) | The naming convention, the `-name` patterns, and utPLSQL's annotation cache. |
| [Local stores / ut](storage_ut.md) | The run history: runs and per-package coverage. |
| [validate](validate.md) | Validate exported APEXlang folders with the APEXlang compiler. |

## Topics

| Topic | What it answers |
| ----- | --------------- |
| [Why ADT.ai](why.md) | What you answer by hand today, what each command group does instead, and a fifteen-minute trial against a development schema. |
| [Project configuration](config.md) | Where config and connection files are looked for, the identity and timeout keys, line endings, `STARTUP.sql`, environment variables. |
| [Local stores](storage.md) | Which SQLite and YAML files ADT.ai keeps under `config/`, who writes and reads each, when deleting one is safe, and the conventions they follow. |
| [Console output](console.md) | How to read `--help`, the banner and section shape, the failure screens, completion beeps, and the flags every command shares. |
| [Install and machine setup](../SETUP.md) | Getting `adtai` onto this machine, Instant Client, SQLcl, and what `doctor` checks. |

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

See [SETUP.md](../SETUP.md) for local setup, and [config.md](config.md#config-connections-and-wallets) for where config, connection files and wallets are looked for. Keep connection YAML files and Oracle wallets outside Git-tracked content.
