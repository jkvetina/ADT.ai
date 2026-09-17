- **`patch` never drops or re-creates a table or a sequence.** A deleted file's drop is linked commented out, and a changed sequence ships an `ALTER SEQUENCE` for each changed clause. The new `immutables` key in `config/config.yaml` lists the protected types; `immutables: []` turns it off.
- **`export_db` writes a sequence without the `/` after its `;`**, so a patch carrying a new sequence no longer fails on `ORA-00955`. Exported sequence files lose that line on their next export.
- **`patch -create` skips commits an earlier patch of the same code already shipped.** The newest commit adding that patch folder marks the older commits as shipped. `-force` keeps them, and a build left with nothing stops with `NO NEW COMMITS FOR "<CODE>"`.
- **The `patch` template scaffold ships APEX examples switched off inside `/* */`** for installing supporting objects, switching the authentication scheme and setting the application version. Delete a block's `/*` and `*/` lines to use it.
- **`flow`'s `-refresh` mode stores a link target with the real application id** in place of `&APP_ID.`, so `f?p=&APP_ID.:7` in application 100 is stored as `f?p=100:7`.
- **The commit store is about half the size and faster to write**, and `rebuild`, `calendar` and `search_repo` read only what they need. An existing store is upgraded in place with every commit number kept.
- **The local stores drop columns nothing read back**, such as the application workspace id `export_apex` and `validate` carried and the run timestamp and line counts `ut` stored. Existing files are upgraded in place.
- **`repo_authors` in `config/config.yaml` maps a personal commit address to the company one**, so `calendar` and `search_repo` show one person, and `-by` and `-my` match either address.
- **Every warning prints under a `WARNING - <SUBJECT>:` header**, among them empty group rules and job arguments not exported in `export_db`, applications too old for `dependencies`, and a password `connection` may echo. Exit codes are unchanged.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores | Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ---: |
| Unit tests     |   8491 |        |            |     100% |    14 | 0:56 |
| User stories   |    151 |        |          2 |          |     3 | 1:35 |
| Security audit |     23 |        |            |          |     1 | 0:15 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
