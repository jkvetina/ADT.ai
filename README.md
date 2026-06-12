# ADT.ai

ADT.ai is a Python command-line tool for exporting Oracle database objects into a deterministic, Git-friendly folder structure.

Version `0.1.0` ships two public commands:

- `adtai export_db` exports Oracle database objects to files.
- `adtai doctor` checks local setup, runs explicit updates, and bootstraps project config.

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

Preview the export without writing files:

```bash
adtai export_db -dry-run
```

## Documentation

- [SETUP.md](SETUP.md) covers install and environment setup.
- [USAGE.md](USAGE.md) is the command index.
- [USAGE/export_db.md](USAGE/export_db.md) documents database object export.
- [USAGE/doctor.md](USAGE/doctor.md) documents setup checks and project bootstrap.
- [CHANGELOG.md](CHANGELOG.md) records released versions.

## Public Scope

This public checkout intentionally includes only the runtime code, configuration defaults, and documentation needed for the commands listed above. Private tests, connection files, wallets, and unrelated internal modules are not part of this checkout.
