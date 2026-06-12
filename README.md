# ADT.ai

ADT.ai is a Python command-line tool for exporting Oracle database, APEX, and data assets into deterministic, Git-friendly files.

Version `0.2.0` ships these public commands:

- `adtai export_db` exports Oracle database objects to files.
- `adtai doctor` checks local setup, runs explicit updates, and bootstraps project config.
- `adtai export_apex` reveals and exports APEX workspaces, applications, REST definitions, and files.
- `adtai export_data` exports table data as CSV plus generated MERGE SQL.

## Install

Install from this checkout:

```bash
python3 -m pip install -e .
```

This installs two equivalent commands:

```bash
adtai --help
adt-ai --help
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
- [USAGE/export_apex.md](USAGE/export_apex.md) documents APEX workspace and application export.
- [USAGE/export_data.md](USAGE/export_data.md) documents table data export.
- [CHANGELOG.md](CHANGELOG.md) records released versions.

## Public Scope

This public checkout intentionally includes only the runtime code, configuration defaults, and documentation needed for the commands listed above. Private tests, connection files, wallets, and unrelated internal modules are not part of this checkout.
