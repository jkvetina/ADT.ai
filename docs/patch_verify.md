# Verifying a Deployed Application (deploy_verify_scan)

What a deploy asks an APEX application once it has landed it, where the answer is written, what a clean answer does not prove, and how a failing answer is undone. The deploy itself is on [patch_deploy.md](patch_deploy.md); landing an APEXlang tree is on [patch_import.md](patch_import.md).

<br>

## Why the step exists

An install script reporting `SUCCESS` says the import worked. It does not say the application still runs.

A page whose region query names a column the same patch renamed imports exactly as happily as one that does not. The failure appears when somebody opens the page, usually after the deploy has been reported clean.

The case this was built for: a patch imported 44 pages, every row in the table said `SUCCESS`, and the home page rendered `ORA-00904` in three regions.

So a deploy that lands an APEX application finishes by putting a question to it.

<br>

## What the scan reads

`APEX_APP_OBJECT_DEPENDENCY.SCAN` compiles every stored SQL and PL/SQL fragment an application holds: region sources, LOVs, processes, validations, computations, dynamic-action bodies, server-side conditions and column expressions. It records, per fragment, both the objects it resolved and the error it hit when it could not compile.

The second half is the one nothing in ADT had read. A fragment that fails to parse resolves to no object at all, so it leaves no dependency row and disappears from every other `APEX_USED_*` view. `deploy_verify_scan` reads `error_message` directly.

Both deploy flavours are covered by one pass. The scan uses the application actually deployed: a retargeted APEXlang import checks its target id. An application touched by both an install script and an import is scanned once.

An application whose own deploy row errored is not scanned at all. The deploy has already failed on its own terms, and scanning a half-installed application reports the half.

<br>

## What a run prints

```text
VERIFYING APPLICATIONS:
-----------------------
  APP 1000 | ERROR | 3 error(s) in 1116 fragments
    PAGE 1 | Column | sourcing | Column Name | PL/SQL: ORA-00904: "SOURCING": invalid identifier
    PAGE 101 | Validation | Company Must Have Contact | PL/SQL Expression | PLS-00222: no function with name 'VALIDATE_COMPANY_HAS_CONTACT' exists in this scope
    PAGE 223 | Region | Rhine Barge Detail | PL/SQL Expression | PLS-00103: Encountered the symbol "SELECT"
    LOG: 20260902-194318_apex_scan_1000.txt
```

A finding is a stanza line rather than a table column, the same call `DEPLOYMENT ERROR:` makes for the same reason: an `ORA-` message in a cell destroys the layout at 80 columns.

**A finding fails the deploy**, unless the run passed `-continue`. Unlike the invalid-object list on the deploy page, this read is patch-scoped. It asks the application this patch just deployed, so it cannot fail a run over an object somebody else left invalid a month ago. What `-continue` changes is below, in "Waiving the verdict for one run".

**A clean scan still prints its row.** The point of the section is that `SUCCESS` in the table above is no longer the last word, so the run has to show the question was asked. A section that appeared only on failure would read exactly like the behaviour it replaced.

<br>

## Where the log goes

One file per application, beside that target's deploy logs:

```text
patch/260902-1-CARGO/logs_DEV/20260902-194318_apex_scan_1000.txt
```

The timestamp format is `today_deploy`, shared with the deploy logs so the scan and the run it verifies sort together. The rest of the name is fixed and is not a config key.

**The console names the file, not the folder.** Every log this section points at is in that one folder, and both halves of its path are already on screen: the patch code on the `DEPLOYING PATCH:` header and the environment on the connection header above it.

**It is deliberately not a script `.log`.** Scan reports describe verification separately from installation. The completed-run receipt includes the required scan result, so a successful install followed by failed verification stays incomplete and is retried. A successful script log alone cannot make the next deployment skip its scan.

<br>

## What a green scan does not prove

It compiles fragments. It does not render a page.

An error in the query APEX generates *around* a fragment at run time, the classic-report wrapper or the interactive-report projection, compiles clean here and still fails in a browser. The log repeats this at the top of every file, so a reader meets the limit where they are rather than only in the source.

A green scan is a necessary condition, never a sufficient one.

<br>

## Turning it off

