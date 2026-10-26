- **Every command takes `-json` and prints one JSON object instead of its screen**: `calendar`, `connection`, `diff`, `discovery`, `doctor`, `export_apex`, `export_data`, `export_db`, `patch`, `rebuild`, `recompile`, `search`, `ut` and `validate`, so an agent reads the result without parsing text.
- **`connection`: `pwd_pattern:` derives the password of a schema from its name**, and a schema named on the command line need not be listed.
- **`connection`: an environment takes what it does not define from the first environment in the connection file.**
- **`connection`: SQLcl connection names and fingerprints are no longer stored in the connection file**, and the first command to read an older file rewrites it to delete them.
- **`connection`: `sqlcl_only` reads query results again on SQLcl 26.3.**
- **A SQLcl script holding a password is removed however the run ends.**
- **`patch`: `DROP INDEX IF EXISTS` in a patch script drops the index again.**
- **`export_db`: a LOCAL index on an interval-partitioned table exports as bare `LOCAL`.**

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  11288 |        |            |     100% |    14 |  3:22 |
| User stories   |    276 |        |            |          |     3 | 30:03 |
| Security audit |     20 |        |            |          |     1 |  2:29 |

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
