# Changelog

All notable changes to the public ADT.ai release are recorded here, newest first.

## 0.7.1 - 2026-07-21

- **Added the `connection` module** for managing named SQLcl connections. Generated SQLcl scripts (including `export_apex -rest`) now connect through a named, credential-free SQLcl connection instead of embedding a username, password, or wallet path each time; a changed credential is detected and re-registered automatically. See `USAGE/connection.md`.
- **Fixed:** `export_apex -rest` against an Oracle wallet (OCI) could produce no output — wallet paths now resolve correctly regardless of where the generated script runs from.
- **Fixed:** `export_apex -rest` occasionally wrote a stray `Session altered.` line into generated REST files, corrupting the output.
- **Fixed:** `export_apex -component` help text could crash on some Python versions due to an unescaped `%` character.
- **Fixed:** `dependencies -app <range> -refresh` against several applications printed one flat combined listing instead of rendering results app by app, matching `export_apex`'s per-application section style.

## 0.7.0 - 2026-07-17

- **Breaking — `recompile`:** the `-target` alias is removed; use `-env`. The report actions `-mviews`, `-synonyms`, `-disabled`, and `-jobs` no longer carry their own `[NAME]` pattern — they are bare flags scoped by the shared `-name` and `-type` filters, so `adtai recompile -mviews DEP%` becomes `adtai recompile -mviews -name DEP%`.
- **Breaking — `dependencies`:** the query flags `-uses` and `-used-by` are renamed to `-from` and `-to`. `-unused` is removed — determining truly unused objects offline is not reliable and the mode produced false results. The derived `config/db_dependencies.yaml` artifact is no longer written; the local SQLite mirror at `config/dependencies.db` is the single source of dependency data.
- **Line endings are deterministic on every platform, and the `file_crlf` setting works.** Exported files previously took the host's native line ending, so a Windows run produced mixed CRLF and LF output, and every exported file differed from the same file exported on macOS or Linux, while `file_crlf` was carried in config but never applied. ADT.ai now writes LF everywhere by default and CRLF everywhere when `file_crlf: true`, including CSV row terminators. Raw LOB sidecars are the deliberate exception: they mirror stored database values byte for byte and are never translated. Flipping the setting never rewrites files whose content is unchanged, so there is no mass rewrite — files adopt the new ending on their next real change. Documented in `USAGE.md` under Line Endings.
- **Long-running queries no longer abort after 15 seconds.** A single timeout bounded both the connection attempt and every query round-trip, so any query running past 15s was killed. The two budgets are now independent and configurable in `config.yaml` as plain seconds: `connect_timeout_seconds` (default 15) and `query_timeout_seconds` (default 1200). Database connections also fail fast on an unreachable host instead of hanging for minutes, and authentication or listener failures surface inside the `CONNECTING TO SCHEMA` block rather than later at the first real query.
- **Added `recompile -trailing`**, which removes trailing whitespace from stored database source in place. `export_db` strips trailing whitespace from every line it writes, so an otherwise untouched package still differs from the database's stored source on every export; `-trailing` fixes the source side once per schema so the two match. It rewrites through `CREATE OR REPLACE` and lists each updated object as it goes — there is no preview mode, so scope it with `-type`/`-name`. An object with nothing to strip is never touched, a disabled trigger stays disabled, and wrapped objects are skipped. Follow a sweep with a plain `recompile`, since `CREATE OR REPLACE` invalidates dependents.
- **`recompile` and `export_db` now speak Oracle's object-type vocabulary.** `-type MVIEW` and `-type MATERIALIZED` both mean `MATERIALIZED VIEW`; a multi-word type may be spelled with a space, an underscore, or any casing (`PACKAGE BODY`, `PACKAGE_BODY`, `package_body`); and quoting no longer changes what a command means — an unquoted `-type PACKAGE BODY` is one filter rather than two. `SPEC` is the counterpart of `BODY`, so `-type PACKAGE SPEC` selects specifications. `-type PACKAGE` still means specifications only; ask for both with `-type PACKAGE%`.
- **`recompile` filters accept multiple values.** `-name` and `-type` take several patterns (`-name APP% CORE%`, `-name APP%,CORE%`, or a repeated flag). `-schema` is now repeatable and pattern-aware, matching `export_db`: `-schema CORE -schema APP` recompiles both, `-schema APP,CORE%` splits and expands, and `-schema %` covers every configured schema. Each schema is an independent pass and every schema runs even when an earlier one fails, so a broken schema cannot mask the rest. A bare `recompile` with no `-schema` now visits every configured default schema rather than only the first. `-disabled` honours `-type` as well as `-name`. `discovery -schema` deliberately stays single-valued and is documented as such.
- **Added `dependencies -age`**, an offline per-scope staleness report. Every `-refresh` stamps each refreshed scope's completion time, and `-age` reads them back with no database connection in `table`, `yaml`, or `md` format — so per-scope freshness is checkable without guessing from file timestamps.
- **`dependencies` query and refresh improvements.** An object name given without a `TYPE.` prefix now resolves instead of returning nothing. `-schema` works as an offline owner filter on the query modes, disambiguating a bare name on a multi-schema mirror. `-refresh -app` accepts multiple ids and ranges (`-app 100 200`, `-app 120-130`, `-app 300+`) and connects straight to the owning schema when it is already known. The default table output splits the old single `OBJECT` column into `OBJECT_TYPE` and `OBJECT_NAME`. Oracle-maintained schemas are never mirrored, and stale rows left by earlier refreshes are purged.
- **`export_db` gains `-by <AUTHOR>` and `-my`**, matching `export_apex`, so a shared schema worked by several developers through proxy users can still resolve who last changed each object. Authorship resolves against a project-defined `audit:` source in `config.yaml` — any DDL-log table or view — so no DBA privilege is needed.
- **Added `export_db -groups`**, an action that reorganizes already-exported files into per-group subfolders so a large object-type folder stays navigable. It previews every planned move and prompts before moving anything; `-dry-run` previews only. `export_db` also runs a duplicate-filename check before writing, so one object name cannot silently exist in two places under the same object-type folder.
- `export_apex -app <id>` connects straight to the owning schema when the app's owner is already known, skipping a wasted default-schema connection and a discovery round-trip.
- `flow -to`/`-from` query tables use uppercase, plural column headers to match the refresh summary. Row values and the Mermaid, DOT, and JSON outputs are unchanged.
- `rebuild`'s progress bar now aligns with every other progress line.
- **New config templates.** `config/me.sample.yaml` documents the gitignored per-developer identity file (`db_schema`, `apex_account`, `email`), and `config/STARTUP.sample.sql` replaces the tracked `config/STARTUP.sql` — copy it once and local session setup no longer dirties the checkout. Two behaviour knobs moved out of the code into `config.yaml`: `merge_batch_size` (rows per generated MERGE statement) and `dependencies_max_depth` (the transitive `-impact` traversal cap).
- **A flag with the same name now parses the same way on every command.** `search_repo -type` and `-name` accept multiple patterns, and `export_db -recent` / `search_repo -recent` accept the bare `-recent` form meaning one day — both matching the commands that already had them. No existing invocation changes meaning.
- Hardened a batch of smaller failure paths: malformed commit-cache files degrade to an empty cache instead of crashing or resuming a partial one, an unreadable project config warns instead of silently behaving as if there were none, a scheduler job argument with no anchor in its DDL warns instead of vanishing, and the `recompile` synonyms report no longer fragments a synonym holding both grantable and non-grantable privileges.
- Fixed `USAGE/discovery.md` misdocumenting `-nolog`, which suppresses report logging only — `-file` result write-back happens on every `-file` run regardless.
- Public commands unchanged: `export_db`, `doctor`, `dependencies`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.4 - 2026-06-26

