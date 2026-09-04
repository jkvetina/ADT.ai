- **A table's MERGE mode is a config entry now, and a typo in one stops the run.** Per-table modes move to a top-level `merge_tables` map, `tables_global.merge` stays the default, and an unknown key or a `merge` block left under `tables:` fails on `CONFIGURATION INVALID`.
- **`file_empty_lines` decides how an exported file ends, and every object type now agrees.** Object files, table `.fix` sidecars and the grants file all close the same way, `0` closes flush, and the default is one line. A table file gains that line on its next export.
- **`export_apex` works on APEX 24.1 again.** The export block passed a parameter that only arrives in 24.2, so 24.1 raised `PLS-00306` before a row was read. It was only ever passed as its own default, so nothing moves on 24.2 and later.
- **A connection with no `schema_apex` infers it from `schema_db`.** A file naming only the database schema stopped on `CONFIGURATION NOT FOUND` the moment anything asked for the APEX schema, then listed that same schema underneath. A present key is never overridden.
- **A materialized view can finally be installed from its exported file.** Every materialized view and log ended `;` and then `/`, which submits the statement a second time and fails with `ORA-12000`. Only that family drops the terminator; views, synonyms and types keep it.
- **Your own index on a materialized view still exports.** The exclusions that skip Oracle's generated container and log objects are anchored on the names Oracle reserves, so a hand-written index goes out with the view and installs after it.
- **`export_db` stops writing the two tables Oracle builds for a materialized view, and the two indexes under them.** The container is identified by asking `user_mviews`, the log and its indexes by the `MLOG$` and `I_SNAP$` prefixes. A `-name MLOG$_EMP` filter answers `EXPORTING 0 OBJECTS:`.
- **`keep_view_column_names` keeps the declared column list on a view or materialized view.** The default still strips it, byte for byte as before. Set it and the list after the object name survives, with the select list still reflowed.
- **A file you renamed keeps your casing inside it, not just on it.** Every generated spelling of the object's own name now follows the file: the definition and `CREATE` lines, the `COMMENT ON` lines, and the `.fix.sql` companion.
- **`export_data` writes a JSON column holding anything but text.** One number inside a JSON document ended the whole run on `TypeError`. Numbers, binary, dates and booleans render as JSON's scalars, and a fraction that cannot survive the round trip raises rather than writing a wrong value.
- **Every MERGE mode and both LOB types are measured against a real database.** Eight tests read the generated SQL for each flag combination, and a PNG and a 30 KB template are decoded back out of the generated script and compared byte for byte with what went in.

## Verification

Fun fact: build verified by 6880 private unit tests over 14 cores in 0:44 with 100% code coverage.

Release evidence: 85 user-story stories passed, 0 failed, and 2 unverified.

The maintained private test suite is available with the existing [GitHub Sponsors Company tier](https://github.com/sponsors/jkvetina).

The public edition covers export, validation, dependency analysis, and repository-history tooling.
