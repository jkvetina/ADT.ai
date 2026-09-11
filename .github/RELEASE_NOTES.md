- **`export_apex`'s `-compact` mode now prints one row per exported unit instead of one row for the whole schema**, and a finished row no longer names the slice that ran last.
- **Every dotted progress row now reaches the right margin.** Two commands had stopped filling it.
- **`export_apex`'s `-embedded` mode no longer fails with `ORA-06502` on an application with no embedded code.**
- **`validate` no longer confuses a patch's own snapshot copies of an APEX export with the real export.** Several exports sharing one staging name could resolve to one directory and compile only the last, so a real application could report `EMPTY`. It now recognizes an export by its folder shape.
- **`export_db` keeps a view's 23ai annotations, and a materialized view no longer loses them silently.**
- **`patch` and `dependencies` commands run faster.** Where a run issues several SQLcl requests in a row — `patch -deploy` per script, `dependencies -refresh` per schema — it now reuses one SQLcl process instead of starting a fresh one each time.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores | Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ---: |
| Unit tests     |   7985 |        |            |     100% |    14 | 0:51 |
| User stories   |    134 |        |          2 |          |     3 | 8:25 |
| Security audit |     22 |        |            |          |     1 | 0:59 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