- Cleaned up `doctor` output: online update checks for Java and Instant Client are no longer performed (both are system-managed), and a version-cache bug that could show a false `UPDATE` row after `git pull` is fixed. `doctor -init` now copies the project `.gitignore` from the ADT.ai root at runtime so new projects stay current.
- `export_apex` scoped exports (`-page`, `-component`, `-recent`, `-by`, `-my`) no longer update timing estimates in `config/apex_timers.yaml`. Fixed `-recent DAYS` without a format flag to print the report cleanly; fixed `-split -recent` so pages whose export ID differs from their page number are written correctly.
- Fixed several `export_db` output issues: the `add_if_not_exists` config flag now works (previously read but never applied), sequence DDL drops Oracle's default `MAXVALUE` clause to reduce noise, index-backed constraint folding now writes `.fix.sql` companions correctly, and constraint ordering within table DDL is deterministic.
- `export_data` now exports BLOB, CLOB, XMLTYPE, and JSON columns as sidecar files with standalone SQL import scripts, prints table names immediately on discovery instead of a silent pause, and adds `-silent` to suppress per-table progress rows. No longer creates or removes `.gitignore` files at runtime.
- `recompile` output improvements: `INVALID OBJECTS` table lists each object once with its ORA/PLS code and error count; `-mviews` accepts an optional name pattern and streams one row at a time; `-synonyms` shows compact per-owner tables with privilege and validity columns; `-disabled` and `-jobs` split into dedicated status-grouped tables.
- `dependencies` improvements: named-object refresh targets only matching objects; `-impact` output now includes APEX callers in all formats; `-unused` excludes objects with APEX callers; fixed `-refresh -app` for APEX versions before 24.2 and for workspace context setup; incremental refresh removes stale relations for dropped objects.
- Fixed `flow -refresh` on APEX 24.2, stale-row display in `-to`/`-from` output, and page-link component name display.
- Fixed `rebuild` error message for unknown `-branch` values. `search_repo` now recognizes the schema-first export layout (`<schema>/database/<type>/`) alongside the legacy layout.
- Public commands unchanged: `export_db`, `doctor`, `dependencies`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.3 - 2026-06-23

