- **`export_db` exports VPD policies, application contexts, tablespace quotas and network ACEs**, and `export_data` warns when a table it exports sits under a policy that may have hidden its rows.
- **`-backup` on `export_db`, `export_data` and `export_apex` exports what the shipped config leaves out**: every row and column, saved reports, and each sequence at its current position.
- **`export_apex` carries on past a slice APEX refuses** and lists every failed slice with the reason APEX gave, then exits 1. An application whose setup is refused no longer ends the run.
- **`export_data` and `diff -data` keep exact table and column names**, quoted and mixed-case ones included, and refuse two tables that would write to the same file.
- **`export_data` exports dates before year 1, skips nested-table storage tables, and a NULL in a nullable LOB or JSON column replays as NULL.**
- **`export_db`: a scheduler program carries its arguments and enabled state**, replays over itself, and a program-only replay keeps the states of its jobs and its grants. A full export with `auto_delete` removes a stale ACL file.
- **Exported code keeps the tabs, form feeds and trailing blanks inside string literals.**
- **`connection`: a key written twice keeps its last value, a cloned environment with a new address no longer copies the old route, and a generated SQLcl connection name always ends in its schema.**
- **`ignored_columns` matches in any case in `export_data` and `diff`, and `-recent 0` is refused on every command.**
- **`patch`: a hardened patch script refuses a different target schema before it checks any local object.**
- **`recompile` and `ut` stop on a lost connection, and `recompile` compiles and refreshes objects with mixed-case quoted names.**
- **`calendar` skips a branch name it cannot store, and `doctor` gives its version checks the same timeout as its other checks.**
- **`-json` output on `discovery`, `rebuild`, `search` and `validate`, as on every command, survives text holding a lone surrogate character, and a negative `-recent` window is refused.**

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  11926 |        |            |     100% |    14 |  2:09 |
| User stories   |    286 |        |            |          |     3 | 20:54 |
| Security audit |     22 |        |            |          |     1 |  0:37 |

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
