- **`search` reads your data: `search TERM -data` finds a text or a number in the rows of tables, views, materialized views and synonyms**, one list per object type. An object the database refuses to read is named under `WARNING - NOT SEARCHED:`, and the rest are still searched.
- **Every error, in `calendar` as everywhere else, opens on a short uppercase headline with no trailing period**, the detail below it in sentence case. Every failure section opens on `ERROR - ` with its body indented two columns, and more warning headers carry the `WARNING - ` prefix.
- **`doctor`: `-init -sync` keeps a project's `.gitattributes` and `.gitignore` current inside an ADT-managed block**, leaving every line outside it alone. The new `auto_sync_git` key, on by default, makes every export do the same on disk before it writes. Large repositories no longer crash it.
- **`doctor`: `-init` writes files with the project's `file_crlf` line ending**, and the scaffolded `.gitignore` carries only what ADT writes into a project.
- **`export_apex`: `-apexlang` always writes LF and carries the plugin and theme static files.** `validate` and `patch -deploy` convert a tree committed with CRLF before SQLcl reads it, and a converted tree still skips its next deploy.
- **`patch` ships an APEXlang application as its folder**, and an APEXlang-only export builds a patch that carries its application. `patch -deploy` refuses up front when a static file the tree names is missing.
- **`patch -create -app <id>` names the target application in `DEPLOY.sql`, its scripts and its logs**, with a comment between the two halves saying which application is imported as which id. An application's `comments/` files are no part of any patch.
- **A patch no longer depends on the target it was created for.** `patch -deploy -target X` switches on that environment's lines and spools into its log folder.
- **A drift refusal in `patch -deploy -app` says who changed the application, when, and how old your base is.**
- **`patch -create` refreshes dependencies only for the schemas the patch carries.** A text file that is not UTF-8 warns instead of failing the patch, and a binary file ships silently.
- **A patch carries the LOB scripts its data MERGE calls**, so a table exported with LOB values deploys through a patch.
- **`export_db` carries on past an object the database refuses** and lists it under a `WARNING` section. Quoted names keep their quotes unless Oracle reads them the same without, an object named with a trailing `$` or `#` loses its owner, and grants are written one statement per grantee.
- **`export_data` round-trips more types.** `delete: true` deletes once before the first MERGE batch, LOB scripts find rows keyed on RAW or DATE columns, and INTERVAL values are written the way Oracle writes them.
- **`rebuild`: `-app` falls back to page-by-page scans when the whole-application scan fails**, and names only the broken pages, by id and name, below the progress list.
- **`connection`: `-set-wallet-pwd` replaces a legacy `wallet_password`**, and a symlinked connection file stays a link when ADT saves it.
- **`diff` reads schemas more exactly.** `-target-schema` no longer reports a grant as missing only because it names the schema, a lower-case `-name` matches, and `-apex -verbose` on APEX 26.1 lists changed supporting-objects scripts.
- **`discovery`: `-file` writes a line break inside a cell as `<br>`**, so a value can no longer close its result block early.
- **`recompile` leaves recycle-bin (`BIN$`) objects out**, and a name its compile statement refuses fails alone instead of ending the pass.
- **`ut` keeps twenty runs per schema and `-name` selection**, and rounds `COVERAGE` half up.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores | Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ---: |
| Unit tests     |   9897 |        |            |     100% |    14 | 2:02 |
| User stories   |    180 |        |          2 |          |     3 | 0:01 |
| Security audit |     26 |        |            |          |     1 | 1:03 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
