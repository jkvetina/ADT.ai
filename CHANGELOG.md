# Changelog

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
