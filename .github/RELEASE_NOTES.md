- **`export_db` no longer fails on Oracle 19c and other pre-23ai databases**: it asks the dictionary for the Data Grant views before reading them.
- **`export_db` writes wrapped packages, procedures, functions and type bodies exactly as the database holds them**, with no `;` added inside the encoded text.
- **`export_db`, `export_apex` and `export_data` with `-config-dir` on a folder without `config.yaml` leave the git files of the project alone**, so a CRLF project is no longer converted to LF.
- **`validate`: `codescan_ignore` works with SQLcl 26.3.1**, which renamed the rule key in its ignore settings.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  11962 |        |            |     100% |    14 |  1:28 |
| User stories   |    288 |        |            |          |     3 | 18:21 |
| Security audit |     22 |        |            |          |     1 |  0:25 |

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
