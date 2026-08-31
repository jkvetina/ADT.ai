# Changelog

All notable changes to the public ADT.ai release are recorded here, newest first.

## 0.9.5 - 2026-08-31

- **`export_db` and `export_data` can preserve owning schemas.** New opt-in `keep_owner` qualifies generated DDL, DML, grants and directories consistently while the default remains schema-neutral. Export destinations are confined to the project, writes are atomic, permissions and line endings are preserved, and unchanged files are not rewritten.
- **`export_apex` and `validate` fail closed around files and resources.** Unsafe output paths and unrepresentable names are rejected, owned database and SQLcl resources close on errors, and generated artifacts are atomically replaced without disturbing identical content.
- **`doctor` and `connection` tighten installation and credential handling.** Installed wheels can initialize projects without a source checkout, package updates use the active Python interpreter, SQLcl upgrades roll back on failure, child processes do not inherit ADT secrets, and connection files remain owner-only.
- **`discovery` preserves its read-only boundary under malformed input.** SQL-aware parsing now handles comments, quoted strings and Oracle q-quotes consistently during execution and file write-back; each transaction is rolled back even when setup fails, and generated reports use atomic writes.
- **`calendar`, `rebuild` and `search_repo` reject ambiguous Git history.** Truncated or malformed Git batch responses fail instead of returning partial records, each cache records its owning branch, legacy YAML history paths are refused with migration guidance, and branch-derived filenames accept only the documented safe form.
- **`ut` `-compact` summarizes the run in one `RESULTS` row.** `recompile`, `validate` and `flow` also inherit deterministic store, stream and descriptor cleanup, so failures cannot leave owned resources open.
- **Documentation is easier to scan and safer to publish.** Every shipped command page now has its own illustration, the index is sorted, oversized topics are split onto focused pages, and local SVG documentation assets are gone. The README reports the measured test count, cores, duration and coverage for this build.

## 0.9.4 - 2026-08-27

- **Breaking: `SKILLS/` is now `skills/`.** It was the only uppercase folder beside `config/`, `connections/`, `docs/` and `src/`, and lowercase is what agent tooling expects of a shipped skill folder. Every external link to `SKILLS/adt/SKILL.md` has to be rewritten, GitHub redirects nothing.
- **Breaking: `docs/arguments.md` is now part of `docs/console.md`.** The shared flags are console surface, so what each one does sits on the same page that says where it appears on a help screen and in what order. Every command page links the merged section.
- **Breaking: `images/` is now `docs/images/`.** The artwork sat one level above the pages that link it, so a documentation page and the README spelled the same folder two different ways.
- **`export_apex` found no APEX applications at all when the connection file spells the schema in lowercase.** The name was bound straight into a dictionary predicate, where Oracle stores it uppercase, so `-reveal` printed an empty screen and exit `0`. Both sides are compared case-insensitively now.
- **`export_apex -reveal` names a workspace the registry cannot show you.** A restricted schema may not read `apex_workspaces`, so the screen could list an application beside no workspace at all. The missing rows are filled in from the applications themselves, with a blank developer count rather than a zero.
- **`apex.workspace` in a connection file no longer narrows `-reveal`.** That screen exists to say what is there, so only `-ws` narrows it now. The key marks an `ACTIVE` column instead, and a workspace nothing matches is reported by name rather than silently emptying the screen.
- **The `-reveal` tables gain a column and lose a word.** The owner table carries `WORKSPACE`, so an owner with applications in two workspaces is two honest rows. The workspaces table carries `ACTIVE`, and both `APPLICATIONS` columns now read `APPS`.
- **`export_data` takes `-groups` and `-force`, the same way `export_db` does.** A table's CSV, its merge script and its sidecar directory move together as one unit, and a table already sitting in a group folder keeps landing there on every later export.
- **`export_db` no longer crashes exporting a DBMS_SCHEDULER `SCHEDULE`.** `DBMS_METADATA.GET_DDL` rejects that literal object type with `ORA-31600`, exactly the limitation `JOB` already worked around, so both are fetched through the same shared token.
- **The `GRANT` row in the `export_db` overview carries a count again.** It counts what the run writes, one for grants made, one per owner received, one for schema privileges and one for directories, the same formula every other row on that table uses.
- **`doctor -update` reaches any release.** A shorter version resolves to the newest on that line, so `-update 0.3` lands the newest `0.3.x`. Both tag spellings now work from a pip install as well as from a checkout, where only the checkout had the fallback.
- **`doctor -init` scaffolds `config/IDENTITY.yaml`, prefilled from the project folder's git identity.** A folder with no git identity gets a commented placeholder rather than an invented value, and an existing file is skipped without `-force` like every other scaffold entry.
- **`doctor` drops two rows it wrote about itself, the `HYDRATED` line and the version row's `+ WIP` marker.** Neither was ever part of the documented environment report. Hydration still runs and still fills in `ADT_ENV`, `ADT_KEY` and `ORACLE_HOME`, it simply prints nothing about it.
- **Every listing of database objects opens on a blank line under its dashed rule.** `export_db` had one and `recompile` did not, so the same section rendered two ways depending on which command happened to draw it.
- **The shared-options footer on every command page promised flags the command rejects.** Nine of the sixteen pages advertised between two and five arguments their own parser answers with an error. That sentence is derived from the parser now.
- **Every reference in the documentation is a link, and the index links every page the release ships.** Six pages were reachable from nowhere on the published site, and five real links had been flattened to plain text by the release build itself.
- **The escape character for a literal `_` or `%` in a pattern is documented, on every page whose flags take one.** Writing `-name "APP\_%"` matches a real underscore. The mechanism was always there and had never been written down.
- **The bundled `adt` skill stops teaching commands the release does not ship.** Every published tree since v0.8.7 carried a full section for a command that is not in the package, over documentation and source that are not there either.
- **The README loses the recording and the text transcript of that same run, and the Install walk ends on linked next steps.** Three more documentation pages open on their own illustration, and the index gains the one it was alone in lacking.

## 0.9.3 - 2026-08-24

- **Breaking: in `search_repo`, `-type`, `-name` and `-by` are SQL LIKE patterns.** `%` and `_` mean what they mean on every other command, and the match is anchored. `-type PACKAGE` no longer returns package bodies, `-type "PACKAGE%"` does, and a partial author address is written `-by "bob%"`.
- **Breaking: `search_repo` `-recent N` covers N whole days, today included.** It compared against `today - N` and so reached one calendar day further back than every export command's `-recent`. Sub-day windows are unchanged.
- **Breaking: `-schema APP_%` reads `_` as SQL LIKE's single-character wildcard.** It was a literal underscore, the one filter in the tool that disagreed with all the others. A `*` is still accepted.
- **Every command was broken on Windows, and is fixed.** The optional SQLcl-only transport imported Unix-only modules at load time, so `adtai --help` died with `ModuleNotFoundError: No module named 'termios'` on any Windows install, whether or not the project ever turned that transport on.
- **`sqlcl_only` runs on Windows.** SQLcl draws no prompt in a Windows console, so each statement runs as its own script with its own connect. `export_apex`, and `ut` when it measures coverage, need one session across statements and refuse there by name rather than returning nothing.
- **`export_apex -rest` handed back a Java stack trace instead of the REST export on Windows.** The child process was given `NUL` as its input, which cannot answer the call SQLcl's console makes at startup, so SQLcl died and still exited zero.
- **A SQLcl run that died before its script started is an error, not an answer.** The failed transcript used to be written into the export as though it were the data.
- **`doctor -update` takes a version.** `adtai doctor -update 0.9.1` lands exactly that release, in either direction, with no confirmation. Requirements and SQLcl still update after it, and a version with no release leaves the checkout where it was.
- **`validate` reports a compile error as an error again.** SQLcl 26.2.1 spells one marker `APEXlang Compile Errors:` where 26.1.2 spelled it `APEXLang`, and the match was case-sensitive, so every real finding was reported as output the parser could not read.
- **`export_data` honours the `{folder, extension}` spelling of `object_types.DATA`.** It accepted only the two-item list and fell back to a hardcoded `data/` and `.sql`, so a project using the mapping form had its data written where nothing else looked for it.
- **`export.prefix` and `export.ignore` split on commas the same way for `export_db` and `export_data`.** `ignore: ['TMP_%,BAK_%']` was one pattern on one command and two on the other, so the filter silently matched nothing. An empty value now means no filter on both.
- **A comment inside exported DDL no longer breaks the scan around it.** A semicolon in `-- add the index; then grant` split a `CREATE TABLE` in two, and an apostrophe in a comment flipped the quote state. The `export_db` normalizers and `discovery` read one tokenizer now.
- **`apex_path_app` refuses a token it cannot resolve.** Writing `'{$APP_ID}_{$APP_VERSION}'` created a folder literally called `100_{$APP_VERSION}`; it is an error now, like the other two path templates.
- **`config/IDENTITY.yaml` answers who you are for git as well.** Its `email` and `apex_account` keys were documented and unread. `search_repo`, `rebuild`, `calendar` and `export_apex` read them for `-my` and `-by` now, falling back to `git config`, and `export_apex -my` matches workspace developers on `apex_account`.
- **A store that cannot set up its schema closes the connection it opened.** A stale or truncated cache file connects fine and fails on the first statement, so the APEX, commit and `dependencies` caches each leaked a connection the caller was designed to swallow the error from.
- **Console lists of files group under their folder.** Rows sit under a folder line, indented one level further, instead of repeating the same path prefix on every row. `nested_files: False` in `config.yaml` restores the flat list.
- **Every listing of database objects prints one shape.** `export_db` and `recompile` drew the same type-and-name row three different ways; names pad and a separator closes each type group on all of them now.
- **An `export_db` run that changed nothing prints nothing under its header.** The overview used to draw two heading lines over no rows, then `EXPORTING 0 OBJECTS:` over nothing, then the timer.
- **Every section header opens on exactly two blank lines, and the first one on a screen opens on one.** The gap used to be a property of whatever printed above it, so one header could render one, two or three blank lines on different screens.
- **A file hash is a property of the content, not of the machine that wrote it.** Line endings collapse to LF and the payload is trimmed once before hashing, so the same object exported on Windows and on macOS hashes the same.
- **The documentation is sixteen pages on one shape.** Purpose, examples, output, topics, then arguments last. The flags every command shares live on one page instead of being restated sixteen times.
- **Five documentation pages are new.** `config.md`, `console.md`, `connection_passwords.md`, `export_db_layout.md` and `ut_coverage.md` take the topics that had been buried inside command pages running to five hundred lines.
- **Every transcript in the documentation came off a live database.** The pages show what the commands actually printed against a real schema rather than output written from memory, and three documented behaviours were corrected as a result.
- **The README stops being the manual.** Installation, a per-command reference table and the walkthroughs moved to the pages that own them.

