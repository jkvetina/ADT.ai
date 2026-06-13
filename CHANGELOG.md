# Changelog

## 0.4.4 - 2026-06-13

- Published ADT.ai `0.4.4` with public commands: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.
- Fixed a batch of `export_db` DDL formatting issues so exported files match old ADT's clean, readable layout on real `DBMS_METADATA` output (not just test fixtures): views (simple, compact comma-packed, mixed quoted/unquoted, and CTE select lists), expression indexes, table columns, `INTERVAL` suffixes, `INMEMORY` clauses, schema qualification, and TYPE / TYPE BODY drop preambles.
- Changed `export_db` to consolidate dedicated PK/UNIQUE indexes: when Oracle exports a primary-key or unique constraint as a separate `CREATE [UNIQUE] INDEX` plus `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX`, the constraint is now folded back inline on the table so it reads as if the table were created in one clean statement. Table constraints are ordered deterministically — PRIMARY KEY, then UNIQUE, then FOREIGN KEY, then CHECK, alphabetically by name within each group — while column lines keep their source order.
- Added a `<table>.fix.sql` companion file beside any table whose index-backed constraints were folded inline. It holds the recovery script (`DROP CONSTRAINT` / `DROP INDEX` / `ADD CONSTRAINT`) that rebuilds the original dedicated-index arrangement, so the clean table export loses no information. The companion is regenerated when folding happens and removed when a table no longer has index-backed constraints.
- Changed `export_db` sequence DDL to drop Oracle's default ascending `MAXVALUE` (28 nines), matching how column DDL already strips it, while preserving explicit non-default maxvalues such as `MAXVALUE 999999999999`.

## 0.4.3 - 2026-06-12

- Published ADT.ai `0.4.3` with public commands: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.
- Changed plain Doctor's ADT.ai update check so non-git installs read the latest public release from `jkvetina/ADT.ai` on GitHub before falling back to PyPI.
- Kept plain Doctor read-only: update actions still require `doctor -update` or `doctor -sqlcl`, and `doctor -offline` still skips remote metadata.
- Corrected public help usage lines to show the installed `adtai` command name instead of the removed `adt-ai` entry point.

## 0.4.2 - 2026-06-12

- Published ADT.ai `0.4.2` with public commands: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.
- Included public README, setup reference, LICENSE, and usage documentation for the released commands only.
- Included repo-local skills and their index: `SKILLS/README.md`, `SKILLS/adt`, and `SKILLS/adt-setup`.
- Excluded private tests, connection files, wallets, and unrelated runtime modules.