- Added the public `dependencies` command. It uses a local SQLite mirror at `config/dependencies.db` to answer `-uses`, `-used-by`, `-impact`, `-tree`, and `-unused`; refresh supports schema, app, forced, and named-object scopes, and writes `config/db_dependencies.yaml` as a compatibility artifact.
- Improved dependency refresh reliability and APEX compatibility: schema refresh is incremental, named-object refresh removes stale relations, APEX app refresh sets workspace/session context, APEX versions before 24.2 skip unsupported app scans, APEX 24.2 uses discovered safe `APEX_USED_*` columns, and APEX fetch failures complete progress rows before printing the database error.
- Added dependency graph output for foreign-key cascade inspection with `dependencies -tree CONSTRAINT_NAME`, including parent primary or unique-key rows and child dependencies where present.
- Updated `export_apex -recent` behavior: no-format `-recent` prints report-only output and exits; explicit recent split, readable, and embedded exports filter files correctly, use the normal progress/timer row, and keep timer training disabled.
- Changed `recompile` invalid-object reporting so normal recompile runs print compile-error details automatically in the `INVALID OBJECTS` section; the retired `-errors` flag is no longer needed.
- Cleaned shipped config, help, and runtime behavior: `config.yaml` only documents active public defaults, `-key` appears under common options, configured chimes stay silent for agent callers unless `-beep` is explicit, and database sessions set `DDL_LOCK_TIMEOUT = 10` before startup.
- Public commands now: `export_db`, `doctor`, `dependencies`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.2 - 2026-06-22

- Added focused `export_apex` component exports. `-page` accepts page IDs and ranges, `-component` accepts type-qualified wildcard filters such as `LOV:NAME%`, and scoped page/component requests default to split output when no explicit export format is passed.
- Improved scoped `export_apex` progress and timing behavior: partial page, component, recent, developer, and current-user exports now print the affected components directly and no longer retrain full-export timing estimates.
- Added `export_apex -recent DAYS -my`, which resolves the current Git identity against saved APEX developer metadata so recent-component reports can be filtered to the current developer. Multi-app recent reports keep the application inventory visible while skipping empty per-app detail sections.
- Simplified public command help pages with single-line summaries, clearer `USAGE/<command>.md` links, compact option wrapping, and ADT-style single-dash aliases in displayed help while keeping long aliases accepted by the parser.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.1 - 2026-06-22

- Added `recompile -disabled [PATTERN]`, a read-only health report for disabled constraints, invalid or function-disabled indexes, and disabled triggers. The report groups findings by object type in compact tables and can be filtered by object name pattern without running the invalid-object recompile flow.
- Added `recompile -jobs [PATTERN]`, a read-only scheduler health report for today's job runs. The report groups jobs by run status and shows compact job name, last-start, duration, and CPU timing columns, with optional job-name filtering.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.0 - 2026-06-21

