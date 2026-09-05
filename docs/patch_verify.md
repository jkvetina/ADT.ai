# Verifying a Deployed Application (deploy_verify_scan)

What a deploy asks an APEX application once it has landed it, where the answer is written, and what a clean answer does not prove. The deploy itself is on [patch_deploy.md](patch_deploy.md); landing an APEXlang tree is on [patch_import.md](patch_import.md).

## Why the step exists

An install script reporting `SUCCESS` says the import worked. It does not say the application still runs.

A page whose region query names a column the same patch renamed imports exactly as happily as one that does not. The failure appears when somebody opens the page, usually after the deploy has been reported clean.

The case this was built for: a patch imported 44 pages, every row in the table said `SUCCESS`, and the home page rendered `ORA-00904` in three regions.

So a deploy that lands an APEX application finishes by putting a question to it.

## What the scan reads

`APEX_APP_OBJECT_DEPENDENCY.SCAN` compiles every stored SQL and PL/SQL fragment an application holds: region sources, LOVs, processes, validations, computations, dynamic-action bodies, server-side conditions and column expressions. It records, per fragment, both the objects it resolved and the error it hit when it could not compile.

The second half is the one nothing in ADT had read. A fragment that fails to parse resolves to no object at all, so it leaves no dependency row and disappears from every other `APEX_USED_*` view. `deploy_verify_scan` reads `error_message` directly.

Both deploy flavours are covered by one pass. The scan uses the application actually deployed: a retargeted APEXlang import checks its target id. An application touched by both an install script and an import is scanned once.

An application whose own deploy row errored is not scanned at all. The deploy has already failed on its own terms, and scanning a half-installed application reports the half.

## What a run prints

```text
VERIFYING APPLICATIONS:
-----------------------
  APP 1000 | ERROR | 3 error(s) in 1116 fragments
    PAGE 1 | Column | sourcing | Column Name | PL/SQL: ORA-00904: "SOURCING": invalid identifier
    PAGE 101 | Validation | Company Must Have Contact | PL/SQL Expression | PLS-00222: no function with name 'VALIDATE_COMPANY_HAS_CONTACT' exists in this scope
    PAGE 223 | Region | Rhine Barge Detail | PL/SQL Expression | PLS-00103: Encountered the symbol "SELECT"
    LOG: patch/260902-1-CARGO/logs_DEV/20260902-194318_apex_scan_1000.txt
```

A finding is a stanza line rather than a table column, the same call `DEPLOYMENT ERROR:` makes for the same reason: an `ORA-` message in a cell destroys the layout at 80 columns.

**A finding fails the deploy.** Unlike the invalid-object list on the deploy page, this read is patch-scoped. It asks the application this patch just deployed, so it cannot fail a run over an object somebody else left invalid a month ago.

**A clean scan still prints its row.** The point of the section is that `SUCCESS` in the table above is no longer the last word, so the run has to show the question was asked. A section that appeared only on failure would read exactly like the behaviour it replaced.

## Where the log goes

One file per application, beside that target's deploy logs:

```text
patch/260902-1-CARGO/logs_DEV/20260902-194318_apex_scan_1000.txt
```

The timestamp format is `today_deploy`, shared with the deploy logs so the scan and the run it verifies sort together. The rest of the name is fixed and is not a config key.

**It is deliberately not a script `.log`.** Scan reports describe verification separately from installation. The completed-run receipt includes the required scan result, so a successful install followed by failed verification stays incomplete and is retried. A successful script log alone cannot make the next deployment skip its scan.

## What a green scan does not prove

It compiles fragments. It does not render a page.

An error in the query APEX generates *around* a fragment at run time, the classic-report wrapper or the interactive-report projection, compiles clean here and still fails in a browser. The log repeats this at the top of every file, so a reader meets the limit where they are rather than only in the source.

A green scan is a necessary condition, never a sufficient one.

## Turning it off

```yaml
deploy_verify_scan      : True
```

`False` skips the scan entirely: no scan, no log, no effect on the status.

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

Every failing outcome reaches the deploy status and the process exit code, exactly as a finding does.

**One case this makes noisy on purpose.** A patch shipping an APEX file for an application the target does not hold names that application on its result row, so the scan is asked about it and the instance answers `ORA-20001: g_security_group_id must be set`. That is now `FAILED` rather than silence, and it is worth reading: you deployed components for an application that is not there. Turn the key off for a patch that is deliberately removing them.

## What the scan does to the schema

It sets the workspace security context and the session PL/Scope flag on the connection the deploy already opened, and opens none of its own.

**The `DEPSCAN$` helper procedures the scan generates are always taken away again.** Install, scan and cleanup sit behind one lifecycle boundary, and the cleanup runs in a `finally`: the obligation to remove the helpers starts when the scan statement is issued, not when it returns, so a scan that fails halfway leaves none of them standing. The cleanup drops whatever currently matches the helper pattern, so running it twice is safe. A deploy that silently grew helper objects would be a worse bug than the one this closes.

Measured on APEX 26.1, a bare scan with no cleanup behind it left no `DEPSCAN` object on the schema at all, so on that release there is nothing to strand in the first place. The boundary is there for the release or the application that does leave one, and it costs a `finally`.

The same boundary is what `dependencies -refresh` runs its APEX axis through, so the two callers cannot drift on when the helpers get cleaned up.

It never recompiles the schema it is verifying. `dependencies -refresh` does that through `ensure_plscope`, which is right for an index refresh the user asked for and wrong for a check running at the end of a deploy.
