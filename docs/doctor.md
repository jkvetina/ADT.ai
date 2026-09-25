# Environment Check and Updates (adtai doctor)

![It only looks. It never touches.](images/doctor.png)

`doctor` tells you whether this machine can run ADT.ai, and what is out of date. Run it after installing, after a toolchain upgrade, or whenever a command fails in a way that smells environmental rather than like a bug. It also owns the explicit updates and the project scaffolding, since neither should ever happen behind your back.

Installation and environment setup are in [SETUP.md](../SETUP.md); this page owns what the command does.

<br>

## Examples

Check the local setup from any folder:

```bash
adtai doctor
```

Check without calling out to remote version metadata:

```bash
adtai doctor -offline
```

Update ADT.ai, its Python requirements and SQLcl, or land a named release:

```bash
adtai doctor -update
adtai doctor -update 0.9.1
```

Update SQLcl on its own, or scaffold a new project folder:

```bash
adtai doctor -sqlcl
adtai doctor -init
adtai doctor -init -root ./new-project
```

Sync an already-scaffolded project's `.gitattributes`/`.gitignore` with the current shipped template:

```bash
adtai doctor -init -sync
```

<br>

## Output

Current versions first, then the runtime environment, then the actions available. Nothing connects to a database:

```text
APEX DEPLOYMENT TOOL - DOCTOR
-----------------------------

CURRENT VERSIONS:
-----------------
  ADT.ai               | 0.9.3
  Python               | 3.13.5
  Git                  | 2.50.1 (Apple Git-155)
  Java                 | 20 2023-03-21
  oracledb             | 4.0.2
  Instant Client       | 23.3.0.23.09
  SQLcl                | 26.2.1.0

ENVIRONMENT:
------------
  ADT_ENV              | DEV
  ADT_KEY              | <redacted>
  ARCH                 | arm64
  JAVA_TOOL_OPTIONS    | -Duser.language=en
  LANG                 | en_US.UTF-8
  NLS_LANG             | AMERICAN_AMERICA.AL32UTF8
  ORACLE_HOME          | /Users/dev/.instantclient_23_3
  SQLCL                | /Users/dev/.instantclient_23_3/sqlcl/bin/sql

TIMER: 1s
```

