- **ADT.ai installs from PyPI.** `pip install adt-ai` is the install, and `pip install --upgrade adt-ai` the upgrade. Cloning the repository and installing that checkout stays documented as the route for working on ADT.ai itself. The README, `SETUP.md` and the setup skill all lead with the package.
- **Releases ship one immutable artifact set, published without a stored token.** A tag builds the wheel and sdist once, records their digests beside a `SHA256SUMS` file and a CycloneDX SBOM, attests their provenance, and re-derives every digest at each later stage. Publishing is PyPI trusted publishing over OIDC.
- **The exact released wheel is installed and exercised on Linux, macOS and Windows before it ships.** Each clean-room job runs `pip check`, `doctor -init` and `doctor -offline`, requires all eleven scaffold files, and compares the packaged patch-template resources against the wheel. The supported floor is Python 3.14 or newer.
- **Repository governance and package metadata are complete.** `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, a pull-request template and two issue forms ship with the release; a private advisory is the only security route. Package metadata carries authors, keywords, project URLs and accurate classifiers.
- **`flow -refresh` resolves the APEX schema itself.** A connection whose `defaults` are empty no longer stops with `Default apex schema not configured` before its first query; the command works out which schema to read the APEX inventory through, as `export_apex -reveal` already did.
- **Runtime state, parsers and credentials fail closed.** Impossible import, console, session and reader states raise typed errors even under `python -O`; Oracle failures and SQLcl JSON are accepted only across exact boundaries; and connection secrets stay masked with one audited plaintext boundary before encryption or a private-file write.
- **Every SQLite store follows one convention, and each has a documented schema.** The five stores share one opener, one version table and one set of naming and timestamp rules, and existing files are migrated in place. Seven pages under `docs/storage*.md` carry an ER diagram and a column table each.
- **`ut` reads coverage for every utPLSQL source type.** Package bodies, type bodies, procedures, functions and triggers are all collected and kept, keyed by type and name. Nothing on screen moves: a printed figure still describes the packages a run's suites test, and `docs/ut_coverage.md` says why.
- **New documentation pages.** `docs/why.md` makes the case for adopting ADT.ai command group by command group, and `docs/apex_round_trip.md` walks the APEX loop end to end with a runnable command per step, every transcript captured from a live run.
- **The Windows script transport is measured rather than assumed.** A hosted Windows job puts the production SQLcl runner and its live reader in front of a real SQLcl, so the pipe transport, the end-of-input guarantee and the live progress rows rest on a recorded run rather than one report.

## Verification

Fun fact: build verified by 6277 private unit tests over 14 cores in 1:16 with 100% code coverage.

Release evidence: 68 user-story stories passed, 0 failed, and 2 unverified.

The maintained private test suite is available with the existing [GitHub Sponsors Company tier](https://github.com/sponsors/jkvetina).

The public edition covers export, validation, dependency analysis, and repository-history tooling.
