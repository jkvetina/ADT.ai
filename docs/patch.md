# Patch Deployments (adtai patch)

![Assembled in order, deployed on purpose](images/patch.png)

`patch` turns committed repository changes into a release you can deploy to the next environment. It collects the files a patch names into ordered install scripts, deploys them through SQLcl, and archives what it delivered. Reads are the default, and every write needs an explicit flag.

Nothing is deployed by building a patch. What you get is a folder you can read, review and hand to whoever holds the keys to the target.

Every table names an object type in Oracle's singular spelling, such as `TABLE` and `VIEW`, count tables included. It is the spelling `-type` takes.

<br>

## Examples

Preview the recent commits and the patch folders that exist:

```bash
adtai patch -target DEV
```

Build the patch for a code, here the ticket number a commit subject carries:

```bash
adtai patch -target DEV -name 12 -create
```

Deploy that folder exactly as it stands:

```bash
adtai patch -target DEV -name 12 -deploy
```

Narrow the preview to the commits a pattern matches:

```bash
adtai patch -target DEV -search %report%
```

Archive delivered patches by number, or by pattern for a whole month:

```bash
adtai patch -target DEV -archive 12
adtai patch -target DEV -archive 202608%
```

Regenerate the schema install scripts from the exported files alone:

```bash
adtai patch -install
```

<br>

## Output

A bare run tops up the commit store, lists what is still outstanding, and lists the folders on disk:

```text
APEX DEPLOYMENT TOOL - PATCH
----------------------------

REBUILDING COMMITS:
-------------------
    BRANCH | adt/docs-capture

  3 COMMITS ................. 33%                                      0:00:00 
  3 COMMITS .................................. 67%                     0:00:00 
  3 COMMITS .................................................... 100%  0:00:00 

RECENT UNPATCHED COMMITS:
-------------------------

  #   MESSAGE
  -   -------------------------------------
  3   @dev #12: add the monthly report view
  2   @dev #11: export the schema as files
  1   @dev #1: scaffold the project

RECENT PATCH FOLDERS:
---------------------

TIMER: 0s
```

- `REBUILDING COMMITS:` appears only when there are commits to hash. It runs before every action that reads commits, so a patch never records a commit number the store disagrees with. `-install`, `-archive` and `-drop` read none and never rebuild.
- `RECENT UNPATCHED COMMITS:` holds the work still to be addressed, so a commit already carried by a folder on disk is not listed. `-commit <n>` reaches a hidden one by number.
- `RECENT PATCH FOLDERS:` lists the folders newest first, so the patch you just made is the top row. Its `STATUS` cell is the newest deploy log for that folder, as `<OUTCOME>/<TARGET>`.
- Both tables are narrowed, which is what `RECENT` names: the folder listing is capped at `patch_show_patches`, and `-by`, `-my` and `-recent` cut both, so `adtai patch -my` means your commits and your patches.

<br>

## Naming a patch

`-name` takes an **id**, a **patch code**, or a **full folder name**, and all three work in every mode. The code you pass becomes the folder name, under a `yymmdd-seq-` prefix the run mints.

- An all-digit value is the ticket number in the code's first segment, matched exactly, so `12` never selects a patch whose label merely contains those digits.
- A value naming an existing folder rewrites that folder rather than growing a second one.
- A well-formed folder name that exists nowhere is refused: that is a typo, not a new code.

Selection for an action is **whole-value**: the folder name, the patch code, or the id. A value that only occurs inside a folder name selects nothing and the run stops, naming what it saw, and a value matching two folders stops the same way rather than choosing. A code matching no folder prints the folder listing and exits `2`.

<br>

## The commits a patch carries

With `-name <CODE>` and no `-search`, the code is the search term, matched against the commit subject alone. `-search` takes a SQL LIKE pattern instead, matched case-insensitively against the subject, the author and each changed path:

```text
RELEVANT COMMITS FOR "%report%":
--------------------------------

  #   MESSAGE
  -   -------------------------------------
  3   @dev #12: add the monthly report view
```

- A term carrying no `%` is searched as `%term%`. Escape a literal wildcard with a backslash.
- **`-search` is a discovery run, so `-create` beside it lists the commits instead of building.** Finding the right commits is what the flag is for, and a build chosen by nothing is a build over every commit the search matched. Add `-commit`, `-ignore` or `-force` once you know which ones you want and the same command builds. Nothing is written in the meantime, so an existing patch folder, its `patch_scripts/` and its snapshots survive the search untouched. A `-create` with no `-search` is unaffected.
- `-commit` and `-ignore` take a number, a hash prefix, or a range (`12`, `12+`, `12-40`). An all-digit ref shorter than seven characters is a number, never a hash prefix, so `-ignore 1` cannot also drop a commit whose hash opens on `1`. A commit you name is an instruction and is never filtered out.
- `patch_commit_pattern` in `config.yaml` keeps commits whose subject does not match that shape out of every patch. An explicit `-search` or `-commit` overrides it.
- **A `-create` whose name matches no commit subject stops with `NO COMMITS MATCHED "<CODE>"`**, quoting the pattern it ran, counting the commits that passed every other filter, and offering two options: `-search PATTERN` to select them by a different term, or `-commit N` and `-ignore N` to select them by number, hash prefix or range. It closes on the habit that avoids the screen altogether, **putting the patch name in the commit message**: the name is matched against subjects, so a repository that writes its ticket number into the subject is found by `-name` alone. `NO COMMITS FOUND ... commits scanned` is the other failure and a different fix, the scan reached nothing at all, so raise `patch_scan_commits`.
- **A commit that committed a patch folder hides the older commits of its code.** The newest commit adding a `.sql` directly in `<patch_root>/<folder>/` for that code marks everything older as shipped, folder on disk or archived, and a commit touching nothing but patch folders goes too. `-force` keeps them, `-commit` names one, and the folder `-name` resolves to never hides its own. With nothing left, `-create` stops with `NO NEW COMMITS FOR "<CODE>"`.
- The commits come from the per-branch store `rebuild` maintains, at `repo_commits_file`. There is one store, shared with `search` and `calendar`, and `patch` tops it up rather than keeping a copy. Use `adtai rebuild` to rebuild one from scratch.

<br>

## Building and deploying are two runs

`-create` builds, `-deploy` ships what is on disk, and neither does the other's job:

```text
RELEVANT COMMITS:
-----------------

  #   MESSAGE
  -   -------------------------------------
  3   @dev #12: add the monthly report view

PROCESSED FILES: SANDBOX
----------------
  - sandbox/database/views/
    - monthly_report_v.sql
    - order_summary_v.sql

PATCH FILES:
------------
  - patch/260822-1-12/SANDBOX.sql
```

`-deploy` never creates a folder, rewrites the selected one, or re-orders its files, so what deploys is what was reviewed. The building flags are accepted beside it and reported under `IGNORING WITH -deploy:` rather than silently applied.

What the two runs write, and every section they print, is on [patch_deploy.md](patch_deploy.md). What goes into the folder in the first place is on [patch_install.md](patch_install.md).

<br>

## Which version of a file ships

`-create` snapshots the **committed** version of each file: the blob at that file's newest commit inside the patch window, so an uncommitted working-tree edit cannot leak into a deployment. Three mutually exclusive flags override it, `-local`, `-head` and `-nosnap`.

All four modes, what each one costs, and which two refs `-head` reads are on [patch_content.md](patch_content.md).

<br>

## Workspace static files