- Extended completion sound controls across every public command. `-beep [theme]` now accepts an optional case-insensitive theme override for one run, bare `-beep` forces the configured sound theme or falls back to `chime` when sounds are disabled in config, and `-nobeep` suppresses sounds for a single run with priority over both config and forced sounds. Help, version, and other static screens remain silent.
- Changed the default exported database layout to the schema-first shape used by current project folders, and tightened `export_db` normalization around materialized-view logs, sequence defaults, and `add_if_not_exists` output.
- Changed `export_apex -recent [DAYS]` from report-only to report-and-filter for component-based exports: split, readable, and embedded exports now write only the recent components returned by the same recent-component query, and `-by DEVELOPER` narrows both the report and exported component set. Full app SQL, REST services, application files, workspace files, and `-reveal -recent` keep their existing behavior.
- Extended `export_data` large-value sidecars so BLOB, CLOB, XMLTYPE, and JSON payloads can be imported through generated SQL-only scripts beside the exported table data. The main MERGE SQL now prints visible SQLcl progress prompts for those payload scripts while keeping the scalar MERGE batched.
- Expanded `recompile` reporting and focused actions. `-mviews [PATTERN]` now targets materialized views without running the invalid-object recompile path, supports forced refresh of every match, streams each view row while work is happening, resolves refresh type to clean `F` or `C`, shows materialized-view log presence, and rounds dictionary timers up to a visible `Ns`. Compile errors are keyed by a stable ID with full messages printed below the table, locked objects are reported, and the new `-synonyms [PATTERN]` report lists synonym targets, privileges, grantability, and target status without changing database objects.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.5.3 - 2026-06-19

- Internal refactor: relocated the shared Git commit-discovery and commit-cache helpers into neutrally-named top-level modules and tidied the package layout. No user-facing change — every command behaves exactly as in 0.5.2.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.5.2 - 2026-06-19

- Added the `flow` command, which maps an APEX application's page navigation graph. `flow -app <id> -refresh` scrapes one application's navigation links from the database once and stores them in a local SQLite file, then `flow -app <id> -to <page>` and `flow -app <id> -from <page>` answer "what links into this page?" and "which pages can I reach from this page?" entirely offline. Edges cover page branches, buttons, list entries, tabs, navigation-bar entries, and report column links, each tagged by how resolvable its target is — a same-application page, a cross-application link, a runtime-dynamic target, or a link that leaves APEX. Every refresh also writes Mermaid, Graphviz DOT, and JSON diagrams of the graph. See [USAGE/flow.md](USAGE/flow.md).
- Public commands now: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.5.1 - 2026-06-19

- Hardened the toolkit through a repository-wide correctness and security audit. Correctness: `export_data` now guards its output-filename derivation for table names without a dotted schema prefix, and `discovery -file` scrubs only ADT-generated result blocks (leaving hand-written `/* … */` comments intact) and splits statements on top-level `;` only, so a semicolon inside a string literal no longer mis-splits a query. Security: connection configuration files are parsed with a safe YAML loader before any round-trip edit; SQLcl connect credentials are kept out of captured output and error messages; the SQLcl upgrade verifies the download host and is hardened against zip-slip extraction; TLS certificate errors are now surfaced instead of being silently routed around verification; generated SQL validates Oracle identifiers before emitting them; and the non-functional `ADT_KEY` password "encryption" is no longer presented in the docs as a working setting. Docs: removed dead README links, fixed stale command names, and dropped retired flag claims.
- Added id-range support to `export_apex -app`. Each `-app` value may now be a plain id (unchanged), a closed range `MIN-MAX`, or an open range `MIN+` (a minimum id with no upper bound), and the tokens combine freely: `-app 0-9999 -all`, `-app 0+ -all`, `-app 0-99 100-999 5000 -all`. When a range is present, matching applications are selected after an unfiltered scan; plain-id-only invocations are unchanged and still filter in SQL. Malformed or inverted range tokens raise a clear error and exit 2.
- Fixed two error-presentation gaps so every command path honors the console contract — print the banner, and hide raw Python tracebacks unless `-debug` is passed. Failures during CLI/argument setup now print the standard `APEX DEPLOYMENT TOOL: ERROR` banner, a concise one-line error, a "use -debug to show the Python traceback" hint, and the `TIMER` footer instead of leaking a traceback. In command dispatch, recognised database errors keep their dedicated screen while every other unexpected exception now prints an `UNEXPECTED ERROR` block with the same hint, keeping the already-printed banner and timer intact. `-debug` still re-raises the full traceback in every path.
- Fixed `export_data` sidecar handling for large and structured columns: BLOB, CLOB, XMLTYPE, and JSON values are now fetched and written under a table-named folder beside the CSV, using `<primary-key>.<column>.<ext>` filenames, while scalar columns continue to drive the CSV and the generated MERGE SQL.
- Changed `export_data` selection when `-name` is omitted so a bare `export_data` updates only tables that already have DATA files in the configured folder; an empty DATA folder now leaves the run empty instead of falling through to a wildcard export. Explicit `-name %` remains the all-matching-tables path.
- Changed `export_data` progress so the `EXPORT TABLE DATA:` header and the current table name print immediately after table discovery, giving real progress during large-table exports. Added `export_data -silent` / `--silent`, matching `export_db`: keep the banner, connection block, summary, and timer while suppressing per-table progress rows.
- Changed completion beeps so a forced `-beep` chime stays non-blocking and is triggered from the shared `TIMER` footer path, covering both success and error exits (including database-connection failures) across every public command. Command help screens remain static and silent.
- Fixed the bare `adtai` / `adtai --help` module overview to end with a trailing blank line, matching the per-command help screens.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.

