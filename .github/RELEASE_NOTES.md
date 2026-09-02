- **`export_data` writes values Oracle reads back unchanged.** RAW travels as hex through `HEXTORAW`, NUMBER is fetched exactly and written digit for digit, and DATE and TIMESTAMP carry their own format model, so a MERGE no longer depends on the session's NLS settings.
- **A filtered `export_data` run no longer deletes the sidecar files of rows it did not select.** Pruning removed the LOB files of every row a `where` predicate excluded. A filtered run now deletes nothing, because it cannot tell a dropped row from an unselected one.
- **MERGE keys survive NULLs and identity columns.** A unique-constraint key joins NULL-safe instead of re-inserting every NULL-keyed row on each replay, and a `GENERATED ALWAYS AS IDENTITY` column is joined on but kept out of INSERT and UPDATE, which Oracle refuses.
- **`export_apex` refuses a REST export that failed halfway.** SQLcl exits successfully when `rest export` fails after some modules printed, and the truncated transcript was written as a module file. A failed export now writes nothing at all.
- **The `-apexlang` and `-embedded` sweeps delete only what they wrote.** Both passed no extension filter, so any file under those folders the run did not produce was removed, including notes kept beside an export.
- **An APEX application name becomes one safe folder name.** A name is free text, so `ORDERS/23` silently nested a folder and `ORDERS:23` broke Windows checkouts. Reserved characters collapse to underscores and a token resolving to nothing stops the export.
- **`dependencies -refresh -app` runs on APEX 26.1.** A version gate treated the reshaped `APEX_USED_*` views as ending at 26.1, so a 26.1 instance fell back to the older query and died on `ORA-00904`. The 24.2 shape is a floor now, with no ceiling above it.
- **A patch survives an apostrophe in an object or owner name.** Signature rows, lock rows and the APEX drop script spliced names into single-quoted PL/SQL unquoted, so a name like `IT'S_PKG` ended the literal early and broke the block.
- **`patch -create -search TERM` opens no database.** The search is a discovery run, but the dependency refresh ran before the gate that decides it, so a stale mirror still cost a connection and a rewrite.
- **`recompile` reports the cause and finishes the work.** A mutual invalidation yields a root cause instead of an empty `ROOT CAUSES:`, the retry repeats until a pass resolves nothing new, and each knock-on is counted under one root.
- **A scoped `recompile` no longer calls an invalid object missing.** A `-type` or `-name` run could not see the object that broke its target, so it advised restoring one already there. Bare `-force` also compiles each object once rather than twice.
- **A `ut` suite that cannot run is one red suite, not a lost run.** An exception mid-run discarded every suite that had already finished. Two tests sharing a description now keep their own verdicts, and a disabled test prints the reason it was disabled.
- **`search_repo -stage` never overwrites uncommitted work.** A restore with staging replaced the working copy of a file holding local edits; a dirty target is refused and listed under `COULD NOT RESTORE:`.
- **`doctor` always comes back.** Its version probes ran with no timeout; a wedged one is reported as a row after ten seconds instead of hanging behind a half-drawn table.
- **Smaller, cleaner packaging.** The source distribution ships the package and its documentation without tests, scenarios or coverage output, and the whole package is checked by strict mypy rather than thirteen files.

## Verification

Fun fact: build verified by 6757 private unit tests over 14 cores in 0:38 with 100% code coverage.

Release evidence: 73 user-story stories passed, 0 failed, and 2 unverified.

The maintained private test suite is available with the existing [GitHub Sponsors Company tier](https://github.com/sponsors/jkvetina).

The public edition covers export, validation, dependency analysis, and repository-history tooling.
