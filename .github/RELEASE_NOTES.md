- **New command: `diff` compares two schemas and writes a reviewable release artifact.** `-source` and `-target` name the environments and `-schema` the schema; `-type` and `-name` narrow the comparison. The screen lists changed objects and grants by which side is missing what.
- **`live_upload` ships in the public build.** `live_upload -once` uploads every file in a folder and exits, into the application or with `-workspace` into the workspace, and keeps subfolder names.
- **`patch -create -files_ws` carries every workspace static file**, and a static file in a subfolder installs under its relative name, such as `css/app.css`.
- **Breaking: `patch -create` stops on a file that is not valid UTF-8 unless the new `repo_encoding` config key names its code page**, for example `repo_encoding: cp1250`. It used to ship the file with its national characters replaced.
- **`patch` install scripts link six shared lock scripts from `config/patch_template/locks/`**, and a patch with two or more install scripts writes `DEPLOY.sql` in deploy order, which `-deploy` follows.
- **`patch -create` generates the ALTER when Oracle refuses a table's previous version**, such as one with a lost or doubled comma, by rebuilding it from its columns and constraints. A hash baseline stores every table, so a table changed by hand on the target still gets its ALTER.
- **`export_db` writes a `--` comment inside a column DEFAULT as a `/* */` comment**, so the exported table file builds again.
- **`export_apex` exports an application through the configured schema that reaches it**, even when its owner is not in the connection file. `export_apex -reveal 430 431` says where those applications live and lists every schema that reaches each one.
- **`export_data` keys rows by a unique key ahead of an identity primary key**, so file names and the MERGE match across environments. A long text LOB reloads without broken characters, and a patch now deploys an exported LOB table.
- **`connection` sets thick mode on an environment with `-thick [PATH|Y]`**, together with `-create` or `-add-env`. The connection docs show the macOS Keychain as a password source.
- **`flow` heads its `-to` and `-from` table columns in the singular.**

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores | Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ---: |
| Unit tests     |   8368 |        |            |     100% |    14 | 1:15 |
| User stories   |    147 |        |          2 |          |     3 | 1:43 |
| Security audit |     23 |        |            |          |     1 | 0:16 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