A patch carries the workspace static files its commits changed; `-create -files_ws` carries all of them. Each one deploys as a self-contained base64 PL/SQL block that `-deploy` runs through SQLcl, the same script a DBA can run by hand. Details on [patch_content.md](patch_content.md#workspace-static-files).

<br>

## Shipping an APEX application whole

`-app` ships an application whole instead of as the components that changed. The application's own exported files pick the mode: an `apexlang/` tree ships as the tree, anything else as its `f<id>.sql`. Its optional value is where the tree lands rather than which applications ship.

`-app <id>` stamps that target with the deployer and time after import, whether the id names a sandbox or the source application itself. Bare `-app` leaves the Builder audit author unchanged.

Both modes, the stale export refusal and the retarget rules are on [patch_app.md](patch_app.md). The import's staging, signatures and refusals are on [patch_import.md](patch_import.md), and the loop around it on apex_round_trip.md.

`-deploy -app`'s drift check is measured against the target as it stood immediately before the `RUN_ONLY` lock, not after: setting build status moves the application's own export checksum, so a deploy-in-place no longer refuses on drift that reading it post-lock would have invented. See [patch_verify.md](patch_verify.md#deploy_build_status) for the measurement.

<br>

## What a deleted object generates, and what a moved one does not

A patch window that deletes an object's file ships a `DROP` script for it under `patch_scripts/objects_after/`, guarded so a re-deploy is not an error:

```text
PROMPT -- SCRIPT: patch_scripts/PATCH309/objects_after/drop.package_body.core_lock.sql
@"./patch_scripts/objects_after/drop.package_body.core_lock.sql";
```

**A table or a sequence is never dropped by a patch.** Both are listed in `immutables` in `config.yaml`, so their DROP script is still written but linked commented out, and dropping stays an edit you make on purpose:

```text
PROMPT -- SCRIPT: patch_scripts/PATCH830/objects_after/drop.table.app_orders.sql
-- [!] IMMUTABLE TABLE, NEVER DROPPED BY A PATCH
--@"./patch_scripts/objects_after/drop.table.app_orders.sql";
```

Nor is either created a second time. A table carrying an ALTER, Oracle's own diff or one you wrote yourself, is always linked commented out this way, marked `REPLACED BY ITS ALTER` and naming the script, whether or not the file says `IF NOT EXISTS`.

A table or sequence with no ALTER at all is still commented out when the target already holds it and the file says no `IF NOT EXISTS`, marked `NEVER RE-CREATED BY A PATCH`; a changed sequence otherwise ships an `ALTER SEQUENCE` under `tables_after/` instead, both described on [patch_templates.md](patch_templates.md). `immutables: []` turns all of this off.

Three deletions earn nothing, and each is a different question:

| The window | Why no `DROP` |
| ---------- | ------------- |
| created and deleted the object itself | the target sits at the pre-window state and has no such object |
| deleted a `GRANT` file | a grant is granted or it is not; there is nothing to drop |
| only MOVED the file | the object never left, so dropping it would delete live data |

The last one is what `export_db -groups` does: it arranges a type folder's files into sub-folders, so `packages/core_lock.sql` becomes `packages/CORE/core_lock.sql` and git records a delete plus an add.

**The unit is the object, never the file path.** Those two paths are the same row of `user_objects`, so a deleted file earns no `DROP` when the object it held is still exported anywhere under its own type folder, or when the same window added that object at another path. A group folder is read that way everywhere else too: a moved file keeps its object type, so it installs in its own `patch_map` section rather than at the end, and the staleness check that refuses a patch built on a stale export still covers it.

A generated `DROP` is written per run and never carried into a re-created folder, which is on [patch_install.md](patch_install.md).

<br>

## After a patch has landed

`-archive` zips the patch folders you name into `patch_archive/<YYYY-MM>/` and removes them from `patch/`; named nothing, it only lists. `-drop` removes the sandbox APEX applications a `-deploy -app <id>` run created, so a workspace does not fill with dead copies.

Each application gets a receipt at `<path_apex>/logs_<ENV>/<timestamp>_apex_drop_<application-id>_<DELETED|FAILED>.log`: `-target` supplies `<ENV>`, while the value passed to `-drop` is the application id. They are on [patch_archive.md](patch_archive.md) and [patch_drop.md](patch_drop.md).

<br>

## Uploading static files as you save them

`-upload` uploads every static file you save straight into APEX, and `-once` uploads the whole folder and exits. It is the smallest thing `patch` ships, one saved stylesheet rather than a release, so it reads no commit and is refused beside every other verb. Everything about it is on [patch_upload.md](patch_upload.md).

```bash
adtai patch -upload -app 100
```

<br>

## Building from hashes instead of commits

`-hash` builds the patch from what the working tree no longer agrees with the target about, which reaches work no commit window covers. `-baseline` records the state you believe the environment is at. Both are on [patch_hash.md](patch_hash.md).

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-name`, `--name` | No | none | The patch this run acts on, in every mode: an id, a patch code, or a full folder name. With no `-search` beside it, it is also the commit filter, matched against commit subjects. On its own it inspects and builds nothing. A value matching no patch lists the available patches and exits `2`. |
| `-target`, `--target` | No | none | The environment the patch is for. `-deploy`, `-drop` and a bare `-hash` or `-baseline` refuse without it. The install scripts `-create` writes are the same for every target: `-deploy` runs the `.[ENV].` templates and scripts and the `patch_apex_build_status` of its own `-target`. Without it `-create`'s table ALTERs connect to the connection file's default environment; `-upload` uses that default too. |
| `-create`, `--create` | No | off | Build the patch named by `-name`, which is mandatory beside it. An existing folder is rewritten; a well-formed folder name that exists nowhere is refused. |
| `-deploy`, `--deploy` | No | off | Deploy the patch named by `-name`, mandatory beside it, exactly as it stands on disk. Beside `-create`, only a name with no folder is built first. |
| `-force`, `--force` | No | off | Override a refusal. With `-deploy -app`, import past a refused signature check or a full export beside the retarget. With `-create`, rebuild a folder carrying a deploy log: logs are kept and generated artifacts follow the new commit window. With `-drop`, remove a sandbox somebody else created. With `-name`, keep the commits an earlier patch of that code already shipped. |
| `-continue`, `--continue` | No | off | With `-deploy`, keep running the remaining install scripts after one fails, instead of stopping and rolling back. A failing `deploy_verify_scan` verdict also becomes advisory: the row still prints, the run still ends `SUCCESS`, and nothing is reverted. A failed install script still ends the run `ERROR`. It does not resume an interrupted run. |
| `-by`, `--by` | Yes | none | Limit commits and patch folders to an author, as a case-insensitive substring of the commit author email. |
| `-my`, `--my` | No | off | Limit commits and patch folders to you, matched against `IDENTITY.yaml` or `git config user.email`. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | off | Only commits and folders from the last `DAYS` days, or a fraction of a day (`1/24` is the past hour). A whole-day window counts today, so `-recent 1` is today. Bare `-recent` means `1`. |
| `-search`, `--search` | Yes | none | Filter commits by a SQL LIKE pattern, matched against the subject, the author and each changed path. `%` is any run, `_` is one character, and a term with no `%` is searched as `%term%`. A discovery run: `-create` beside it lists the commits and builds nothing until `-commit`, `-ignore` or `-force` narrows them. |
| `-commit`, `--commit` | Yes | none | Include commit numbers, hash prefixes or ranges (`12`, `12+`, `12-40`). One flag takes several refs, comma- or space-separated, and the flag repeats. |
| `-ignore`, `--ignore` | Yes | none | Exclude commit numbers, hash prefixes or ranges, in the same shape as `-commit`. |
| `-app [ID]`, `--app [ID]` | No | off | Ship every APEX application the patch touches whole instead of as the components that changed. The application's own export format picks the mode: an `apexlang/` tree ships as the tree, anything else as its `f<id>.sql` full export with the components dropped. Bare, no application id changes; `ID` lands the tree on that application id instead, with the alias derived alongside it, and on `-create` names the APEXlang application's `init` and `end` scripts and their logs for `ID`, with a `DEPLOY.sql` comment between them. One id per run. Refuses the build when a full-export application changed after the export it would ship; an APEXlang application needs no `f<id>.sql` and is never compared against one. On `-deploy` it also imports the `apexlang/` tree, refusing on target drift. With `-upload` it is the one application to upload into, and required. |
| `-hash [FILE]`, `--hash [FILE]` | No | off | Build the patch from what the working tree no longer matches the baseline on, instead of from commits. `FILE` names the baseline; omitted, it is `patch_hashes/baseline.<TARGET_ENV>.log`. Forces the `local` content mode. |
| `-baseline [FILE]`, `--baseline [FILE]` | No | off | Record every current file hash as this target's deployed baseline, and store every table beside it under `baseline.<TARGET_ENV>/`, overwriting both whole. Builds nothing and opens no database. |
| `-install`, `--install` | No | off | Write `config/install/<SCHEMA>.sql` for each exported schema from the checked-out files; `-schema` picks the schemas. Needs no name. Details on [patch_install.md](patch_install.md#the-install-script). |
| `-upload`, `--upload` | No | off | Upload static files into APEX as you save them, or the whole folder once with `-once`. Needs `-app ID`, no name, and is refused beside every other verb. On [patch_upload.md](patch_upload.md). |
| `-folder`, `--folder` | No | the exported folder | With `-upload`, the folder to watch. |
| `-interval`, `--interval` | No | `1` | With `-upload`, seconds between passes over the folder. Refused with `-once`. |
| `-once`, `--once` | No | off | With `-upload`, upload the folder once and exit instead of watching. |
| `-show`, `--show` | No | off | With `-upload`, list what the folder holds before the watch starts. |
| `-archive`, `--archive` | No | none | Archive folders by ticket number or LIKE pattern; omit refs to only list. One flag takes several refs and mixes both kinds. A ref matching nothing archives nothing and still exits `0`. `-archive %` takes every folder; `\` escapes a literal `_` in a ticket-number ref, quoted. Closes with `ALL PATCH FOLDERS:`, every folder left on disk. |
| `-drop ID [ID ...]`, `--drop` | No | none | Remove the sandbox APEX applications a `-deploy -app ID` run created. Ids only: no `-name` and no patch folder, and `-target` is required. An id is taken only when it is a derived sandbox, `<application><task>` carrying the derived `<SOURCE_ALIAS>_<task>` alias, so an application's own id refuses and names it. A sandbox drops when its recorded creator is your `apex_account` in `config/IDENTITY.yaml` or when it records no creator at all; anybody else's needs `-force`. Every id is checked before the first one is dropped. |
| `-local`, `--local` | No | off | Snapshot the working-tree file instead of its committed version. Mutually exclusive with `-head` and `-nosnap`. |
| `-head`, `--head` | No | off | Snapshot the newest committed version of each file, taken from the local branch or the remote default branch, and skip the newer-commit warning. Runs `git fetch --prune origin` first, before anything reads history, best effort on an offline repository. Which ref wins is on [patch_content.md](patch_content.md). Mutually exclusive with `-local` and `-nosnap`. |
| `-nosnap`, `--nosnap` | No | off | Write no snapshots; link each repo file where it already lives. Mutually exclusive with `-local` and `-head`. |
| `-files_ws`, `--files_ws`, `--files-ws` | No | off | With `-create`, carry every workspace static file, not only the ones the selected commits changed, in the version the content mode selects. Application static files are not widened. Exits `2` on a run that builds nothing. With `-upload` it names where the uploads land instead, the workspace files rather than the application ones. On [patch_content.md](patch_content.md#workspace-static-files). |
| `-branch`, `--branch` | No | current branch | Scan the named branch's history instead of the checked-out one. Read-only. A name resolving to no ref fails the run. Refused beside `-install`. |

Shared options (-root, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