```yaml
deploy_verify_scan      : True
```

`False` skips the scan entirely: no scan, no log, no effect on the status.

<br>

## Waiving the verdict for one run (-continue)

The key above is a project setting. `-continue` is the per-run answer, and it waives rather than skips:

```bash
adtai patch -name 260902-1-CARGO -deploy -app -continue
```

The scan still runs, the row still prints its real verdict, and every finding is still listed and still written to the log. What changes is what the run does about it:

| | Default | Under `-continue` |
| --- | --- | --- |
| The row and its findings | Printed | Printed |
| The deploy status and exit code | `ERROR` | `SUCCESS` |
| A failing `-app` import | Reverted | Left installed |
| A failed install script | `ERROR` | `ERROR` |

The last row is the one to read twice: `-continue` has never laundered a failed install script into a successful run, and it still does not. Only the scan verdict became advisory.

A waived row says so under its findings, so an `ERROR` above a `SUCCESS` run is never left to be inferred:

```text
  APP 1000 | ERROR | 3 error(s) in 1116 fragments
    PAGE 1 | Column | sourcing | Column Name | PL/SQL: ORA-00904: "SOURCING": invalid identifier
    -continue: this verdict did not fail the deploy and nothing was reverted
    LOG: 20260902-194318_apex_scan_1000.txt
```

The backup is still taken on an `-app` run, so the way back exists even though the run did not take it.

<br>

## What each outcome means

Nothing here raises out into a run that has a table to print, so every reason a scan produced no findings comes back as a row. The row is not one word, though, because a verification that did not happen is not a verification that passed:

| Status | What it means | Fails the deploy |
| --- | --- | --- |
| `SUCCESS` | Every fragment the application holds compiled | No |
| `ERROR` | Named fragments do not compile | Yes |
| `UNSUPPORTED` | The release is proven older than 24.2, so its dictionary carries no `ERROR_MESSAGE` to read | No |
| `FAILED` | The scan was attempted and did not complete: a query, a parse, the helper install, the database | Yes |
| `EMPTY` | The scan completed and analyzed nothing, with no evidence that nothing is the right answer | Yes |

`UNSUPPORTED` is the only outcome that passes without verifying anything, and the capability check has to prove it: an unreadable or unparsable release reads as *unknown, try anyway*, and whatever the scan then does decides the run.

**Zero analyzed fragments does not pass on its own.** It is either an application with nothing to analyze or a verification that never happened, and the count cannot tell those apart. So the deploy asks `apex_application_pages`, a view the scan does not write: an application holding no page holds no component, and zero is then the whole of its scope. Pages present, or a page count that answers no row at all, leaves the scan `EMPTY`.

Every failing outcome reaches the deploy status and the process exit code, exactly as a finding does, and `-continue` waives all three of them exactly as it waives a finding.

**One case this makes noisy on purpose.** A patch shipping an APEX file for an application the target does not hold names that application on its result row, so the scan is asked about it and the instance answers `ORA-20001: g_security_group_id must be set`. That is now `FAILED` rather than silence, and it is worth reading: you deployed components for an application that is not there. Turn the key off for a patch that is deliberately removing them.

<br>

## What the scan does to the schema

It sets the workspace security context and the session PL/Scope flag on the connection the deploy already opened, and opens none of its own.

**The `DEPSCAN$` helper procedures the scan generates are always taken away again.** Install, scan and cleanup sit behind one lifecycle boundary, and the cleanup runs in a `finally`: the obligation to remove the helpers starts when the scan statement is issued, not when it returns, so a scan that fails halfway leaves none of them standing. The cleanup drops whatever currently matches the helper pattern, so running it twice is safe. A deploy that silently grew helper objects would be a worse bug than the one this closes.

Measured on APEX 26.1, a bare scan with no cleanup behind it left no `DEPSCAN` object on the schema at all, so on that release there is nothing to strand in the first place. The boundary is there for the release or the application that does leave one, and it costs a `finally`.

The same boundary is what `rebuild -app` runs its dependency scan through, so the two callers cannot drift on when the helpers get cleaned up.

It never recompiles the schema it is verifying. A `rebuild` schema refresh does that through `ensure_plscope`, which is right for an index refresh the user asked for and wrong for a check running at the end of a deploy.

