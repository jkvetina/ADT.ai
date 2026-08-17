# ADT.ai

ADT.ai is a Python command-line tool for exporting Oracle database, APEX, and data assets into deterministic, Git-friendly files.

![Database in, Git-friendly files out](images/cover.png)

Version `0.9.0` ships these public commands:

- `adtai export_db` exports Oracle database objects to files.
- `adtai doctor` checks local setup, runs explicit updates, and bootstraps project config.
- `adtai calendar` renders your Git activity across all branches as a month-by-month calendar, from the cached commit history.
- `adtai connection` creates or edits a project's connection YAML file — add an environment, add a schema, or set a password — from the command line.
- `adtai dependencies` queries and refreshes the local Oracle dependency mirror.
- `adtai discovery` runs read-only SELECT discovery queries and can print without writing logs.
- `adtai export_apex` reveals and exports APEX workspaces, applications, REST definitions, and files.
- `adtai export_data` exports table data as CSV plus generated MERGE SQL.
- `adtai flow` maps an APEX application's page navigation links into a local SQLite store and renders Mermaid, DOT, and JSON flow diagrams.
- `adtai rebuild` refreshes the Git commit cache used by repository search workflows.
- `adtai recompile` recompiles invalid Oracle database objects with scoped filters.
- `adtai search_repo` searches cached Git commit history and can restore selected file versions.
- `adtai ut3` runs the connected schema's utPLSQL test suites and reports every non-passing test, with a non-zero exit code on any failure, error, or run that executed no tests at all.
- `adtai validate` runs the APEXlang compiler over exported `apexlang/` folders and reports its errors with a non-zero exit code. It needs no database connection.

Build verified by 3352 private unit tests.

## Install

Install from this checkout:

```bash
python3 -m pip install -e .
```

This installs two equivalent commands:

```bash
adtai --help
adt --help
```

## Quick Start

Check your machine setup:

```bash
adtai doctor
```

Create a project config skeleton:

```bash
adtai doctor -init -root /path/to/project
```

Export database objects from a configured project folder:

```bash
cd /path/to/project
adtai export_db
```

Preview the database export without writing files:

```bash
adtai export_db -dry-run
```

Reveal configured APEX applications:

```bash
adtai export_apex -reveal
```

Export configured table data:

```bash
adtai export_data
```

## Documentation

- [SETUP.md](SETUP.md) covers install and environment setup.
- [USAGE.md](USAGE.md) is the command index.
  - [USAGE/export_db.md](USAGE/export_db.md) documents database object export.
  - [USAGE/doctor.md](USAGE/doctor.md) documents setup checks and project bootstrap.
  - [USAGE/calendar.md](USAGE/calendar.md) documents Git activity calendars.
  - [USAGE/connection.md](USAGE/connection.md) documents connection YAML file management.
  - [USAGE/dependencies.md](USAGE/dependencies.md) documents dependency mirror queries and refreshes.
  - [USAGE/discovery.md](USAGE/discovery.md) documents read-only SELECT discovery queries.
  - [USAGE/export_apex.md](USAGE/export_apex.md) documents APEX workspace and application export.
  - [USAGE/export_data.md](USAGE/export_data.md) documents table data export.
  - [USAGE/flow.md](USAGE/flow.md) documents APEX page navigation flow mapping.
  - [USAGE/rebuild.md](USAGE/rebuild.md) documents Git commit cache rebuilds.
  - [USAGE/recompile.md](USAGE/recompile.md) documents invalid object recompilation.
  - [USAGE/search_repo.md](USAGE/search_repo.md) documents cached Git history search.
  - [USAGE/ut3.md](USAGE/ut3.md) documents utPLSQL test suite runs.
  - [USAGE/validate.md](USAGE/validate.md) documents APEXlang export validation.
- [SKILLS/README.md](SKILLS/README.md) explains which repo-local skill to install and when to use setup.
  - [SKILLS/adt/SKILL.md](SKILLS/adt/SKILL.md) is the agent-facing ADT.ai command guide.
  - [SKILLS/adt-setup/SKILL.md](SKILLS/adt-setup/SKILL.md) is the agent-facing setup checklist.
- [CHANGELOG.md](CHANGELOG.md) records released versions.
- [LICENSE](LICENSE) covers public use.

## Articles

Longer write-ups on what ADT.ai does and how it is used in real Oracle and APEX projects are published on One Oracle Developer:

- [ADT articles on oneoracledeveloper.com](https://www.oneoracledeveloper.com/search/label/project_adt)

## Public Scope

This public checkout intentionally includes only the runtime code, configuration defaults, and documentation needed for the commands listed above. Private tests, connection files, wallets, and unrelated internal modules are not part of this checkout.