## 0.5.0 - 2026-06-15

- Fixed the shipped `config.yaml` path defaults back to the documented database-first layout: `path_objects` is `database/<schema>/<object_type>/` and `path_apex` is `apex/<schema>/`. This matches the README, the `adt` skill, `USAGE/search_repo.md`, and how `search_repo` derives object paths; the interim layout could silently break `search_repo -type` and `-name` on newly exported files.
- Reorganized `export_db` DDL normalization into focused per-object-type modules (table, view, index, sequence, synonym, type, job) for clearer, more consistent formatting. No change to command usage.
- Split the CLI into a modular `cli_*` family (parsing, help, runtime, context) with `cli.py` as a thin facade. Internal restructuring only — all commands and options behave as before.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.

## 0.4.6 - 2026-06-14

- Fixed the database error banner: a query that fails *after* a successful connection now prints a `DATABASE QUERY FAILED` header, the offending SQL, and the database error message, instead of mislabeling it as `DATABASE CONNECTION FAILED`. The wallet/connection advice footer now appears only for real connection failures.

## 0.4.5 - 2026-06-14

- Refined the `export_apex` inventory listing (`-reveal`) so it reports only what the current connection can actually reach: workspaces are scoped to the schemas configured for the environment (via `apex_workspace_schemas`), and Oracle's reserved internal workspaces (`INTERNAL` and the `COM.ORACLE.*` namespace) are filtered out so only user-provisioned workspaces appear.
- Changed `-reveal` to collect all matching applications first and then derive the workspaces and owners summary tables from those actual results, so narrowing with `-app` or `-schema` narrows the summary sections too. Application-section headers now include the workspace name (`APEX APPLICATIONS: <WORKSPACE>, <SCHEMA>`).

## 0.4.4 - 2026-06-13

- Fixed a batch of `export_db` DDL formatting issues so exported files match a clean, readable layout on real `DBMS_METADATA` output: views (simple, compact comma-packed, mixed quoted/unquoted, and CTE select lists), expression indexes, table columns, `INTERVAL` suffixes, `INMEMORY` clauses, schema qualification, and TYPE / TYPE BODY drop preambles.
- Changed `export_db` to consolidate dedicated PK/UNIQUE indexes: when Oracle exports a primary-key or unique constraint as a separate `CREATE [UNIQUE] INDEX` plus `ALTER TABLE ... ADD CONSTRAINT ... USING INDEX`, the constraint is now folded back inline on the table so it reads as if the table were created in one clean statement. Table constraints are ordered deterministically — PRIMARY KEY, then UNIQUE, then FOREIGN KEY, then CHECK, alphabetically by name within each group — while column lines keep their source order.
- Added a `<table>.fix.sql` companion file beside any table whose index-backed constraints were folded inline. It holds the recovery script (`DROP CONSTRAINT` / `DROP INDEX` / `ADD CONSTRAINT`) that rebuilds the original dedicated-index arrangement, so the clean table export loses no information. The companion is regenerated when folding happens and removed when a table no longer has index-backed constraints.
- Changed `export_db` sequence DDL to drop Oracle's default ascending `MAXVALUE` (28 nines), matching how column DDL already strips it, while preserving explicit non-default maxvalues such as `MAXVALUE 999999999999`.

## 0.4.3 - 2026-06-12

- Changed Doctor's ADT.ai update check so non-git installs read the latest public release from `jkvetina/ADT.ai` on GitHub before falling back to PyPI.
- Kept Doctor read-only: update actions still require `doctor -update` or `doctor -sqlcl`, and `doctor -offline` still skips remote metadata.
- Corrected public help usage lines to show the installed `adtai` command name instead of the removed `adt-ai` entry point.

## 0.4.2 - 2026-06-12