<br>

## Undoing a failed import (deploy_revert_on_scan_failure)

A failing scan used to end the run with the broken application still installed. The import is a whole-app replacement, the scan runs after it, and nothing in between kept what it replaced: the reader was told the target was broken and handed no way back.

```yaml
deploy_revert_on_scan_failure : True
```

On by default, and only ever on a `patch -deploy -app` run. It has no opinion about install scripts: the tree-level backup is what `-app` buys, and a per-app script is the patch's own SQL running against the target.

`-continue` suppresses the revert without touching the key: a run told to keep going past a failure and then handed its application back is a run that did not continue. The backup is still exported, so the way back is on disk either way.

**The revert is another import, so the backup is an export in the import's own format.** `apex import -input <tree>` is an id-based replacement, which is what makes it reversible: run it a second time against the tree the target held before, and the target is what it was.

So, immediately before the import writes, the live target is exported into the deploy's own log folder:

```text
patch/260907-1-CARGO/logs_DEV/20260907-194318_apex_backup_1000/
```

The timestamp is `today_deploy` again, so the backup, the import that overwrote it and the scan that judged the import all sort together. The import log names it on a `BACKUP` row beside the signature rows, whether or not the run ever needs it.

The backup keeps the static-file payloads that `export_apex -apexlang` deliberately drops. That export drops them so `-files` stays the repository's one static-file channel and the repo never holds two copies; a backup is not a repository, and an application restored without its stylesheets is not the application that was there.

<br>

## What a revert prints

Under the scan row that called for it, never in a section of its own:

```text
VERIFYING APPLICATIONS:
-----------------------
  APP 1000 | ERROR | 3 error(s) in 1116 fragments
    PAGE 1 | Column | sourcing | Column Name | PL/SQL: ORA-00904: "SOURCING": invalid identifier
    LOG: 20260907-194318_apex_scan_1000.txt
    REVERT: RESTORED
```

Every log this section names lives in `patch/<code>/logs_<ENV>/`, and both the patch code and the environment are on screen above it, so the rows carry the filename alone.

**The deploy stays failed.** Reverting undoes the write; it does not make the patch correct, so the run still ends `ERROR` and still exits non-zero.

| Outcome | What it means | On screen |
| --- | --- | --- |
| `RESTORED` | The backup imported back and the target exports the application it held before the deploy | `REVERT: RESTORED` |
| `FAILED` | The import refused, the connection did, or the application afterwards is not the one from before | `REVERT: FAILED`, with the reason under it |
| `SKIPPED` | There was nothing to put back, which is the ordinary case on a fresh application id | no row at all |

**`Import successful` is not the proof.** The backup tree is hashed as it is written, the application is exported again after the revert import and hashed the same way, and the two have to agree. A revert that ran and left the target somewhere else is `FAILED`, and the report names both hashes.

**The proof is a content hash rather than APEX's `CHECKSUM-SH256`, and that is measured rather than preferred.** An import bumps `APEX_APPLICATIONS.FILES_VERSION`, APEX's cache token for `#APP_FILES#` URLs, and the checksum moves with it. Measured on APEX 26.1.0: an application reverted from its own backup exported byte-identical to that backup and still read a different checksum, and re-importing the same bytes answered a different value again. The checksum is stable to read and unstable across an import, so comparing it would have reported every revert as `FAILED`. The hash is `apex_signature`'s, the same one the deploy's own `DEPLOYING` row carries.

<br>

## Holding the application shut (deploy_build_status)

The signature is read off the target, then the tree is imported over it. A developer who saves in the App Builder between those two moments has their change imported over with no trace: the gate checked, and it checked a state that no longer existed when the write happened.

```yaml
deploy_build_status     : restore
```

The deploy reads the signature and sets the application to `RUN_ONLY` as one step, before it stages a thing, and puts back the status it found afterwards.

**The read comes one statement before the write, because setting build status changes the application's export checksum.** Measured on APEX 26.1.0: `Run and Develop` and `Run Only` hash differently. Reading the target after the lock would compare a value the deploy itself had just written, and refuse every deploy the gate exists to protect.