- A bare version row is good news: that value was detected and no newer one was found, or online checks were skipped.
- Encryption key material is never printed. A direct `ADT_KEY` renders as `<redacted>`, a configured command as `<from ADT_KEY_CMD>`, and no source as `<empty>`. Setting both sources is ambiguous and renders a warning without showing either value.
- The `ENVIRONMENT:` rows are what the process actually holds, so a run under an AI tool shows the values ADT.ai filled in for itself from your startup file. How that works is on [config.md](config.md#environment-variables).
- Every version banner `doctor` reads from a child process (Git, Java, SQLcl and the rest) decodes as UTF-8, not the console's own codepage, so a non-ASCII byte in the banner renders correctly instead of turning into replacement characters.
- Status words append after a dot leader, capped at 78 characters.

<br>

## What the statuses mean

| Status | Meaning |
| ------ | ------- |
| `UPDATE` | A newer version was found online. |
| `WARN` | Read-only `doctor` still runs, but optional setup is missing, uncertain or contradictory: Java, SQLcl, Instant Client, the encryption-key source or `JAVA_TOOL_OPTIONS`. `ADT_ENV` is displayed only and never warns. |
| `FAIL` | A required prerequisite is missing or broken, Git or the `oracledb` module for instance, or a SQLcl too old for this project's APEXlang exports. The run exits non-zero. |

Plain `doctor` is read-only. It never runs `git pull`, never installs anything, never fetches or replaces SQLcl, and never stashes your work. By default it does check online for newer ADT.ai, Java, SQLcl, `oracledb` and Instant Client, which `-offline` turns off.

For ADT.ai itself, an editable or git install is compared against its own configured `origin`, and a normal install against the latest public GitHub release before falling back to PyPI metadata. Update subprocesses force English and UTF-8 settings, so a local language override cannot change what SQLcl, Oracle or pip report back.

A git checkout reads `UPDATE` only when `-update`'s pull would move it: its `HEAD` is an ancestor of `origin`'s, or `origin` holds commits it has never fetched. A checkout ahead of `origin`, or on a feature branch, is current.

A normal wheel installed inside another repository's `.venv` is still a package install. `doctor` does not mistake that enclosing repository for an editable ADT.ai checkout, and therefore cannot pull, stash, or switch the wrong project.

<br>

## Actions

`ACTIONS:` closes the run and lists only upgrades an online check actually found:

- `-update` appears when ADT.ai, `oracledb` or SQLcl is behind. `oracledb` counts because the full update reinstalls `requirements.txt`.
- `-sqlcl` appears only when SQLcl itself is behind, or when it is below the APEXlang floor below.
- A schema folder rename appears when the exported tree disagrees with the case your layout would write.
- The APEXlang SQLcl floor appears when this project exports APEXlang and your SQLcl is too old to do it correctly.

When none applies the whole section is omitted, header included: an up-to-date machine is offered nothing. Under `-update` and `-sqlcl` it always prints, because there it reports the actions that ran.

`-offline` checks nothing online, so the two staleness offers cannot appear there. The last two read your repository rather than the network, and are reported whether or not you are offline.

The offer is always for the latest release. A specific version is something you ask for, never something `doctor` proposes.

<br>

### Schema folder case

The schema token in `path_objects` and `path_apex` carries its own case: `<schema>` writes `app_owner/` and `<SCHEMA>` writes `APP_OWNER/`. Flipping it changes what the next export writes and moves nothing already on disk, so `doctor` reads the tree and offers the rename:

```text
ACTIONS:
  Schema folders do not match path_objects: <SCHEMA> writes them uppercase, the repo has app_owner, core.
  Rename them before the next export, git records a case-only move:
    git mv app_owner APP_OWNER
    git mv core CORE
```

`doctor` never performs it. A repository-wide move is yours to review and commit, and on macOS or Windows a case-only difference is invisible to the filesystem, so `git mv` is what actually records it. Nothing is reported when the tree already agrees, when the layout pins no schema level, or when the project has no config yet.

<br>

### APEXlang SQLcl floor

APEXlang exports need **SQLcl 26.2.2 or newer**. Below that, two defects fail quietly: files the export means to overwrite keep their previous content, and static files come back damaged. The export reports success either way, so the first honest signal is a deployed application behaving like an older one.

`doctor` therefore reports it as a failure of the setup, not as an offer you may decline: the `SQLcl` row reads `FAIL` and the run exits non-zero.

```text
CURRENT VERSIONS:
  SQLcl                | 26.2.1.0 ....................................... FAIL

ACTIONS:
  APEXlang exports need SQLcl 26.2.2 or newer, this is 26.2.1.0.
  26.2.2 fixed the overwrite default and the static-file corruption.
  Run `adtai doctor -sqlcl` to upgrade SQLcl only.
```

Only a project that already holds `apexlang/` exports at its configured export shape (`path_apex` / `apex_path_app` / `apexlang`, the same shape [validate](validate.md) matches) is held to the floor. A database-only project has no reason to care which SQLcl it has, and `doctor` still diagnoses a machine that has no project at all.

The verdict compares your installed version against a fixed number, so `-offline` reports it exactly as a plain run does.

<br>

## Landing a specific version

`-update` takes an optional version, and then ADT.ai goes to that release instead of the latest one. The version scopes to the **ADT.ai step alone**: the requirements and SQLcl steps still run after it, because the release you land on decides which requirements you need.

Which way the number moves is not this command's business. An older release is checked out exactly like a newer one, with no confirmation and no override flag, which is how you step back off a release that broke you and how you match the version a colleague is running. `v0.9.1` and `0.9.1` are the same request.

**A shorter version names a LINE, not one release.** `-update 0.3` lands the newest `0.3.x` release, whichever one that is; `-update 0.9.1` still lands exactly that one and only that one. Every ADT.ai release has always been three-part, so a shorter request can only ever mean "the newest on this line."

How the release is found depends on the install, and both spellings, `v<version>` and a bare `<version>`, work either way:

- **A git checkout**, the documented install, fetches tags from its own `origin` and resolves the release against them. Release tags live in the public repository, so a checkout that carries none (an editable install backed by the private DEV repository, for instance) cannot resolve one; this is a real limitation of that install, not a bug, since there is nothing else to check out. A pinned checkout sits on a detached HEAD, and a later bare `-update` returns it to the remote's default branch before pulling, so latest is always one command away.
- **Anything else** resolves the release against the public repository's own tags (no local checkout to ask) and installs it with pip. A fully-specified release tries the `v`-prefixed tag first and falls back to the bare spelling with no extra network round trip; a shorter one always asks the public repository which release the line's newest tag is, since pip cannot resolve an ambiguous ref on its own.

Every pip action runs as `<current Python> -m pip`. It therefore updates the environment that is running ADT.ai, even when a different `pip3` happens to appear first on `PATH`.

A version with no release **fails and stays put**. The `ADT.ai` row reads `FAILED` with the version and the remote it was looked for in underneath, the run exits non-zero, and the checkout does not move.

So a downgrade can never quietly install something newer than what it reached for. A value that is not a version at all is refused before any git command runs.

<br>

### Going back below the release that added this

A downgrade installs the older release in full, its own `doctor` included. Land on a release published before this flag existed and you are running a `doctor` that has never heard of it.

`-update <version>` there answers `unrecognized arguments`, and a bare `-update` answers `FAILED` on `git pull`, because the older code has no re-attach step and git will not pull onto a detached HEAD.

Nothing is broken and nothing is lost. The checkout just needs its branch back by hand:

```bash
git checkout main
git pull
python3 -m pip install -e .
```

Between two releases that both carry the flag, `-update <version>` and bare `-update` move the checkout in either direction on their own.

<br>

## Scaffolding a project

![Blank folder in. Project out.](images/doctor_init.png)

`-init` writes the project override config and `config/IDENTITY.yaml`, a `.gitignore` and a `.gitattributes` holding only what ADT writes into a project, copies the `config/patch_template/` scaffold verbatim, and writes the `connections/.gitkeep` and `connections/wallets/.gitkeep` placeholders.

Those source files are bundled in the wheel as package resources, so the same scaffold is available from a normal install with no source checkout beside it.

It creates no cache folders, no APEX credential folders, no connection YAML and no wallet contents. Existing generated files are listed under `SKIPPED (use -force to overwrite):`, and `-force` overwrites them. Every file it writes takes the project's `file_crlf` line ending.

Its rows are relative to the project folder, whose own name is never printed, and group under their folders like every other ADT file list; [`nested_files: False`](config.md#how-a-list-of-files-reads) flattens them.

`config/IDENTITY.yaml` is prefilled from the project folder's own `git config user.name`/`user.email` where it has one, and ships with a commented `db_schema` placeholder either way, the database half has no git equivalent to read. See [Developer identity](config.md#developer-identity).

`.gitattributes` is the one scaffolded file ADT.ai does not copy off its own root: that one is a merge driver for its `CHANGELOG.md` and says nothing about line endings.

What a project gets instead are the pins that make [LF everywhere](config.md#line-endings) a property of the repository rather than of each machine's `core.autocrlf`. `*.sql`, `*.apx`, `*.json`, `*.yaml`, `*.csv` and `*.md` are pinned `text eol=lf`, and anything under a `files/` folder is left untranslated, those being APEX static payloads mirrored byte for byte.

The pins follow the project's `file_crlf`: a project that sets it `True` gets them rendered `eol=crlf`, at scaffold time and on every sync, so the file and the exporter keep saying the same thing, and the block's own lines take that ending too. Project rules go below the managed block, never inside it.

The `.apx` and `.json` files under an `apexlang/` folder are pinned `text eol=lf` on three lines of their own, below the others, and stay that way under `file_crlf: True`: `export_apex -apexlang` writes LF whatever `file_crlf` says, since SQLcl's APEXlang compiler reads nothing else. Its `static-files/` payloads are left untranslated.

The patch templates are scaffolded because `patch -create` reads them from the **project** root, so a folder that only ships with ADT.ai is a folder nobody has. All six source files land verbatim; see [patch templates](patch_install.md#templates-and-the-project-sql-around-the-objects) for the slots and what each file does.

**Read `db_end/` before your first deploy.** Those three refresh every materialized view, gather schema statistics, and run every enabled daily job with a 60-second wait, and the APEX pair carries `<APEX_WORKSPACE>`, `<APEX_APP_ID>` and `<APEX_VERSION>` placeholders you fill in once. Delete what your deploy should not do.

Patch *scripts* are not scaffolded: `patch_scripts/` is per patch code and generated per patch, so there is nothing fixed to seed.

`adtai update`, `adtai upgrade` and `adtai init` are not commands. Each prints the generic error banner and points at the `doctor` flag that does the job.

Before replacing SQLcl, `doctor` downloads, extracts, validates, and makes the new launcher executable in a staging directory beside the live install. Promotion is a same-filesystem rename. If that final swap fails, both the live install and any pre-existing backup are restored; a corrupt or incomplete archive never moves the live install at all.

The download link is **scraped out of Oracle's SQLcl page rather than written down**. It must use `https` on `download.oracle.com`, and every redirect must keep the download on the original HTTPS host and port. Metadata requests may redirect to another HTTPS host, but never to HTTP.

A refused redirect stops before contacting its destination or writing the download. Extracted launchers retain ordinary executable permissions; archive entries cannot restore setuid, setgid, or sticky permission bits.

Oracle publishes no checksum for that archive by any route, so integrity rests on the transport and on the launcher validation above.

<br>

## Syncing the managed block

![The managed block stays current on an already-scaffolded project.](images/doctor_init.png)

`-init` alone never touches an existing `.gitattributes`/`.gitignore`: it skips them, and `-force` overwrites them whole. Neither shape reaches a project that scaffolded before a fix to the shipped template landed. `-init -sync` is the third option, and it only ever works alongside `-init`:

```bash
adtai doctor -init -sync
```

Both files carry the shipped template inside an ADT-owned block, between two fixed marker lines that never carry a version number:

```text
# >>> adtai managed
... the shipped template, verbatim ...
# <<< adtai managed
```

A fresh scaffold writes the block WITH its markers already in place, at the top of the file, so the very next `-sync` recognizes it immediately. `-sync` rewrites only the text between the markers; every line outside it, a project's own rules included, is left byte for byte alone, and a second run in a row is a zero-byte diff.

An already-migrated block is rewritten in place wherever it sits, never moved back to the top: a developer who filed their own rules above it keeps them there. A file with no markers at all is a legacy scaffold, and the block is inserted at the top instead.

Any of a legacy file's own lines that exactly (whitespace-trimmed) repeat a non-comment, non-blank block line are dropped as a now-redundant duplicate; the run reports which lines it removed.

**Broken markers refuse the whole file rather than guess.** A start with no matching end, an end with no matching start, or two blocks: nothing is written, and the run names the file and the line the break sits on.

After `.gitattributes` syncs, `-sync` also fixes the line endings of files **already committed** under its patterns: it runs `git add --renormalize` scoped to those patterns, so a `.sql` file committed before the `eol=lf` pin existed picks up the fix.

That step only **stages** the result, and a `.gitattributes` or `.gitignore` the run created or rewrote is staged with it, so the index never carries LF files without their rule. `-sync` never commits. Outside a git work tree staging, renormalizing and the override report below are silent no-ops.

A scaffolded file that differs from the template only in its line endings, a patch template an older ADT copied LF into a `file_crlf: True` project say, is rewritten in the project's ending rather than skipped. Any real edit keeps it skipped.

`-sync` also reports a line a project has added **below** the block that changes the effective value of one of its attributes (`git check-attr` resolves "later line wins" the same way git itself does). Only `-init -sync` reports overrides; the automatic export sync below never does.

The report groups its rows the same way `CREATED:` does above: `SYNCED:`, `UNCHANGED:` and `REFUSED:` per file, then `REMOVED DUPLICATE LINES:`, the `NORMALIZED EOL TO` groups and `OVERRIDES:` where any of those found something. Under `-sync` the two files appear only in those groups, never under `CREATED:` or the skipped group.

`NORMALIZED EOL TO CRLF:` and `NORMALIZED EOL TO LF:` are named by the ending git checks each file out with, so under `file_crlf: True` an APEXlang file still lands under LF. A large repository renormalizes thousands of files, so each lists its first ten and closes on one `... and <N> more` row.

`-sync` with no `-init` is a usage error, the same shape as any other doctor flag combination the parser refuses.

<br>

### Syncing before every export

Set [`auto_sync_git`](config.md#syncing-git-metadata-before-export) to run the same block-and-EOL sync automatically, on disk only, right before `export_db`, `export_apex` or `export_data` writes anything. It rewrites the block in an existing `.gitattributes` and `.gitignore`, never creates either, and converts only files git itself pins to `eol=lf`: `-text` payloads and a project's own CRLF rule are left alone.

An export never stages the result (that is `-sync`'s job alone) and never reports an override; it prints at most one short row, and only when something actually changed. `patch` never runs this sync. It is on by default; set the key `False` to turn it off.

<br>

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-offline` | No | off | Skip the online update checks and show local versions only. |
| `-update [VERSION]` | No | off | Run the full ADT.ai, Python requirements and SQLcl update. A version lands ADT.ai on that release, up or down, instead of the latest. Cannot be combined with `-sqlcl`. |
| `-sqlcl` | No | off | Upgrade SQLcl only, reading Oracle's own download page for the current release and replacing the resolved install folder. Runs immediately, and cannot be combined with `-update`. |
| `-init` | No | off | Scaffold the project config, `config/IDENTITY.yaml`, the root `.gitignore` and `.gitattributes`, `config/patch_template/`, and the connection and wallet placeholders. |
| `-sync` | No | off | With `-init`, sync the ADT-owned block in an existing `.gitattributes`/`.gitignore` instead of leaving it alone, renormalize tracked files under it, and report attribute overrides. Requires `-init`. |
| `-force`, `--force` | No | off | With `-init`, overwrite generated template files that already exist. |

Shared options (-root, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
