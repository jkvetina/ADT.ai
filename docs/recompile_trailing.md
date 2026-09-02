# Stripping Trailing Whitespace (adtai recompile -trailing)

What `-trailing` rewrites and why, which object types it covers, the separate path a view takes, and what the sweep guarantees while it runs. The command and its flags are on [recompile.md](recompile.md).

## What it fixes

`-trailing` fixes the version-control noise the export creates. `export_db` strips trailing whitespace from every line it writes, so an untouched 10k-line package still differs from the database's stored source on every export. `-trailing` repairs the *source* side once per schema.

There is no preview mode and no second flag to confirm with: asking for `-trailing` is asking for the fix. It lists each rewritten object as it goes under an `UPDATED <n> OBJECTS:` header, in the shared object listing ([console](console.md)), and a clean schema prints `UPDATED 0 OBJECTS:`, the proof the pass ran.

```text
UPDATED 2 OBJECTS:
------------------

           PROCEDURE | APP_UTIL
                     | APP_NOTIFY
                     |
```

The safety is structural rather than a prompt: an object with nothing to strip is never touched, and stripping trailing whitespace cannot change what the code does.

## Which objects, and the view exception

Scope is `PACKAGE`, `PACKAGE BODY`, `PROCEDURE`, `FUNCTION`, `TRIGGER` and `VIEW`. Types and type bodies are deliberately out.

The first five come from `user_source`, which round-trips faithfully. **Views take a separate path**, having no `user_source` rows: their only faithful source is `user_views.text`, a LONG holding the `SELECT` alone.

So a view is rebuilt as `CREATE OR REPLACE FORCE VIEW <name> (<columns>) AS <text>`, with the column list re-read in `column_id` order. That list is not optional: a view whose select list has no aliases would otherwise fail or silently rename its columns.

Two classes of view are skipped rather than rebuilt, since neither property survives a rebuild:

- **Views carrying a constraint.** `WITH READ ONLY` and `WITH CHECK OPTION` each record one, and a rebuild would silently drop the clause.
- **Editioning views**, which are not maintained through a plain `CREATE OR REPLACE VIEW`.

A view whose column list is not a plain unquoted identifier is reported as a failed object rather than guessed at.

## What the sweep guarantees

- **Nothing else changes.** Each line is stripped exactly the way the export strips it; blank lines stay blank and indentation is untouched.
- **An object with nothing to strip is never touched**, so no `LAST_DDL_TIME` churn and no needless dependent invalidation.
- **One object at a time**, fetched, rewritten and finished before the next is read, so a colleague's change made in that window is not clobbered.
- **Disabled triggers stay disabled.** `CREATE OR REPLACE TRIGGER` re-enables one, so the status is captured first and restored after.
- **Wrapped objects are skipped**, since their stored source is an obfuscated blob.
- **A view's grants survive**, which is why the view is replaced rather than dropped and recreated.

`CREATE OR REPLACE` invalidates dependents, so follow a sweep with a plain recompile pass. Any failed rewrite is listed below the table as `  <NAME>) <error>` and makes the run exit non-zero.