| Value | What the deploy does |
| --- | --- |
| `restore` | Locks for the deploy, then puts back the status the application carried (default) |
| `run_only` | Locks and leaves it locked, for a target nobody develops on |
| `off` | Leaves build status alone, and writes no timeline |

**Build status is the lock because APEX offers no other.** The Builder's own application lock has no public API (`WWV_FLOW_LOCK.LOCK_APPLICATION` and its siblings carry no grant to any user), and an import deletes the lock row anyway (bug 39557252, reproduced on APEX 26.1.0). A lock the deploy itself drops cannot guard the deploy. Build status needs no version floor either: it goes through `APEX_UTIL.SET_APP_BUILD_STATUS`, the same call `patch_apex_build_status` already emits into a generated install script.

**It closes the door, not the room.** Measured on APEX 26.1.0: a Page Designer session that is already open saves successfully under `RUN_ONLY`, and the save lands. What `RUN_ONLY` refuses is Builder ENTRY, and reloading the page answers `Application not available for edit`. So the lock stops a new editing session starting mid-deploy and does not evict one already running. It narrows the window; the signature gate remains the guard.

**A task sandbox is never locked.** An import retargeted onto another id (`-app 1000123`) lands on a throwaway nobody is editing, so it is left alone whatever this key says, and locking it would strand a prototype on `RUN_ONLY`.

**Where `patch_apex_build_status` names a status for the target environment, that key wins.** It is a project's deliberate statement about how an application is left on an environment, and a lock restoring over it would silently unlock PROD.

<br>

## What the lock writes

A `BUILD STATUS` row on the import log, beside the signature rows:

```text
--   BUILD STATUS     | RUN_ONLY (was Run and Develop)
```

And its own timeline report, because the last two moments happen after that log is written:

```text
patch/260907-1-CARGO/logs_DEV/20260907-194318_apex_build_status_1000.txt
```

```text
--   APPLICATION      | 1000
--   MODE             | restore
--   STATUS           | HELD
--   BEFORE DEPLOY    | Run Only
--   LOCKED           | RUN_ONLY
--   AFTER IMPORT     | Run and Develop
--   FINAL            | Run Only
```

And the three moments that belong to this deploy on the `VERIFYING APPLICATIONS:` row, so a run says where it left the application without anyone opening a file:

```text
VERIFYING APPLICATIONS:
-----------------------
  APP 1000 | SUCCESS | 1116 fragments, no errors
    LOG: 20260907-194318_apex_scan_1000.txt
    BUILD STATUS: Run and Develop -> RUN_ONLY -> Run Only
```

**An application nothing locked prints no row**, which is every deploy under `off` and every task sandbox. The import log still carries its `BUILD STATUS` row with the reason on it.

**`AFTER IMPORT` is APEX's own doing, not ADT's.** An APEXlang import resets build status every time, and `apex_application_install.set_build_status`, which does pin the classic `f<id>.sql` path, is ignored by SQLcl's APEXlang importer, so there is nothing to pin with. The deploy therefore re-applies the final status after the scan instead of trying to carry the lock through the import.

**A lock that could not be taken never fails the deploy.** The signature gate is the guard and this is the courtesy in front of it, so a status that could not be read or set is reported as `FAILED` on the row and in the timeline, and the run continues.

**An id holding no application is `SKIPPED`, not a failure.** A fresh sandbox id is the ordinary case: there was nothing to export, and removing the application this run created would be a drop, which is [patch_drop.md](patch_drop.md)'s job and its ownership rail.

<br>

## Where the revert report goes

One file per reverted application, beside the scan that asked for it, and `.txt` for the same reason:

```text
patch/260907-1-CARGO/logs_DEV/20260907-194318_apex_revert_1000.txt
```

It carries the application, the outcome, the backup folder, the content hash before the deploy and the one after the revert, so the claim that the target came back is readable without the console. Its header says which value those are, so no reader compares them to the `SH256:` rows in the import log beside it.

<br>

## Turning the revert off

`False` takes no backup at all and leaves a failed import installed, which is what every release before this one did. It also does nothing on its own when `deploy_verify_scan` is `False`, because there is then no verdict for it to act on.
