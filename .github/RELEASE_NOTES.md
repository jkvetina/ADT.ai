- **`patch -create` links every group in dependency order**: a foreign-key child follows its parent, a view follows the function it calls, and a child table's rows follow its parent's.
- **`patch -app` takes a `#` for each application's own id**: `-app #6000` lands 122 on 1226000 and 123 on 1236000, and `-app 6000#` lands them on 6000122 and 6000123.
- **`patch` hash mode takes a deployed deletion out of the baseline**, keeps the DROP for an object deleted, re-added and deleted again, and writes no DROP for a deleted DATA script.
- **`export_db` writes interval-partitioned tables and quoted column comments that re-create**, and a `-recent` window lists a refused object again until a run writes it.
- **`export_data` writes a MERGE only on a key that names the same row everywhere**, never on an identity or nullable key, stays valid with insert and update both off, and uses the configured file extension.
- **`doctor` masks the value of any secret-named JVM property** in `JAVA_TOOL_OPTIONS`, quoted values included.
- **`connection`: `-set-wallet-pwd` removes every competing wallet password command** and says so in its preview.
- **`ut` fails a run whose report leaves out a discovered test**, and `validate -scan` exits 1 when `-page` matches no page.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  10389 |        |            |     100% |    14 |  2:10 |
| User stories   |    191 |        |          2 |          |     3 | 18:32 |
| Security audit |     25 |        |            |          |     1 |  1:08 |

The 2 unverified user stories are Windows-only contracts, and every release is built on macOS.

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