## 0.9.2 - 2026-08-20

- **A windowed `export_db` exports every object type again, `JOB` and `MVIEW LOG` included.** Neither carries a change timestamp, so a run with `-recent` used to drop both in silence. Two scheduler jobs live on a database had never reached its repo.
- **A windowed run exports the scheduler jobs that changed, not all of them.** A content signature computed in the database tells an edit from a run, so `-recent` no longer re-exports two thousand jobs. An explicit `-type JOB` still exports the whole set.
- **An exported job file no longer switches on a job the database has switched off.** The template hardcoded an enable call and a priority attribute, so deploying it would have overridden two jobs disabled on purpose. The file now carries what the dictionary actually has.
- **`export_db -recent` dates an index by when it changed, not by when its statistics were gathered.** A nightly stats job moved indexes into the window that had no DDL at all, and an index never analysed dropped out of every windowed run.
- **`export_db -groups` on a folder two object types share no longer plans the same file twice.** `packages/` holds specs and bodies, and the shorter extension is a suffix of the longer one, so a run could die renaming a file it had already moved.
- **`export_db -groups A B -force NAME` lands every named prefix in one folder.** The name overrides the group for every prefix the run listed, across every object type they reach. Bare `-force` is unchanged, and a name beside auto-detecting `-groups` is refused.
- **`export_db` announces its `GRANT` exports only on the runs where a grant moved.** The row was appended from what the config selected rather than from anything that changed, so an export of an untouched schema printed a table holding one line. Every file is still written.
- **`<SCHEMA>` in a path template writes the schema folder uppercase.** `path_objects` and `path_apex` carry their own case, so `export_db`, `export_data` and `export_apex` all follow what the template spells. A repo migrated from old ADT keeps `APP_OWNER/`.
- **An unresolved `<token>` in a path template is refused instead of exported as a folder.** A typo used to build a real directory named after it and collapse every schema into one tree, two silent failures from one character.
- **`doctor` reports a folder tree whose case disagrees with the template, and never renames it.** The read-only `ACTIONS:` section gains a row per mismatch with the `git mv` that fixes it. A repo-wide move is reviewed and committed by a person.
- **`ut` reports coverage for a suite whose name does not match a package.** A derived name that is no package now falls back to the longest package it prefixes, so one measured module moved from 40.7 % to 45.7 %. A name resolving to nothing reads `?`.
- **`COVERAGE CHANGED SINCE LAST RUN:` compares against the last run that was different.** Running `ut` twice over unchanged code recorded identical figures, so the table a reader opens after a deploy was always empty. A `-name` run now keeps its own history.
- **A progress row whose label is wider than the grid is trimmed to it.** The middle of the label goes rather than its tail, the tail being what tells two rows apart. Every module that streams a labelled row inherits the fix.
- **`dependencies` stores one schema under one spelling, and heals a mirror holding two.** A schema named `ict_owner` in a connection file and `ICT_OWNER` on the command line filled the mirror twice, and the freshness gate then read whichever copy sorted last.
- **`dependencies -refresh` prints `RECOMPILING DUE TO WRONG PL/SCOPE` only when something is being recompiled.** The row used to open before the catalog scan, so a warm schema and every incremental refresh drew a bar reading `100%` over no work at all.
- **Every key in the shipped `config.yaml` explains itself, briefly.** All 67 were documented, five had grown past 400 characters, and three named a folder without saying what changing it moves. A contract test now measures both ends.
- **Two keys the code has always read now ship in the config file.** `rest_timeout_seconds` and the `audit:` block were documented and absent, so the only way to find either was to read the source. `audit:` ships commented out, its log table being a project's own.
- **Two shipped defaults move: the APEX page stamp and four more audit column names.** `apex_timestamps` goes to `'20260101000000'`, and `ignored_columns` gains `CREATED_ON`, `CREATED_DATE`, `UPDATED_ON` and `UPDATED_DATE`, so four more columns drop out of every generated CSV and MERGE.

## 0.9.1 - 2026-08-19

