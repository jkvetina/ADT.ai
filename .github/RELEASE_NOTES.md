- **`dependencies` now names its mode, and `-refresh` is required rather than implied.** A bare `adtai dependencies`, a mistyped query, or `-schema`, `-app`, `-force` and `-recent` on their own all used to connect and rebuild the mirror. An invocation naming no mode is refused before it connects.
- **`dependencies -scan` asks an APEX application whether its stored SQL still compiles.** No patch, no deploy, and it writes nothing at all. `-page` narrows the findings and recomputes the verdict, saying how many are shown. `ERROR`, `FAILED` and `EMPTY` exit `1` so a pipeline can gate on it.
- **A second component scan of the same application works.** The scan re-inserts application-level rows a unique key already holds, so a repeat run answered `ORA-00600` or `ORA-00001`. Every scan clears the dictionary cache first, which fixes `patch -deploy` verification and the `dependencies -refresh` APEX axis too.
- **`export_db` exports the four object types Oracle 26ai adds.** SQL domains, property graphs, MLE modules and MLE environments each get a folder, a `-type` vocabulary entry and a patch group. `DBMS_METADATA` serves none of them, so each is assembled from the dictionary and replays as measured.
- **`export_db` exports a 23ai assertion.** A multi-table constraint belongs to no table, so every table it constrains exported clean while the rule itself was nowhere in the repository. `assertions/` drops before it creates, keeps the condition byte for byte, and replays identically.
- **`patch -create` asks Oracle for a table change instead of parsing the columns itself.** The old splitter compared column text only: a primary key, unique, foreign key or check could be added or removed and the patch shipped no statement, and a missing trailing comma reported a `MODIFY` nobody made.
- **A `-create` that selects no commit says which of its two failures happened.** `NO COMMITS MATCHED` quotes the pattern it really ran and counts the commits that passed every other filter; `NO COMMITS FOUND` is kept for a run that reached nothing. Both close on two numbered options.
- **A `patch` carrying a workspace static file can be deployed at all.** Four defects sat in that one path: the wrong schema group, the application procedure used for a workspace file, a snapshot missing its `begin`, and an `updated_on` guard that passed unconditionally because no workspace was set.
- **`patch -deploy -continue` now reaches the post-deploy scan.** The verdict is still computed and logged; the status, receipt and exit code report `SUCCESS`, nothing is reverted, and a waived row says so. `-continue` is part of the run fingerprint, so a waived receipt cannot satisfy a later plain deploy.
- **The deploy says where it left an application's build status.** `BUILD STATUS:` rides the scan's own row with this deploy's three moments, and an application nothing locked prints no row. `VERIFYING APPLICATIONS:` rows carry the filename alone, and a revert row reads `RESTORED` or `FAILED`.

## Verification

Build verified by 7564 private unit tests over 14 cores in 0:49 with 100% code coverage.

Release evidence: 98 user stories passed, 0 failed, and 2 unverified (Windows related).

The maintained private test suite is available with the existing [Company Sponsor membership on Buy Me a Coffee](https://buymeacoffee.com/apexdeploymenttool).
