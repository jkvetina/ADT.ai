# Changelog

## 0.4.6 - 2026-06-14

- Fixed the database error banner: a query that fails *after* a successful connection now prints a `DATABASE QUERY FAILED` header, the offending SQL, and the database error message, instead of mislabeling it as `DATABASE CONNECTION FAILED`. The wallet/connection advice footer now appears only for real connection failures.
- Published ADT.ai `0.4.6` with public commands: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.
- Included public README, setup reference, LICENSE, and usage documentation for the released commands only.
- Included repo-local skills and their index: `SKILLS/README.md`, `SKILLS/adt`, and `SKILLS/adt-setup`.
- Excluded private tests, connection files, wallets, and unrelated runtime modules.