- **Breaking: `ut3` is now `ut`.** The command was named after utPLSQL's own version number. There is no compatibility alias, so a pipeline calling `ut3` has to be edited. The six `ut_*` config keys are unchanged, and the run history is taken over under the new name.
- **Breaking: `USAGE/` is now `docs/`, and `USAGE.md` is now `docs/README.md`.** Every help screen points at `docs/<command>.md`. GitHub renders the folder's own index, so the documentation opens where readers already look for it.
- **Breaking: `adtai export_db -dry-run` is withdrawn.** It printed the same object rows a real export does, with nothing saying no file had been written, so the only way to learn what it had done was to look in the folder. No other command has one.
- **Breaking: `export_db -groups` lists and moves nothing, and `-force` is what applies it.** The confirmation prompt is gone, so bare `-groups` is a report you read. `-force` moves exactly what the listing showed, and a group you arranged by hand is never touched.
- **The `-groups` listing reads as a grouping rather than a wall of paths.** `PLANNED MOVES:` names each target group with its files under it, A to Z, and `UNMATCHED (LEFT IN PLACE):` follows in the same shape. Naming a group narrows the screen as well as the move.
- **A connection file can hold no password at all.** `pwd_cmd:`, `wallet_pwd_cmd:` and `ADT_KEY_CMD` run a command of your own and take its output as the secret, so 1Password, HashiCorp Vault, `pass` or Azure Key Vault owns the credential, its rotation and its access log.
- **`auth: external` passes no credential to the database at all.** The Oracle client reads the login out of a Secure External Password Store, so the connect call carries neither a user nor a password and no decryption happens. The mode implies the thick client.
- **`sqlcl_only` keeps every database password out of the Python process.** Set it in the project config and every call is served by SQLcl, so the credential stays in SQLcl's own secure store and a connection needs no stored password. Slower per query, and off by default.
- **Stored secrets are salted and versioned, and a wrong key now says so.** A new encrypted value carries its own random salt and 600000 iterations. A fingerprint beside it separates a wrong key from a damaged value. Files already encrypted keep working and are never rewritten underneath you.
- **`adtai connection -rekey` moves a whole file onto a new encryption key in one pass.** It walks every environment, every schema and the wallet block. Previewing is the default, and nothing is written unless every secret decrypts, so a wrong old key leaves the file untouched.
- **A loaded credential renders as `***`.** Passwords are wrapped where the connection is constructed, so a debug print, an error message or a test runner showing local variables can no longer put one on screen. Three call sites read the real value, each handing it straight to Oracle.
- **A connection error names the thing you have to fix.** `CONFIGURATION INVALID:`, `CREDENTIAL UNAVAILABLE:`, `DATABASE CONNECTION FAILED:` and `CONFIGURATION NOT FOUND:` are four screens now, so a YAML typo no longer advises you to go and find the file it had just read.
- **`docs/connection_security.md` answers a security reviewer.** It separates what encryption protects, a file that leaks, from what it does not, a machine where other software can read what the tool reads, and it gives the longest section to the controls that are yours rather than ours.
- **`ut` remembers what it measured.** Every run is recorded, and under `-verbose` a `COVERAGE CHANGED SINCE LAST RUN:` table lists only the suites whose ratio moved, with `WAS`, `NOW` and a signed `DELTA`, worst first. The last twenty runs per schema are kept.
- **`adtai export_apex -rest` and `-files_ws` no longer walk the applications.** A run whose every format is schema-level prints one `EXPORTING:` header and skips the per-application blocks, measured at 34 seconds down to 8 on a 17-application schema. The per-application header now leads with the verb.
- **`dependencies -refresh` shows the PL/Scope recompile moving.** On a schema without full `PLSCOPE_SETTINGS` it is the slowest thing the command does and it had no output at all. One progress row now carries a rising percent and a falling estimate through the loop.
- **`dependencies -refresh` records the database's own UTC offset.** Freshness is compared against the database clock rather than this machine's, so a repo on a host in another timezone is no longer judged against the wrong one. A mirror refreshed before this needs one refresh.
- **The README opens on the problem it solves, and every section carries its own illustration.** Commands are grouped by the job you came to do, Install walks from cloning the repository to a first export, and Quick Start groups its examples the same way Commands does.
- **`discovery` and `dependencies` say who they are really for.** `discovery` is the safe way to let an AI agent explore a schema, because it only ever reads. `dependencies` costs an agent a fraction of the tokens that digging through a live schema would.

## 0.9.0 - 2026-08-17

- **`export_db -compact` replaces the per-object rows with one progress bar per schema.** The overview table, connection block and timer stay. The countdown is seeded from what the last export of that schema cost, and the bar labels the object type it is working through.
- **`export_apex -compact` draws one progress bar per schema across every requested application and format.** Its budget comes from the times previous runs recorded, so the countdown reflects what this export usually costs rather than a count of actions.
- **`export_db` shows its `GRANT` exports on screen.** Grants made, grants received, user privileges and directories were always written and never announced. They now run under their own heading, with a row each, and the four files land as before.
- **Every command prints the heading for the work it is about to do, before it blocks.** A run no longer sits on a finished row or a completed progress bar while the database is read. `export_db`, `export_apex`, `dependencies`, `recompile`, `flow` and `ut3` all gained the flush.
- **Breaking: the four APEX caches become one store at `config/internal/apex.db`.** The YAML files are converted on the next run and deleted. `validate` resolves `-app` through the store, still offline. Anything reading those files directly needs the store instead.
- **Breaking: `export_apex -checksum` is withdrawn and the checksum becomes cached metadata.** Every run records it in the APEX store, and `apex/<app>/checksum.txt` is no longer written or kept. Nothing has to ask for it.
- **Breaking: `calendar` loses `-list`.** The flag was declared, forwarded and read by nothing, so it parsed and did nothing. A flag that cannot act is now rejected by the parser rather than accepted in silence.
- **Breaking: `rebuild` writes one commit store per branch, `config/commits/#BRANCH#.db`.** The YAML cache is replaced by SQLite, a commit's number is allocated once and never re-derived, and a branch name carrying `/` now writes a file rather than a folder.
- **`rebuild -verify` reports each branch store read-only.** It prints the commit count, the number range and `CONTIGUOUS` or `BROKEN`, exiting 1 on a problem. A from-scratch rebuild now reaches one year back by default rather than walking a branch's whole history.
- **`search_repo` reads the configured `repo_commits_file` instead of the default path.** A project that had configured the key could rebuild happily and then be told its cache did not exist. Its per-file status letter now comes from git rather than a guess.
- **`export_apex -rest` puts workspace roles and privileges in `__enable_schema.sql`, and both artifacts compile.** The splitter dropped ORDS roles entirely and duplicated a privilege naming several modules, so a service that exported cleanly could fail to install.
- **`-recent` takes a fraction of a day, so an export can ask for the past hour.** `1/24` and `5/1440` are accepted wherever a whole number was, and the sub-day header reads the database clock rather than the machine's.
- **`ut3` names the run and what each table groups: `RUNNING TESTS FOR <patterns>:`, `SUMMARY PER SUITE:` and `SUMMARY PER MODULE:`.** The suites roll-up moves behind `-verbose`, so a default run goes from the connection block straight to the progress bar.
- **`dependencies` with no query refreshes the mirror.** The four line hint listing the flags is gone: the one invocation that needs no arguments to describe it was the one invocation that did nothing.
- **A superseded `db_dependencies.yaml` is deleted once its store has replaced it.** Nothing reads the flattened report any more, so `dependencies` removes it on the conversion and on every run after it.
- **`-by` and `-my` read the same on every screen that carries them.** The rows had five unrelated wordings and two different orders. Both are now declared in one order with one caption, wherever they appear.

## 0.8.7 - 2026-08-14

- **The data ADT.ai writes about a project moves into `config/internal/`.** The APEX metadata caches, the recent-export watermark and the stores behind `dependencies` and `flow` leave `config/`, which is for configuration you edit. Every run relocates what it finds, so nothing needs a command.
- **The sweep adds `config/internal/` to the project's own `.gitignore`.** On one live root it turned a folder git had never seen into an untracked 273 MB store, one `git add .` from a commit. Folders keep their documented locations: `config/commits/`, `config/discovery/`, `config/flow/` and `config/temp/` do not move.
- **`ut3` shows a progress bar by default, and the per-test listing moves behind `-verbose`.** The bar is bumped by finished suites and its label counts the tests the run will execute. The time on the right is what is left, seeded from the previous run.
- **`ut3` caps `ERRORS & FAILURES:` at the first 20 stanzas.** A real run put 397 of them on screen and pushed both summary tables out of scrollback. The new `ut_limit_errors` sets the cap, `0` prints everything, and the counts in the tables are never capped.
- **`ut3 -gate [N]` fails a run whose tested packages fall under a coverage threshold.** The whole report prints first and a `COVERAGE BELOW <n>:` table closes it, worst first. Bare `-gate` uses the new `ut_coverage_gate`, and only a measured figure is compared.
- **`ut3`'s `MODULES:` gains a `LINES` column, and a passing test row shows its own elapsed seconds.** `LINES` totals the body lines of the packages a group's suites test, the same set `COVERAGE` beside it is computed over. A clean test's timing is new information where `PASS` restated nothing.
- **`export_db` keeps a multi-line CHECK constraint's own line breaks and comments.** Flattening the body parked every value behind the first comment, and the exported table then failed to deploy with `ORA-00936`. Eleven table files on one measured schema were unusable.
- **A comment is no longer read as SQL anywhere the DDL is scanned.** An apostrophe inside a line comment flipped the string state, and an unbalanced bracket inside one moved the parenthesis depth, which could drop a whole table back to raw dictionary output.
- **`export_data` no longer exports virtual columns.** A virtual column carries an ordinary `column_id`, so the filter meant to exclude it never did, and the generated INSERT or UPDATE naming it fails with `ORA-54013`. The column list now filters on `virtual_column` instead.
- **A finished progress row closes itself, so two rows can never share a line.** An `export_apex` run could weld `READABLE COMPONENTS` and `EMBEDDED CODE REPORT` onto one 125-column line, which reads as the first action having failed when both had succeeded.
- **An `&` in a password no longer hangs the SQLcl connect.** The credential is embedded verbatim, so an ampersand read as a substitution variable and SQLcl blocked on a prompt nothing could answer. The directive making it inert now leads every connect block, covering `export_apex` and `validate`.
- **`search_repo` accepts a commit range.** `-commit 12` selects that commit, `12+` that commit and everything newer, and `12-40` the inclusive span. `-hash` reads the same syntax. One shared resolver answers for every command taking a commit reference.
- **Every command's `--help` says what the module is for, not how it works.** The SUMMARY on `calendar`, `connection`, `discovery`, `doctor`, `rebuild`, `recompile` and every other command was rewritten and capped at eight lines, so it no longer repeats what the option rows and the USAGE page already explain.
- **Option rows across every command speak in one voice.** Multi-value flags had three spellings for one idea, ranges had two, and six descriptions had grown into paragraphs that belonged in the USAGE page. Every caption is now a single fragment bound to four rendered lines.
- **No text a reader sees spells a dash the wrong way.** Every help screen, USAGE page and error message was swept of the em dash and of the two-hyphen stand-in for it, which the ADT.ai writing style bans. SQL comment syntax inside generated scripts is untouched.

