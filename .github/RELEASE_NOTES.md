- **`diff -rest` compares the REST modules, privileges and roles two schemas publish.** It runs `export_apex`'s `-rest` export on both sides and lists each one as `MISSING`, `EXTRA` or `CHANGED`. The object comparison is skipped, so the run is fast.
- **`diff -data` compares the rows of the tables `export_data` exports.** Rows match on their key, and only data is compared, never metadata. `-ignore` and `-limit` narrow the comparison.
- **`diff -data -verbose` prints one block per table and names what differs.** `-out` writes the untrimmed rows.
- **The `diff` documentation is a hub page plus one page per mode.**
- **`recompile -vpd` reports VPD policies and the tables that carry a tenant column but no policy.** Policies are grouped by function, with the columns each one filters on.
- **`recompile -vpd` reads `COLUMNS` from the policy function's source and reads the `export_db` file**, exporting what is missing or old. It prints one column name per row, in the order `FUNCTION`, `COLUMNS`, `DYNAMIC`, `TABLES`.
- **Every `recompile` report prints its first header before it reads.** `-vpd`, `-disabled`, `-jobs`, `-synonyms` and `-trailing` no longer sit silent under the connection block.
- **`recompile` drops stray `DEPSCAN$` helper procedures and never compiles or rewrites one.** APEX's dependency scan generates them, and `dependencies` and `patch` already dropped them after their own scan. `recompile -trailing` no longer rewrites them, and the report-only modes leave them alone.
- **An empty `recompile -trailing` run leaves two blank lines above `TIMER:`, not four.**

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores | Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ---: |
| Unit tests     |   8631 |        |            |     100% |    14 | 1:32 |
| User stories   |    154 |        |          2 |          |     3 | 7:54 |
| Security audit |     23 |        |            |          |     1 | 0:15 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
