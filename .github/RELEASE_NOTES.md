- **An application is written `<id>/<alias>` everywhere**, and every APEXlang compile counts down from the time its last compile took.
- **`patch` compiles the APEXlang tree before `-create` builds or `-deploy -app` runs anything**, and `-deploy -app` compiles before it connects. In `validate` and `patch`, `VALIDATING APPS:` rows name each application, tick once a second and carry the compile's warnings.
- **`export_apex -apexlang` compiles what it just exported**, each application in its own block, and reports what the compile found as a warning.
- **`patch`: `-create` replaces an altered table with its ALTER instead of shipping both**, and lists every uncommitted file in the repository as a tree below `PROCESSED FILES:`.
- **`patch`: `-create` warns when an APEXlang application changed since your export**, names it in the warning header and numbers the way out. It reads the live checksum on APEX 24.2 too, on the environment `export_apex` recorded.
- **`patch`: `-deploy` no longer writes `logs_<ENV>/deployment.json`**, and a failed application scan prints `ERROR - VERIFICATION FAILED:` with one row per page.
- **A remedy with more than one step prints as numbered steps**, in `doctor` as in every command, and a `ValueError` or `RuntimeError` gets its own error header.
- **`connection`: `-test` checks that each connection opens and reports an environment as one status table**, and with `-schema` shows what each schema holds. A connection file that does not parse is refused without printing its contents.
- **Unsafe connection fields and update redirects are rejected.**
- **SQLcl stays on the thin driver on Windows too**, where an installed Oracle client older than 23 used to break every SQLcl-backed command.
- **`export_db`: `-compact` closes its progress bar on `ALL DONE`.**
- **`export_data` leaves a comment-only `.sql` in place of a MERGE it can no longer generate.**
- **`export_apex -deep` includes a page's shared components.**
- **`search`: `TERM` names an exported file it cannot read and carries on**, and the dependency graph attributes an APEX caller to the owner of the object it uses.
- **The `diff` documentation now ships its schema and APEX pages.**

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  10271 |        |            |     100% |    14 |  2:52 |
| User stories   |    185 |        |          2 |          |     3 | 19:13 |
| Security audit |     22 |        |            |          |     1 |  0:41 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