## 0.8.6 - 2026-08-11

- **Breaking: `ut3 -coverage` is removed, and coverage is measured on every run.** It lands as a `COVERAGE` column immediately after `TIMER` in both `SUMMARY:` and `MODULES:`, so a plain `ut3` reports it. The flag now errors rather than being accepted and ignored.
- **The coverage figure is run-scoped, a deliberate trade.** A suite row carries the figure for the package it tests, paired through `ut_match`; `MODULES:` rows and the total aggregate that group. `ut3` now answers how much of what a suite tests it reached, never what in the schema is untested.
- **A blank `COVERAGE` cell and `0.0` are different findings.** Blank means no measurement: nothing paired, or a target Oracle never instrumented, since native compilation strips the instrumentation. `0.0` means instrumented and never entered. The removed `NO CODE COVERAGE:` work list is what this release gives up.
- **`ut3` status words shorten to `PASS`, `FAIL`, `ERROR` and `SKIP`**, everywhere they print: the `SUMMARY:` and `MODULES:` column headers, the fixed-width `TEST RESULTS:` rows, and the `ERRORS & FAILURES:` stanza headings, which now lead `FAIL > PKG.TEST`. One constant is both word and header, so they cannot disagree.
- **`SUMMARY:` reads `SUMMARY FOR <PATTERNS>:` under `-name`**, upper-cased with several patterns comma-joined, since case carries no meaning in a `LIKE` pattern against Oracle identifiers. `MODULES:` keeps its own header.

## 0.8.5 - 2026-08-09

- **`ut3` test-package naming is configurable.** The `_UT` suffix was hardcoded, so a project using another convention could not use the command at all. Four Oracle regexes replace it: `ut_pattern`, `ut_match`, `ut_owner`, `ut_module`. The defaults reproduce the old behaviour, and an unmatchable expression is refused at load.
- **`ut3` adds a per-module roll-up.** A `MODULES:` table groups the verdict and `TIMER` columns by module, adding a `PACKAGES` count. The module is read off the package under test, not off its suite, so a filtered run attributes every package. Nothing prints when `ut_module` is unset.
- **`doctor` reports the real version.** The package had carried the scaffold `0.1.0` since the repo was created, so every editable install misreported itself while released wheels were correct. A git checkout now reads `0.8.5 + WIP`; an installed copy shows `0.8.5`.
- **`doctor` offers only the updates you actually need.** `ACTIONS:` printed both upgrade commands on every read-only run without consulting the statuses above it, so an up-to-date machine was told to run both. Each line is now earned by its own component.
- **Every section header renders the schema name uppercase.** ADT.ai learns the schema from a connection-file key or a `-schema` argument, so one run printed `CONNECTING TO SCHEMA ict_owner` above `REFRESHING ICT_OWNER SCHEMA:`. Display only: export paths and connection lookups keep the file's casing.
- **The `Repeatable` column in every `USAGE` argument table now matches the parser.** Sixteen rows were wrong: flags documented as single-valued that accept repeats, and flags advertised as repeatable whose second occurrence replaces the first. A contract test now reads the live parser and fails any row that disagrees.

## 0.8.4 - 2026-08-07

- **New: `ut3 -coverage` reports Oracle code coverage for every package in the schema, including ones no test reaches.** Coverage data alone can only describe packages that were executed, so `CODE COVERAGE:` leads with the schema's own package list and joins the measurements onto it. See `docs/ut3.md`.
- **`NO CODE COVERAGE:` is the work list beside it**, carrying `PACKAGE` and `LINES` only. Three cells never render alike: `-` means nothing was measured, `0` means Oracle instrumented the package and nothing ran it, and `NATIVE` is a `-` with the cause attached.
- **A `-coverage` run goes quiet.** The suites still execute, since running the code is how Oracle collects block coverage, but the per-test rows and the run `SUMMARY:` are suppressed. `ERRORS & FAILURES:` still prints when there is something to show.
- **`SUMMARY:` closes with one row: `PACKAGES` / `LINES` / `COVERED` / `COVERAGE`.** The figures deliberately do not divide into each other: `LINES` counts every package-body source row, `COVERED` counts instrumented lines that ran, `COVERAGE` stays covered blocks over measured blocks. A percentage blanks when nothing was measured.
- **`ut3`'s `SUMMARY:` gains a `TIMER` column, that suite's own seconds.** It is wall clock around the whole `ut.run` call, not the sum of utPLSQL's per-test times: a suite spends as much cost in `%beforeall`, teardown and the round trip as in assertions. `0.0` is a measurement, not a blank.
- **`recompile` now says where to start when a schema is full of invalid objects.** A new always-on `ROOT CAUSES` section separates broken objects from downstream ones, ranks them by how much they explain, and adds a `BLAST` column counting what clears transitively. It reads after the list it ranks.
- **`ROOT CAUSES` keeps three fixes apart.** `CAUSE` reads `SOURCE` (its own text does not parse), `MISSING` (something it needs is gone) or `GRANT` (it exists and this schema cannot see it). Recompiling forever never fixes the last of those. A stale dependency mirror degrades the ranking, never failing the run.
- **`recompile` compile errors are a per-object list, not a table:** one `<OBJECT TYPE>.<OBJECT NAME>` stanza, then `<line>.<pos> <message>` per distinct error, wrapped under a hanging indent and never truncated. Oracle's cascade rows are dropped and discounted, so the noisiest object no longer outranks the most upstream.
- **`export_db -by` and `-my` stop claiming a recency they never measured.** Both were documented as exporting what a developer *last* changed; the query selected any object that developer had ever touched, unordered. A new optional `audit.changed_at` column gives them a real answer; without it nothing changes.
- **With `changed_at`, `-by`/`-my` report each object's latest author.** An object you touched that somebody else changed afterwards is still exported, since dropping it would lose your work, and its row carries the later author in brackets, `APP_ORDER_PKG [SCOTT]`. `-recent` reaches the audit source too.
- **`export_apex -rest`: a dead SQLcl session can no longer finish as a successful export.** `SP2-0640: Not connected` is a client-level message, so `WHENEVER SQLERROR` never fired: SQLcl ran the whole script against nothing and exited `0`. The failure is now read out of the transcript and raised.
- **`export_apex -rest` gained a deadline and better errors.** New `rest_timeout_seconds` (default 60) bounds the export, which could previously sit for as long as SQLcl liked. The whole scrubbed transcript now rides the error rather than one regex-picked line, and a failed row completes with `FAILED` first.
- **`export_apex -files_ws` exported the same workspace files once per application.** Workspace files land under `apex/workspace/`, a path with no app id, but the export ran inside the per-application loop: N applications rewrote identical files N times, and a schema hosting no application exported none. It now runs once per schema.
- **The APEXlang skip row prints only when `-apexlang` was asked for by name.** Under `-all` nobody named the format, so every pre-26.1 instance reported the absence of something never requested. Skipping is unchanged either way, and `-readable`'s own 26.1 gate has always been silent, so the pair finally reads consistently.
- **Tables no longer wrap on an 80-column terminal.** Every cell was padded to its column width and given a three-space gutter, the last column included, so an unstripped row ran past its visible content and wrapped as a blank line under every row. Tables now fit 78 columns.
- **Every section header ends with `:`.** Half of the tool's 102 titled sections disagreed with the other half, with no rule to consult. A value or count now folds into the header phrase instead of trailing the colon: `EXPORTING OBJECTS: (61)` is now `EXPORTING 61 OBJECTS:`.
- **`dependencies -refresh`'s locked-object skip line is indented and unqualified**, where it read `SKIPPED LOCKED PACKAGE.AUTHMAN` hard against the left margin. `-refresh` is not read-only, since it recompiles to collect PL/Scope, so a skip means that object's edges were not refreshed while the command still exits `0`.

