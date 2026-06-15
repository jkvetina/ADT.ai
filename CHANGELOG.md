# Changelog

All notable changes to the public ADT.ai release are recorded here, newest first.

## 0.5.0 - 2026-06-15

- Fixed the shipped `config.yaml` path defaults back to the documented database-first layout: `path_objects` is `database/<schema>/<object_type>/` and `path_apex` is `apex/<schema>/`. This matches the README, the `adt` skill, `USAGE/search_repo.md`, and how `search_repo` derives object paths; the interim layout could silently break `search_repo -type` and `-name` on newly exported files.
- Reorganized `export_db` DDL normalization into focused per-object-type modules (table, view, index, sequence, synonym, type, job) for clearer, more consistent formatting. No change to command usage.
- Split the CLI into a modular `cli_*` family (parsing, help, runtime, context) with `cli.py` as a thin facade. Internal restructuring only — all commands and options behave as before.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.

## 0.4.6 - 2026-06-14

- Fixed the database error banner: a query that fails *after* a successful connection now prints a `DATABASE QUERY FAILED` header, the offending SQL, and the database error message, instead of mislabeling it as `DATABASE CONNECTION FAILED`. The wallet/connection advice footer now appears only for real connection failures.

## 0.4.5 - 2026-06-14

- Refined `export_apex` inventory queries.

## 0.4.4 - 2026-06-13

- Fixed a batch of `export_db` DDL formatting issues so exported files match a clean, readable layout on real `DBMS_METADATA` output: views (simple, compact comma-packed, mixed quoted/unquoted, and CTE select lists), expression indexes, table columns, `INTERVAL` suffixes, `INMEMORY` clauses, schema qualification, and TYPE / TYPE BODY drop preambles.
- Changed `export_db` to consolidate dedicated PK/UNIQUE indexes: when Oracle exports a primary-key or unique constraint as a separate `CREATE [UNIQUE] INDEX` plus `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX`, the constraint is now folded back inline on the table so it reads as if the table were created in one clean statement. Table constraints are ordered deterministically — PRIMARY KEY, then UNIQUE, then FOREIGN KEY, then CHECK, alphabetically by name within each group — while column lines keep their source order.
- Added a `<table>.fix.sql` companion file beside any table whose index-backed constraints were folded inline. It holds the recovery script (`DROP CONSTRAINT` / `DROP INDEX` / `ADD CONSTRAINT`) that rebuilds the original dedicated-index arrangement, so the clean table export loses no information. The companion is regenerated when folding happens and removed when a table no longer has index-backed constraints.
- Changed `export_db` sequence DDL to drop Oracle's default ascending `MAXVALUE` (28 nines), matching how column DDL already strips it, while preserving explicit non-default maxvalues such as `MAXVALUE 999999999999`.

## 0.4.3 - 2026-06-12

- Changed Doctor's ADT.ai update check so non-git installs read the latest public release from `jkvetina/ADT.ai` on GitHub before falling back to PyPI.
- Kept Doctor read-only: update actions still require `doctor -update` or `doctor -sqlcl`, and `doctor -offline` still skips remote metadata.
- Corrected public help usage lines to show the installed `adtai` command name instead of the removed `adt-ai` entry point.

## 0.4.2 - 2026-06-12

- Documentation and help-text corrections across the README and usage index.

## 0.4.1 - 2026-06-12

- Added the public skills index `SKILLS/README.md`.

## 0.4.0 - 2026-06-12

- Added repo-local skills `SKILLS/adt` and `SKILLS/adt-setup` to help set up and drive ADT.ai.

## 0.3.0 - 2026-06-12

- Added commands `recompile`, `rebuild`, `search_repo`, and `discovery`, each with usage documentation.

## 0.2.0 - 2026-06-12

- Added commands `export_apex` and `export_data`, each with usage documentation.
- Added a LICENSE file to the public checkout.

## 0.1.0 - 2026-06-12

- First public release with the `export_db` and `doctor` commands.
