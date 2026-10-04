- **`patch`: `-create` writes a table's and a sequence's ALTER to `tables_before/`**, and every script in `tables_after/` runs after the table rows, a hand-written `ALTER TABLE` included.
- **`patch`: `-deploy` refuses a full or split APEX install onto an application changed after the patch was built**, and the `patch_signatures` setting is now `deploy_live_check`.
- **`patch`: `-create` prints its screen as it builds**, and `PATCH FILES:` lists `DEPLOY.sql` first, then the install scripts in the order it runs them.
- **`patch`: `-upload -folder` refuses a folder that does not exist before it connects.**
- **A command that streams its screen no longer loses the output of a child that exits first**, so a fast run keeps its transcript.
- **`recompile`: `-silent` keeps the errors**: `INVALID OBJECTS:`, `ERROR - RECOMPILATION FAILED:` and `ROOT CAUSES:` still print, and only the overview is dropped.
- **`recompile`: `-warnings ALL` enables every warning category**, any other unknown value is refused before the run connects, and `-disabled -type TRIGGER` prints only the disabled triggers.
- **`export_apex`: `-by` and `-my` without `-recent` export only the components that developer last changed.**
- **`export_db`: `-delete` beside `-type` or `-name` deletes only the files those filters match**, and scheduler programs export to `job_programs/` with every job bringing its program and schedule.
- **`export_data` keeps the offset of a `TIMESTAMP WITH TIME ZONE`** in the CSV and in the MERGE.
- **`search`: `-since` and `-until` refuse a value that is not a date before reading any history**, `-from` names a materialized view as such, and a one-object `-data` list reads in the singular.
- **`validate` and every scan-based command leave another session's `DEPSCAN$` helpers alone**, so two scans at once no longer fail each other.
- **`connection`: `-create -default` writes `schema_db` beside `schema_apex`**, and the `CONNECTION FILE NOT FOUND` refusal adds a third remedy.
- **The `discovery` and `doctor` documentation states what the commands do**: comments are read past, not refused, and `adtai doctor` gives no hint for `adtai update` or `adtai upgrade`.

## Verification

| Suite          | Passed | Failed | Unverified | Coverage | Cores |  Time |
| -------------- | -----: | -----: | ---------: | -------: | ----: | ----: |
| Unit tests     |  10647 |        |            |     100% |    14 |  4:05 |
| User stories   |    247 |        |            |          |     3 | 17:29 |
| Security audit |     21 |        |            |          |     1 |  7:17 |

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
