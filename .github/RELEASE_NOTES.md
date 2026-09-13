- **Breaking: `patch`'s `-install` mode writes one script per schema to `config/install/<SCHEMA>.sql`.** The name and links are the same on every branch. `-schema` picks which schemas get a script. A script at the old `<schema>/database/INSTALL.sql` location moves on the next run. The `patch_install_file` config key is removed.
- **`patch -install -schema` naming no exported schema is refused as `ERROR - ARGUMENT INVALID:`** instead of `PATCH FAILED:`. The message names the value and the schemas that were exported, and the command exits `2`.
- **`patch`'s `-install` mode refreshes a stale dependency graph itself** instead of stopping and asking you to run `dependencies -refresh`. It runs before the commit history rebuild, so a refusal that survives the refresh costs no rebuild.
- **`patch`'s `-install`, `-archive` and `-drop` modes no longer rebuild the commit history first.** They read no commit, so a large repository no longer waits minutes on `REBUILDING COMMITS:` before the work starts.
- **`patch -deploy -app <id>` stamps the application with who deployed it and when.** `LAST_UPDATED_BY` and `LAST_UPDATED_ON` are updated while the imported version is kept. Without an explicit target id, the Builder's audit author is left untouched.
- **`export_apex` no longer writes a `.gitignore` into an APEXlang `static-files/` folder.** The APEX compiler imported that file into the application as one more static file. The ignore now lives in `.git/info/exclude`, and `export_apex`, `validate` and `patch -deploy` remove the leftover from applications already affected.
- **`export_apex`'s `-rest` mode writes REST modules in a stable order.** SQLcl can return templates, handlers and parameters in a different order for the same service, so unchanged services showed up as changed files.
- **Object types read singular everywhere.** The `-compact` bar of `export_db`, the `OBJECTS OVERVIEW:` tables of `export_db`, `recompile` and `patch -install` print `TABLE`, `PACKAGE BODY` and `GRANT`, the spelling `-type` takes.
- **Registering a named SQLcl connection no longer rewrites an unchanged connection file.** When the stored name and fingerprint already match, nothing moves on disk, so a sync client sees no churn, and a file holding a password stays owner-only.
- **The installed `adtai` command is now smoke-tested on Windows, macOS and Linux** from the built wheel, not only the Python module.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores | Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ---: |
| Unit tests     |   8166 |        |            |     100% |    14 | 1:03 |
| User stories   |    137 |        |          2 |          |     3 | 5:02 |
| Security audit |     22 |        |            |          |     1 | 0:15 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
