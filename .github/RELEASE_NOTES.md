- **`patch` ships.** It turns committed repository changes into a release you can deploy to the next environment: the files a window of commits names, collected into ordered install scripts, deployed through SQLcl, and archived afterwards. Building a patch deploys nothing, and every write needs an explicit flag.
- **A patch refuses to deploy over work it has never seen.** Each signable object carries the signature it was built against and the one it ships, and a block in the install script stops on a third version. Where CORE_LOCKS is installed the objects are locked for the run.
- **A deploy asks the application it just landed whether its own SQL still compiles.** Stored SQL and PL/SQL fragments that no longer resolve are reported per page and fail the run, which an install script reporting `SUCCESS` never did.
- **The whole repository was audited by GPT-6 Astra at Extra High effort and then by Fable 5.1 at Max effort.** Every P1 and P2 finding from both rounds is implemented in this release.
- **`export_data` exports the geometry a spatial column holds.** An `SDO_GEOMETRY` column wrote a Python memory address into the CSV and the generated MERGE. The shape now travels as `SRID=4326;POINT (...)` and reloads through Oracle's own constructor; the hidden attribute columns beside it are no longer exported.
- **A CSV cell a spreadsheet would run as a formula is neutralized on the way out.** `export_data` prefixes a cell opening on `=`, `+`, `-` or `@` with an apostrophe, and the MERGE that reloads the file strips it again, so the round trip is lossless.
- **`doctor` refuses a download URL that is not https, before any bytes move.** The SQLcl link is scraped off Oracle's page rather than written down, so the check now sits where the request is built. A refusal names the URL it refused.
- **`ut` refuses a utPLSQL JUnit report that declares a DTD.** Unbounded entity expansion and external-entity retrieval both begin in the doctype, so refusing it closes the class rather than one case. A refused report reads as an error, never a pass.
- **`dependencies` cleans up the `DEPSCAN` procedures its APEX scan generates, even when the scan fails.** Install, scan and cleanup sit behind one boundary with the cleanup in a `finally`, so a failed refresh cannot strand generated helpers on the target schema.
- **Every module package opens on the project signature.** `calendar`, `connection`, `discovery`, `export_apex`, `export_db`, `flow`, `rebuild`, `recompile`, `search_repo` and `validate` now carry the MIT licence line, the repository URL and the copyright above their own docstring.
- **Three scanners run against the tree on a schedule, and every finding carries a written verdict.** Bandit, Semgrep and pip-audit run beside the suite; an unruled finding blocks the release, and each accepted one is recorded with the reason it was accepted.
- **Every job in the public CI workflow declares its own permissions.** All three inherited a read/write token to do work that only reads a checkout; each asks for `contents: read` and nothing else now.
- **`docs/why.md` opens on its own illustration.** It was the last shipped documentation page still starting on prose.
- **Upgrading: a patch already deployed under an earlier version runs once more.** Skipping a completed target now needs a deployment receipt, which those runs never wrote, so the first deploy after the upgrade executes the patch again instead of skipping it.

## Verification

Build verified by 7071 private unit tests over 14 cores in 1:46 with 100% code coverage.

Release evidence: 86 user-story stories passed, 0 failed, and 2 unverified (Windows related).

The maintained private test suite is available with the existing [GitHub Sponsors Company tier](https://github.com/sponsors/jkvetina).
