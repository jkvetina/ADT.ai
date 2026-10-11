- **`connection`: an `ords:` key reaches the database over HTTPS when the listener port is blocked**, so `export_apex`, `export_db` and `patch` keep working behind a firewall.
- **`export_apex` and `patch`: the documentation now points to the ORDS connection page.**

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  10975 |        |            |     100% |    14 |  3:00 |
| User stories   |    257 |        |            |          |     3 | 26:19 |
| Security audit |     21 |        |            |          |     1 |  1:29 |

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