## 0.8.3 - 2026-08-05

- **New `ut3` command: it runs the connected schema's utPLSQL test suites.** A bare `adtai ut3` runs every suite; `-name` takes repeatable Oracle `LIKE` patterns. A package is a suite only when both halves agree: its name ends in `_UT` *and* utPLSQL has parsed it as a `%suite`. See `docs/ut3.md`.
- **`ut3` treats dishonest green as a failure.** utPLSQL does not raise on failure, so the exit code is the deliverable: a failed or errored test, an empty or unparsable report, and a run matching nothing are all non-zero. An empty green run is what a vanished suite looks like.
- **`ut3` output is one grid.** `UNIT TESTS SUITES:` rolls up what will run; `TEST RESULTS:` prints as the run proceeds, each row carrying the test's procedure name rather than its `%test` description; every non-passing test gets a stanza under `ERRORS & FAILURES:`; `SUMMARY:` repeats the suites table with verdict counts.
- **SQLcl is launched without `ORACLE_HOME`, so it stays on the thin driver.** Every SQLcl-backed command died on macOS with `no ocijdbc23 in java.library.path`, a library that is present and correct, so the obvious readings are all wrong. ADT.ai created the condition itself by exporting `ORACLE_HOME` for python-oracledb's thick mode.
- **The SQLcl child process now gets an environment with `ORACLE_HOME` removed and everything else intact.** `PATH` still finds the launcher, `TNS_ADMIN` still resolves aliases, wallet connects are unchanged, and the parent's environment is never touched, so thick mode still works for every command connecting through python-oracledb.
- **A schema name resolves case-insensitively.** An Oracle schema name is a case-insensitive identifier, but the connection lookup matched the YAML key exactly, so a file keyed `ict_owner` failed for `ICT_OWNER`, reporting `Schema not configured` above `Available schemas: - ict_owner`. The exact key still wins.
- **`file_crlf` now decides the line ending of exported files.** Python never touches a `"\r"` already in the string, so LF mode passed an existing `"\r\n"` through and CRLF mode rewrote it to `"\r\r\n"`: both modes produced CRLF on exactly the objects the setting exists for. Every incoming `\r` collapses first.
- **`export_db`'s unchanged-file check was wrong the same way**, comparing raw DDL against a universal-newline read, so a CRLF-carrying object was rewritten on every export. The blast radius was all three exporters. Raw LOB sidecars and the CSV handle stay byte-faithful and are unchanged.
- **`export_apex -rest` is a schema-level export again.** REST services land in `apex/workspace/rest/`, a path with no app id, but the export ran inside the per-application loop: a schema publishing REST services but hosting no APEX application skipped it entirely and exited `0`. It now runs once per schema.
- **An unresolved `{$...}` placeholder in `path_objects` is now a config error, not a folder name.** Old ADT's `{$NAME}` syntax is not among the two placeholders `path_objects` resolves, so a template passed through verbatim and the export built a directory literally called `{$INFO_SCHEMA}`: 851 files on one project, nothing warning.
- **The placeholder guard is deliberately narrow and never cleans up.** Only `{$...}` in `path_objects` is treated as unresolved; the token stays live syntax in keys that substitute their own. A tree an earlier run created is left for you to delete. A config holding an unusable value reports `CONFIGURATION INVALID`.

## 0.8.2 - 2026-08-03

- **A SQLcl session that never connected no longer reports success.** `export_apex -rest` could run its progress bar to 100%, write no files, and say nothing. Two causes: SQLcl continues after an error, and the process inherited the terminal's input, so a failed connect waited forever on an invisible prompt.
- **The connection file is no longer written ahead of the run that earns it.** A SQLcl connection name and fingerprint were recorded for a connection that had never been created, and deleting the two lines by hand wrote them back. **Behaviour change:** a failed connect is now a non-zero exit.
- **`export_apex -rest` tells an empty export apart from a failed one.** A schema that genuinely publishes no REST services still exports successfully and leaves an empty folder; an export that reported an Oracle or SQLcl error now fails naming that error, instead of completing as though nothing needed writing.
- **`recompile` shows what the run actually repaired.** Every number in OBJECTS OVERVIEW was read *after* the recompile, so a run that fixed seventeen package bodies printed what a run that fixed nothing printed. A new `VALIDATED` column counts objects by identity, since recompiling a spec invalidates its dependents.
- **Documentation corrected: no sample connection file ships with the tool.** `docs/connection.md` described a template connection file as shipped and created for you; connection files hold credentials, so `doctor -init` writes none. `connection -create` is the bootstrap path.

## 0.8.1 - 2026-08-03

- **Breaking: a connection file no longer renames the folder a schema exports into.** The `export.subfolder` key and its `-subfolder` flag are gone, so a schema's objects always land under the schema's own lowercased name, decided by `path_objects` alone. Upgrading needs no edit; a leftover `subfolder:` line is ignored.
- **`connection -create` now fills blank export placeholders instead of doing nothing.** The sample connection file ships its export block pre-seeded and empty, and an existing key was treated as a value to preserve, so `-create … -ignore 'JOB%' -go` wrote nothing and exited `0`. A blank value is now recognised.
- **`connection` previews no longer print stored secrets.** The default preview reused the loaded environment verbatim, so it printed every sibling schema's passwords and the wallet passwords to stdout, the surface that lands in shell history and transcripts. The preview is now a structural copy with every secret stripped.
- **`dependencies -refresh` opens its connection the same way every other command does.** On a schema whose DDL trigger requires a client identifier the refresh failed outright with `ORA-20990`. Every command now builds its connection through one shared function, so session setup reaches all of them instead of eleven wirings.
- **A disabled trigger now exports as a file that actually runs.** Oracle reports a disabled state by appending `ALTER TRIGGER … DISABLE;` *inside* the `CREATE TRIGGER` block, above the terminator. `export_db` stripped only the `ENABLE` form, so every disabled trigger raised `PLS-00103`. The statement is now re-emitted below the terminator.
- **`export_db` puts a trigger's `FOR EACH ROW` on a line of its own.** Oracle hands the header back exactly as the developer typed it, so the clause deciding whether the body runs per row or per statement could sit buried anywhere. Only the header is touched; repeated exports stay byte-identical.
- **`recompile` no longer has a LOCKED OBJECTS report.** It read `gv$` views, and ADT.ai connects as the application schema, which holds no `SELECT` on them: a DBA decision, not a misconfiguration a tool can correct. Nothing is lost, since a stuck object surfaces under `INVALID OBJECTS`.
- **A corrupt cache or hand-edited config file now degrades with a warning instead of killing the run.** A YAML syntax error in a generated cache or `config/IDENTITY.yaml` used to cost the whole command with a raw traceback. Both loaders now warn and continue empty, and a malformed `connections.yaml` reports normally.
- **In the same hardening pass:** a foreign-key tree walk survives chains deeper than Python's recursion limit, `search_repo -restore` prints a `COULD NOT RESTORE:` section instead of letting an incomplete restore look finished, blank `groups.yaml` entries are reported, not dropped, and a control character can no longer truncate a cached record.
- **Documentation corrected against the code.** `docs/recompile.md` marks `-scope`/`-warnings` repeatable (they always were), `docs/search_repo.md`'s argument table gains the `Repeatable` column its siblings carry, and the discovery report filename reads `<YYYY-MM-DD--HH-MI>.md` everywhere.

## 0.8.0 - 2026-07-28

