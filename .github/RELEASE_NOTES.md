- **`patch`: `-deploy -app` on APEX 26.2+ imports only the changed `.apx` files into the same application**, and `-deploy -app 0` lands the tree on a working copy of the application instead.
- **`patch`: `-deploy -app` holds the target with the App Builder application lock on APEX 26.2+ instead of RUN_ONLY**, and with page locks on an older APEX where a DBA has granted them.
- **`patch`: `-create` and `-deploy -app` warn when the target runs a newer APEX than the APEXlang tree was exported on.**
- **`validate -codescan` runs the SQLcl codescan over the exported code with a baseline ratchet**, and `patch -create` scans the files the patch carries. `codescan_rules` picks the rules reported, `codescan_ignore` suppresses a finding with its reason, and a new finding names who last changed its file.
- **`doctor` names the SQLcl 26.3 that codescan needs** once a project gates on codescan, and says how to upgrade.
- **`export_apex`: `-apexlang` keeps the translations of a translated application on APEX 26.2.** `-max_app_id` and `-mirror` are removed; `-app 0-10000` now also bounds the counts `-reveal` prints.
- **`export_db` exports 26ai Deep Data Security Data Grants and Data Roles.**
- **`validate` names every Static ID holding a Tab**, which APEX 26.2 will not save.
- **`rebuild -app` writes the whole application flow as one CSV.**
- **`ut` lists type bodies, procedures, functions and triggers in an `OTHER UNITS:` block of their own.**
- **A drift refusal names the fetch to run first.**

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  11072 |        |            |     100% |    14 |  3:05 |
| User stories   |    256 |        |            |          |     3 | 31:20 |
| Security audit |     21 |        |            |          |     1 |  1:50 |

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