- Corrected documentation and help text across the README and the usage index so examples, command references, and argument tables match the shipped command surface (the installed `adtai` command name, the real public options, and the per-command `USAGE/<command>.md` files).

## 0.4.1 - 2026-06-12

- Added the public skills index `SKILLS/README.md`, which explains that `adt` is the installed day-to-day skill for driving ADT.ai's commands while `adt-setup` is only for first-time setup and troubleshooting.

## 0.4.0 - 2026-06-12

- Added two repo-local skills so the tool is usable straight from a checkout: `SKILLS/adt` drives day-to-day command help and health checks, and `SKILLS/adt-setup` is a deeper install-and-troubleshooting checklist (covering Instant Client issues such as `DPI-1047` / a missing `libclntsh.dylib`).

## 0.3.0 - 2026-06-12

- Added four new commands — `recompile`, `rebuild`, `search_repo`, and `discovery` — each with its own `USAGE/<command>.md` reference.
- `recompile` recompiles a schema's invalid objects with an objects / invalid-objects overview, supports `-force`, `-scope`, and name filtering, builds the right `ALTER ... COMPILE` flags (native vs interpreted, optimize level, PL/Scope, warnings), retries in reverse dependency order on reconnect, runs a final re-check, and exits non-zero when objects remain invalid.
- `rebuild` builds a fast per-branch Git commit cache (one file per branch) with a count-first pass and progress ETA; its read-only `-reveal` branch inspector filters by name words, `-my`, and `-since` with a `-limit` cap, and can `-switch` the working tree to a listed branch; incremental runs resume from the cached tip so large branches refresh in seconds.
- `search_repo` searches Git history fast off the `rebuild` cache — by summary terms, file path, database object type/name, author, commit or branch, and date windows (`-since` / `-until`) — printing newest-first with optional changed-file rows, and can restore matched historical file versions.
- `discovery` is a safe, read-only `SELECT` explorer aimed at AI-assisted querying: a static validator accepts only a single `SELECT` per statement (rejecting DML, DDL, PL/SQL, multiple statements, and comment-smuggled commands), every accepted query runs inside a rolled-back `SET TRANSACTION READ ONLY` session, and results render to the console (`-nolog`) or to a per-run Markdown report.

## 0.2.0 - 2026-06-12

- Added the `export_apex` and `export_data` commands and shipped an MIT `LICENSE` so the public repo is safe to use and distribute.
- `export_apex` exports APEX applications in every format — full, split, readable, embedded, REST, application files, and workspace files, with `-all` running them together — using stable output paths and post-processing; its `-reveal` inventory lists matching workspaces and applications across every configured schema, persists application / developer / timing metadata for repeatable exports, reports recent component changes (`-recent`, `-by`), and can override `p_release` (`-release`) for upgrade recovery.
- `export_data` exports table data to CSV with configurable delimiters, ignored columns, and primary/unique-key row ordering, applies global and per-table `where` filters, and generates DATA MERGE SQL with batched insert / update / delete blocks.
- Shared connection handling improved for both commands: Oracle wallet zip archives are auto-extracted before connect, and database-connection failures print a concise, actionable message (with the full traceback available under `-debug`).

## 0.1.0 - 2026-06-12

- First public release, shipping the `export_db` and `doctor` commands.
- `export_db` exports an Oracle schema to a clean, version-controllable file tree — tables, views, materialized views, indexes, sequences, synonyms, types, packages, procedures, functions, triggers, jobs, grants, and comments — normalizing raw `DBMS_METADATA` output into a stable, readable layout that compares cleanly from one export to the next.
- `export_db` scope and filtering: `-type` and `-name` filters with `%` / `_` SQL-style wildcards and comma-separated values, `-recent` for recently changed objects, and multi-schema exports (default schema lists, comma-separated `-schema`, `%` schema patterns) into the database-first layout `database/<schema>/<object_type>/`.
- `export_db` repository hygiene: `-delete` clean exports, detection and removal of stale object files no longer backed by the database (dry-run stays read-only), in-place updates of nested subfolders, clean `Ctrl+C` handling, and a `-silent` mode for agent-driven runs.
- `doctor` runs local environment health checks for Python, Git, Java, SQLcl, `oracledb`, Instant Client, `PATH`, `JAVA_TOOL_OPTIONS`, and the ADT-compatible environment variables.
- Foundations shared by every command: external connection files and wallets resolved from outside Git (kept out of the repo), automatic wallet extraction before connect, and a consistent console contract — banner, connection block, progress, then a `TIMER` footer.