- **New `validate` command: check an exported APEXlang folder before anything tries to import it.** It runs the APEXlang compiler over the `apexlang/` trees and exits non-zero when the compiler reports anything, so it works as a deploy or CI gate. It never connects to a database. See `docs/validate.md`.
- **`validate` resolves targets three ways, and they combine.** `-input` takes a folder, a zip, or a single `.apx` file; `-app` is repeatable and resolves offline to that application's export path; a bare run validates every `apexlang/` folder under the configured APEX root. Warnings never fail the run.
- **A `validate` run that checked nothing is never reported as a pass.** A folder holding no APEXlang files, an input path that cannot be found, a requested application with no export on disk, and output this version cannot parse are all non-zero. Static-file payloads are linked into place before compiling.
- **`export_apex` gains a ninth format: `-apexlang` (alias `-apx`)**, which exports APEX 26.1's APEXlang `.apx` source as a whole-application tree under `apexlang/`, and joins `-all`. The layout is what APEX emits, written verbatim with none of the SQL export's postprocessing: `.apx` is compiler input, not something to decorate.
- **APEXlang is a whole-application format in this version.** `-page`, `-component` and `-recent` never filter it, and an APEXlang run never advances a `-recent` watermark. Below APEX 26.1 the row completes as `SKIPPED` rather than failing, so an `-all` run against an older instance degrades instead of breaking.
- **Static-file payloads are deliberately not written into `apexlang/`.** `-files` remains the single static-file channel, so the repository holds one copy of each file. That makes `apexlang/` a source and review surface, readable in Git and editable by hand or by an AI tool, rather than a directly importable artifact.
- **New `calendar` command**: `adtai calendar` renders your Git activity across all branches as a month-by-month calendar, read from the commit index `rebuild` caches, so it answers "what did I work on, and when?" without touching a database. See `docs/calendar.md`.
- **`export_apex -deep` is now part of the public surface.** Combined with `-page`, it also exports the components recorded for the selected pages in `config/dependencies.db`, so a page export carries the shared components that page actually uses. Run `dependencies -refresh` first; `-deep` without `-page` is rejected.
- **ADT.ai now fills in its own environment when the calling shell never ran your startup file**, so a command from an AI tool or editor stops failing on a missing `ADT_KEY` or `ORACLE_HOME`. When either sentinel is unset, it reads that file and fills in only the variables it knows.
- **A variable already present in the environment is never overwritten**, so an explicit export, a CI variable, or a one-off `ADT_ENV=PROD adtai …` always wins. Every failure mode is soft, and it is a no-op on Windows. `doctor` gains a `HYDRATED` row; `ADT_KEY` is hydrated but never printed.

## 0.7.3 - 2026-07-25

- **A multi-schema run now reads schema by schema instead of one merged pile.** `export_db`, `export_data`, `export_apex`, `recompile`, and `dependencies -refresh` print one complete section per schema: the connection block, that schema's entire work, then its own `TIMER`, before moving to the next. A single-schema run is unchanged.
- **`dependencies -refresh` console output is quieter.** The scope header is now `REFRESHING: <scope>`, since the banner above it already names the command, and the internal note about the local dependency database being rebuilt from scratch is gone. Nothing about the refresh itself changed.

## 0.7.2 - 2026-07-23

- **Breaking: a bare `-recent` now means "since my last successful export", not "one day".** `export_db` and `export_apex` keep a per-scope watermark in `config/recent.yaml`, so a repeated run exports only what changed since the previous covering run. `-recent N` keeps its N-day meaning everywhere.
- **The `-recent` watermark is read from the database clock before the object listing**, so an object changed mid-run is re-selected next time rather than lost, and it advances only on a successful, unnarrowed, non-dry-run pass. With no watermark yet the run is a full export that seeds it.
- **Breaking: the per-developer identity file is now `config/IDENTITY.yaml`** (was `config/me.yaml`; migrate with `mv config/me.yaml config/IDENTITY.yaml`). It keeps the same three keys, stays gitignored, and ships no sample file; its shape is documented in `docs/README.md` under Developer Identity.
- **Every new database connection now sets the Oracle session identifier automatically** from `db_schema` before `STARTUP.sql` is processed, so sessions are attributable through `V$SESSION.CLIENT_IDENTIFIER` without the hand-written startup block the docs used to suggest. A personal `STARTUP.sql` can still override it.
- **Added `export_apex -checksum`**, which writes the application's ID-independent SHA-256 fingerprint to `checksum.txt` beside `f<id>.sql`. Because the fingerprint ignores internal component ids, a deploy gate can answer "did anything actually change?" with `git diff --exit-code` on one small file instead of a full export.
- **`-schema` accepts a space-separated list** on `export_db`, `export_data`, `export_apex`, `recompile`, and `dependencies`, so `-schema CORE APP` runs both schemas in one pass, joining the repeated-flag, comma-separated, and wildcard forms that already worked. `discovery` and `connection` stay single-valued by design.
- **`recompile` no longer silently converts natively-compiled objects to interpreted.** A plain recompile used to name the code type on every PL/SQL object, which overrides `REUSE SETTINGS`. The code type is now stamped only when `-native` or `-interpreted` is passed explicitly, and `-interpreted` became a real choice.
- **`recompile -force` with a compile modifier now recompiles only what actually drifts.** A bare `-force` still recompiles every matching object, but `-force` with `-native`, `-interpreted`, `-level`, `-scope`, or `-warnings` selects only valid PL/SQL objects whose settings differ from the requested state. Types carrying no compile settings are skipped.
- **A duplicate object filename no longer aborts `export_db`.** The export runs to completion and the affected object is reported inline, one row per location marked `[DUPE]`, so the stale copy is named rather than merely detected. Two schemas legitimately exporting the same object name are not a collision.
- **`dependencies -app … -refresh`** lists the selected applications as a table before the refresh loop and shows progress during each application's component scan, matching `export_apex`.
- Every `docs/<command>.md` now opens with a description of what the module does and how it relates to its siblings, instead of starting straight on a command example. Three stale one-line purposes in the `docs/README.md` command table were corrected, and personal identifiers were replaced with neutral placeholders.

## 0.7.1 - 2026-07-21

- **Added the `connection` module** for managing named SQLcl connections. Generated SQLcl scripts (including `export_apex -rest`) now connect through a named, credential-free SQLcl connection instead of embedding a username, password, or wallet path each time; a changed credential is detected and re-registered automatically. See `docs/connection.md`.
- **Fixed:** `export_apex -rest` against an Oracle wallet (OCI) could produce no output — wallet paths now resolve correctly regardless of where the generated script runs from.
- **Fixed:** `export_apex -rest` occasionally wrote a stray `Session altered.` line into generated REST files, corrupting the output.
- **Fixed:** `export_apex -component` help text could crash on some Python versions due to an unescaped `%` character.
- **Fixed:** `dependencies -app <range> -refresh` against several applications printed one flat combined listing instead of rendering results app by app, matching `export_apex`'s per-application section style.

## 0.7.0 - 2026-07-17

