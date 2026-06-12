# Changelog — adt

## Unreleased

- Aligned skill guidance with current ADT.ai CLI: project bootstrap now uses `doctor -init`, obsolete patch source-flag guidance was removed, and no-write discovery examples use `-nolog`.
- Updated `search_repo` guidance for cache-backed search, opt-in `-files`, newest-first limits, and rebuild-cache dependency.
- Added `search_repo` to the ADT.ai usage skill, including Git-history search and historical file restore examples.
- Initial ADT.ai usage skill. Covers `export_db` (with automatic `-silent`), `export_apex`, `export_data`, `discovery`, `dependencies`, `diff`, `recompile`, `patch`, `rebuild`, `search_repo`, and project bootstrap through `doctor -init`.
- Read-only `discovery` documented as the first-choice command for database exploration.
- Legacy old-ADT commands `search_apex` and `live_upload` are intentionally omitted until implemented in ADT.ai.