- **Breaking — `recompile`:** the `-target` alias is removed; use `-env`. The report actions `-mviews`, `-synonyms`, `-disabled`, and `-jobs` no longer carry their own `[NAME]` pattern — they are bare flags scoped by the shared `-name` and `-type` filters, so `adtai recompile -mviews DEP%` becomes `adtai recompile -mviews -name DEP%`.
- **Breaking — `dependencies`:** the query flags `-uses` and `-used-by` are renamed to `-from` and `-to`. `-unused` is removed — determining truly unused objects offline is not reliable and the mode produced false results. The derived `config/db_dependencies.yaml` artifact is no longer written; `config/dependencies.db` is the single source.
- **Line endings are deterministic on every platform, and the `file_crlf` setting works.** Exported files previously took the host's native line ending, so a Windows run produced mixed output while `file_crlf` was carried in config but never applied. ADT.ai now writes LF by default and CRLF when `file_crlf: true`.
- **Flipping `file_crlf` never rewrites files whose content is unchanged**, so there is no mass rewrite; files adopt the new ending on their next real change. Raw LOB sidecars are the deliberate exception, mirroring stored database values byte for byte. Documented in `docs/README.md` under Line Endings.
- **Long-running queries no longer abort after 15 seconds.** A single timeout bounded both the connection attempt and every query round-trip. The two budgets are now independent and configurable as plain seconds: `connect_timeout_seconds` (default 15) and `query_timeout_seconds` (default 1200). Connections also fail fast on an unreachable host.
- **Added `recompile -trailing`**, which removes trailing whitespace from stored database source in place. `export_db` strips it from every line it writes, so an otherwise untouched package still differs from the database's stored source on every export. There is no preview mode, so scope it with `-type`/`-name`.
- **`recompile -trailing` never touches an object with nothing to strip**, a disabled trigger stays disabled, and wrapped objects are skipped. Follow a sweep with a plain `recompile`, since `CREATE OR REPLACE` invalidates dependents.
- **`recompile` and `export_db` now speak Oracle's object-type vocabulary.** `-type MVIEW` and `-type MATERIALIZED` both mean `MATERIALIZED VIEW`; a multi-word type may be spelled with a space, an underscore, or any casing; and quoting no longer changes what a command means. `SPEC` is the counterpart of `BODY`.
- **`recompile` filters accept multiple values.** `-name` and `-type` take several patterns (`-name APP% CORE%`, `-name APP%,CORE%`, or a repeated flag). `-schema` is now repeatable and pattern-aware, matching `export_db`. A bare `recompile` with no `-schema` visits every configured default schema rather than only the first.
- **Each `recompile` schema is an independent pass** and every schema runs even when an earlier one fails, so a broken schema cannot mask the rest. `-disabled` honours `-type` as well as `-name`. `discovery -schema` deliberately stays single-valued and is documented as such.
- **Added `dependencies -age`**, an offline per-scope staleness report. Every `-refresh` stamps each refreshed scope's completion time, and `-age` reads them back with no database connection in `table`, `yaml`, or `md` format, so per-scope freshness is checkable without guessing from file timestamps.
- **`dependencies` query improvements.** An object name given without a `TYPE.` prefix now resolves instead of returning nothing, and `-schema` works as an offline owner filter, disambiguating a bare name on a multi-schema mirror. The default table output splits the old `OBJECT` column into `OBJECT_TYPE` and `OBJECT_NAME`.
- **`dependencies -refresh` improvements.** `-refresh -app` accepts multiple ids and ranges (`-app 100 200`, `-app 120-130`, `-app 300+`) and connects straight to the owning schema when it is already known. Oracle-maintained schemas are never mirrored, and stale rows left by earlier refreshes are purged.
- **`export_db` gains `-by <AUTHOR>` and `-my`**, matching `export_apex`, so a shared schema worked by several developers through proxy users can still resolve who last changed each object. Authorship resolves against a project-defined `audit:` source in `config.yaml`, so no DBA privilege is needed.
- **Added `export_db -groups`**, an action that reorganizes already-exported files into per-group subfolders so a large object-type folder stays navigable. It previews every planned move and prompts before moving anything; `-dry-run` previews only. `export_db` also runs a duplicate-filename check before writing.
- `export_apex -app <id>` connects straight to the owning schema when the app's owner is already known, skipping a wasted default-schema connection and a discovery round-trip.
- `flow -to`/`-from` query tables use uppercase, plural column headers to match the refresh summary. Row values and the Mermaid, DOT, and JSON outputs are unchanged.
- `rebuild`'s progress bar now aligns with every other progress line.
- **New config templates.** `config/me.sample.yaml` documents the gitignored per-developer identity file, and `config/STARTUP.sample.sql` replaces the tracked `config/STARTUP.sql`, so local session setup no longer dirties the checkout. `merge_batch_size` and `dependencies_max_depth` moved out of the code into `config.yaml`.
- **A flag with the same name now parses the same way on every command.** `search_repo -type` and `-name` accept multiple patterns, and `export_db -recent` / `search_repo -recent` accept the bare `-recent` form meaning one day, both matching the commands that already had them. No existing invocation changes meaning.
- **Hardened a batch of smaller failure paths:** malformed commit-cache files degrade to an empty cache instead of crashing, an unreadable project config warns instead of reading as none, a scheduler job argument with no anchor in its DDL warns instead of vanishing, and the synonyms report no longer fragments.
- Fixed `docs/discovery.md` misdocumenting `-nolog`, which suppresses report logging only — `-file` result write-back happens on every `-file` run regardless.
- Public commands unchanged: `export_db`, `doctor`, `dependencies`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.4 - 2026-06-26

- **Cleaned up `doctor` output:** online update checks for Java and Instant Client are no longer performed (both are system-managed), and a version-cache bug that could show a false `UPDATE` row after `git pull` is fixed. `doctor -init` now copies the project `.gitignore` from the ADT.ai root at runtime.
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
- Simplified public command help pages with single-line summaries, clearer `docs/<command>.md` links, compact option wrapping, and ADT-style single-dash aliases in displayed help while keeping long aliases accepted by the parser.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.1 - 2026-06-22

- Added `recompile -disabled [PATTERN]`, a read-only health report for disabled constraints, invalid or function-disabled indexes, and disabled triggers. The report groups findings by object type in compact tables and can be filtered by object name pattern without running the invalid-object recompile flow.
- Added `recompile -jobs [PATTERN]`, a read-only scheduler health report for today's job runs. The report groups jobs by run status and shows compact job name, last-start, duration, and CPU timing columns, with optional job-name filtering.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.6.0 - 2026-06-21

- **Extended completion sound controls across every public command.** `-beep [theme]` accepts a case-insensitive theme override for one run, bare `-beep` forces the configured theme or falls back to `chime` when sounds are disabled, and `-nobeep` suppresses sounds for one run with priority over both. Static screens stay silent.
- Changed the default exported database layout to the schema-first shape used by current project folders, and tightened `export_db` normalization around materialized-view logs, sequence defaults, and `add_if_not_exists` output.
- **Changed `export_apex -recent [DAYS]` from report-only to report-and-filter for component-based exports:** split, readable, and embedded exports now write only the recent components returned by the same recent-component query, and `-by DEVELOPER` narrows both the report and the exported set. Full app SQL keeps its existing behavior.
- Extended `export_data` large-value sidecars so BLOB, CLOB, XMLTYPE, and JSON payloads can be imported through generated SQL-only scripts beside the exported table data. The main MERGE SQL now prints visible SQLcl progress prompts for those payload scripts while keeping the scalar MERGE batched.
- **Expanded `recompile` reporting and focused actions.** `-mviews [PATTERN]` targets materialized views without running the invalid-object recompile path, supports forced refresh of every match, streams each view row while work is happening, resolves refresh type to clean `F` or `C`, and shows materialized-view log presence.
- **`recompile` compile errors are keyed by a stable ID** with full messages printed below the table, locked objects are reported, and the new `-synonyms [PATTERN]` report lists synonym targets, privileges, grantability, and target status without changing database objects.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.5.3 - 2026-06-19

- Internal refactor: relocated the shared Git commit-discovery and commit-cache helpers into neutrally-named top-level modules and tidied the package layout. No user-facing change — every command behaves exactly as in 0.5.2.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.5.2 - 2026-06-19

- **Added the `flow` command, which maps an APEX application's page navigation graph.** `flow -app <id> -refresh` scrapes one application's navigation links from the database once and stores them in a local SQLite file, then `-to <page>` and `-from <page>` answer "what links here?" and "where can I go?" entirely offline.
- **`flow` edges cover page branches, buttons, list entries, tabs, navigation-bar entries, and report column links**, each tagged by how resolvable its target is: a same-application page, a cross-application link, a runtime-dynamic target, or a link that leaves APEX. Every refresh also writes Mermaid, Graphviz DOT, and JSON diagrams.
- Public commands now: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`, `flow`.

## 0.5.1 - 2026-06-19

- **Hardened the toolkit through a repository-wide correctness and security audit.** `export_data` guards its output-filename derivation for table names without a dotted schema prefix, and `discovery -file` scrubs only ADT-generated result blocks and splits statements on top-level `;` only, so a semicolon inside a string literal no longer mis-splits a query.
- **Security fixes in the same audit:** connection files parse through a safe YAML loader before any round-trip edit; SQLcl credentials stay out of captured output; the SQLcl upgrade verifies the download host and is hardened against zip-slip; TLS certificate errors are surfaced, not routed around; generated SQL validates Oracle identifiers.
- **The non-functional `ADT_KEY` password "encryption" is no longer presented in the docs as a working setting.** Dead README links were removed, stale command names fixed, and retired flag claims dropped.
- **Added id-range support to `export_apex -app`.** Each `-app` value may now be a plain id, a closed range `MIN-MAX`, or an open range `MIN+`, and the tokens combine freely: `-app 0-99 100-999 5000 -all`. Plain-id-only invocations are unchanged; a malformed or inverted range exits 2.
- **Fixed two error-presentation gaps so every command path honors the console contract:** print the banner, and hide raw Python tracebacks unless `-debug` is passed. Failures during CLI setup now print the standard error banner, a concise one-line error, a `-debug` hint, and the `TIMER` footer instead of a traceback.
- **In command dispatch, recognised database errors keep their dedicated screen** while every other unexpected exception now prints an `UNEXPECTED ERROR` block with the same hint, keeping the already-printed banner and timer intact. `-debug` still re-raises the full traceback in every path.
- Fixed `export_data` sidecar handling for large and structured columns: BLOB, CLOB, XMLTYPE, and JSON values are now fetched and written under a table-named folder beside the CSV, using `<primary-key>.<column>.<ext>` filenames, while scalar columns continue to drive the CSV and the generated MERGE SQL.
- Changed `export_data` selection when `-name` is omitted so a bare `export_data` updates only tables that already have DATA files in the configured folder; an empty DATA folder now leaves the run empty instead of falling through to a wildcard export. Explicit `-name %` remains the all-matching-tables path.
- Changed `export_data` progress so the `EXPORT TABLE DATA:` header and the current table name print immediately after table discovery, giving real progress during large-table exports. Added `export_data -silent` / `--silent`, matching `export_db`.
- Changed completion beeps so a forced `-beep` chime stays non-blocking and is triggered from the shared `TIMER` footer path, covering both success and error exits (including database-connection failures) across every public command. Command help screens remain static and silent.
- Fixed the bare `adtai` / `adtai --help` module overview to end with a trailing blank line, matching the per-command help screens.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.

## 0.5.0 - 2026-06-15

- Fixed the shipped `config.yaml` path defaults back to the documented database-first layout: `path_objects` is `database/<schema>/<object_type>/` and `path_apex` is `apex/<schema>/`. This matches the README, the `adt` skill, and how `search_repo` derives object paths; the interim layout could silently break `search_repo -type` and `-name`.
- Reorganized `export_db` DDL normalization into focused per-object-type modules (table, view, index, sequence, synonym, type, job) for clearer, more consistent formatting. No change to command usage.
- Split the CLI into a modular `cli_*` family (parsing, help, runtime, context) with `cli.py` as a thin facade. Internal restructuring only — all commands and options behave as before.
- Public commands unchanged: `export_db`, `doctor`, `export_apex`, `export_data`, `recompile`, `rebuild`, `search_repo`, `discovery`.

## 0.4.6 - 2026-06-14

- Fixed the database error banner: a query that fails *after* a successful connection now prints a `DATABASE QUERY FAILED` header, the offending SQL, and the database error message, instead of mislabeling it as `DATABASE CONNECTION FAILED`. The wallet/connection advice footer now appears only for real connection failures.

## 0.4.5 - 2026-06-14

- Refined the `export_apex` inventory listing (`-reveal`) so it reports only what the current connection can actually reach: workspaces are scoped to the schemas configured for the environment, and Oracle's reserved internal workspaces (`INTERNAL` and the `COM.ORACLE.*` namespace) are filtered out so only user-provisioned workspaces appear.
- Changed `-reveal` to collect all matching applications first and then derive the workspaces and owners summary tables from those actual results, so narrowing with `-app` or `-schema` narrows the summary sections too. Application-section headers now include the workspace name (`APEX APPLICATIONS: <WORKSPACE>, <SCHEMA>`).

## 0.4.4 - 2026-06-13

- Fixed a batch of `export_db` DDL formatting issues so exported files match a clean, readable layout on real `DBMS_METADATA` output: views (simple, compact comma-packed, mixed quoted/unquoted, and CTE select lists), expression indexes, table columns, `INTERVAL` suffixes, `INMEMORY` clauses, schema qualification, and TYPE / TYPE BODY drop preambles.
- **Changed `export_db` to consolidate dedicated PK/UNIQUE indexes:** when Oracle exports a primary-key or unique constraint as a separate `CREATE [UNIQUE] INDEX` plus `ALTER TABLE … USING INDEX`, the constraint is folded back inline so it reads as if the table were created in one clean statement.
- **Table constraints are ordered deterministically** — PRIMARY KEY, then UNIQUE, then FOREIGN KEY, then CHECK, alphabetically by name within each group — while column lines keep their source order.
- **Added a `<table>.fix.sql` companion file beside any table whose index-backed constraints were folded inline.** It holds the recovery script that rebuilds the original dedicated-index arrangement, so the clean table export loses no information. The companion is regenerated on folding and removed when a table no longer qualifies.
- Changed `export_db` sequence DDL to drop Oracle's default ascending `MAXVALUE` (28 nines), matching how column DDL already strips it, while preserving explicit non-default maxvalues such as `MAXVALUE 999999999999`.

## 0.4.3 - 2026-06-12

- Changed Doctor's ADT.ai update check so non-git installs read the latest public release from `jkvetina/ADT.ai` on GitHub before falling back to PyPI.
- Kept Doctor read-only: update actions still require `doctor -update` or `doctor -sqlcl`, and `doctor -offline` still skips remote metadata.
- Corrected public help usage lines to show the installed `adtai` command name instead of the removed `adt-ai` entry point.

## 0.4.2 - 2026-06-12

- Corrected documentation and help text across the README and the usage index so examples, command references, and argument tables match the shipped command surface (the installed `adtai` command name, the real public options, and the per-command `docs/<command>.md` files).

## 0.4.1 - 2026-06-12

- Added the public skills index `SKILLS/README.md`, which explains that `adt` is the installed day-to-day skill for driving ADT.ai's commands while `adt-setup` is only for first-time setup and troubleshooting.

## 0.4.0 - 2026-06-12

- Added two repo-local skills so the tool is usable straight from a checkout: `SKILLS/adt` drives day-to-day command help and health checks, and `SKILLS/adt-setup` is a deeper install-and-troubleshooting checklist (covering Instant Client issues such as `DPI-1047` / a missing `libclntsh.dylib`).

## 0.3.0 - 2026-06-12

- Added four new commands — `recompile`, `rebuild`, `search_repo`, and `discovery` — each with its own `docs/<command>.md` reference.
- `recompile` recompiles a schema's invalid objects with an objects / invalid-objects overview, supports `-force`, `-scope`, and name filtering, builds the right `ALTER … COMPILE` flags (native vs interpreted, optimize level, PL/Scope, warnings), retries in reverse dependency order on reconnect, and exits non-zero when objects remain invalid.
- **`rebuild` builds a fast per-branch Git commit cache** (one file per branch) with a count-first pass and progress ETA. Its read-only `-reveal` branch inspector filters by name words, `-my`, and `-since` with a `-limit` cap, and can `-switch` the working tree to a listed branch.
- `search_repo` searches Git history fast off the `rebuild` cache — by summary terms, file path, database object type/name, author, commit or branch, and date windows (`-since` / `-until`) — printing newest-first with optional changed-file rows, and can restore matched historical file versions.
- **`discovery` is a safe, read-only `SELECT` explorer aimed at AI-assisted querying.** A static validator accepts only a single `SELECT` per statement, rejecting DML, DDL, PL/SQL, and comment-smuggled commands; every accepted query runs inside a rolled-back `SET TRANSACTION READ ONLY` session; results render to the console or a Markdown report.

## 0.2.0 - 2026-06-12

- Added the `export_apex` and `export_data` commands and shipped an MIT `LICENSE` so the public repo is safe to use and distribute.
- **`export_apex` exports APEX applications in every format** — full, split, readable, embedded, REST, application files, and workspace files, with `-all` running them together — using stable output paths and post-processing. Its `-reveal` inventory lists matching workspaces and applications across every configured schema.
- **`export_apex` persists application, developer and timing metadata** for repeatable exports, reports recent component changes (`-recent`, `-by`), and can override `p_release` (`-release`) for upgrade recovery.
- `export_data` exports table data to CSV with configurable delimiters, ignored columns, and primary/unique-key row ordering, applies global and per-table `where` filters, and generates DATA MERGE SQL with batched insert / update / delete blocks.
- Shared connection handling improved for both commands: Oracle wallet zip archives are auto-extracted before connect, and database-connection failures print a concise, actionable message (with the full traceback available under `-debug`).

## 0.1.0 - 2026-06-12

- First public release, shipping the `export_db` and `doctor` commands.
- `export_db` exports an Oracle schema to a clean, version-controllable file tree — tables, views, materialized views, indexes, sequences, synonyms, types, packages, procedures, functions, triggers, jobs, grants, and comments — normalizing raw `DBMS_METADATA` output into a stable, readable layout that compares cleanly from one export to the next.
- `export_db` scope and filtering: `-type` and `-name` filters with `%` / `_` SQL-style wildcards and comma-separated values, `-recent` for recently changed objects, and multi-schema exports (default schema lists, comma-separated `-schema`, `%` schema patterns) into the database-first layout `database/<schema>/<object_type>/`.
- `export_db` repository hygiene: `-delete` clean exports, detection and removal of stale object files no longer backed by the database (dry-run stays read-only), in-place updates of nested subfolders, clean `Ctrl+C` handling, and a `-silent` mode for agent-driven runs.
- `doctor` runs local environment health checks for Python, Git, Java, SQLcl, `oracledb`, Instant Client, `PATH`, `JAVA_TOOL_OPTIONS`, and the ADT-compatible environment variables.
- Foundations shared by every command: external connection files and wallets resolved from outside Git (kept out of the repo), automatic wallet extraction before connect, and a consistent console contract — banner, connection block, progress, then a `TIMER` footer.
