# The Exported Files (adtai export_db)

![Look first. Move only what you named.](images/export_db_layout.png)

What an exported file looks like, the folder tree it lands in, how to reorganize that tree into per-group subfolders, what happens when one object ends up in two places, and how to record what an environment actually holds. The command and its flags are on [export_db.md](export_db.md).

## What the exported DDL looks like

Normalization is where the export earns a readable comparison. Bodies are preserved; only the parts old ADT rewrote are rewritten:

- **Definition lines** for `PACKAGE`, `PACKAGE BODY`, `PROCEDURE`, `FUNCTION`, `TRIGGER`, `TYPE`, `TYPE BODY` and `SYNONYM` lose the owner qualifier, a simple quoted uppercase name is unquoted and lowercased, and the terminator is cleaned. Body text is never touched.
- **Views** drop the metadata header's column list and default collation, and format quoted select-list items lowercased, one per line, preserving expression text and layout from `FROM` onward. A select list carrying a comment is left exactly as the database returned it.
- **Tables** render constraints as `--`-separated blocks (CHECK, PK, UNIQUE, FK), strip only the exported owner, keep FK `ON DELETE` actions, `INTERVAL` alignment and trailing `INMEMORY`, and drop generated `ENABLE` / `USING INDEX`.
- **Indexes** use `CREATE INDEX IF NOT EXISTS`, unquote simple identifiers and reflow multi-column lists, preserving string literals inside expression indexes.
- **Recycle-bin objects** (`BIN$...`) are ignored everywhere, scheduler job arguments are preserved, and an explicit sequence `MAXVALUE` is never stripped as noise.

A trigger's status arrives as an `ALTER TRIGGER` appended inside the `CREATE TRIGGER` block. The `ENABLE` form is dropped as the default state and the `DISABLE` form is moved below the block's `/`, so a disabled trigger exports as a runnable file:

```sql
CREATE OR REPLACE TRIGGER trg_d
    BEFORE INSERT ON tab
    FOR EACH ROW
BEGIN
    NULL;
END;
/

ALTER TRIGGER "APP"."TRG_D" DISABLE;
```

Left inside the block the whole file is one statement and raises `PLS-00103`. Only a real statement line is matched, so a body logging those words inside a string literal is untouched.

A CHECK condition is stored verbatim by Oracle, so a domain list written over several lines with comments arrives that way and is exported that way, re-hung under the `CHECK (` block:

```sql
    CONSTRAINT doc_chk_type
        CHECK (
            doc_type in (
                -- customer facing
                'INVOICE',
                'CREDIT_NOTE', -- issued when an invoice is cancelled
                'MEMO'
            )
        )
```

Blank lines inside it are the one thing dropped, since an empty line ends the buffer in a plain SQLcl session.

When a PRIMARY KEY or UNIQUE constraint was added after table creation and tied to a pre-existing index, Oracle exports it as a separate `ALTER TABLE ... USING INDEX` plus a `CREATE INDEX`.

`export_db` folds that back inline, keeping the constraint name and columns and dropping the index name, and writes a `<table>.fix.sql` companion holding the recovery script. The companion is removed when the table no longer has such a constraint.

## Where files land

`path_objects` in `config.yaml` is the export path **template**, not a literal folder. It resolves exactly two placeholders:

| Placeholder | Resolves to |
| ----------- | ----------- |
| `<schema>` | The schema or owner name, lowercased. |
| `<SCHEMA>` | The same name, uppercased. |
| `<object_type>` | The per-type folder from `object_types` (`views/`, `packages/`). Appended automatically when the template omits it, which is the legacy `'database/'` layout. |

So the shipped `'<schema>/database/<object_type>/'` writes:

```text
sandbox/database/procedures/adt_fixture_recent_prc.sql
sandbox/database/tables/adt_fixture_ddl_log.sql
sandbox/database/views/adt_fixture_ddl_log_v.sql
sandbox/database/grants/SANDBOX.sql
```

`object_types` gives each type its folder and its file extension, and takes either spelling. The two-item list the shipped config uses and the mapping form mean the same thing to every command that reads the key:

```yaml
object_types:
    DATA  : ['csv_data/', .dat]
    VIEW  : {folder: views/, extension: .sql}
```

## The schema token carries its own case

Oracle stores identifiers uppercase, but ADT.ai learns a schema name from a connection key or a `-schema` argument, where `app_owner` is as likely as `APP_OWNER`, so somebody has to choose. The template chooses: `<schema>` writes `app_owner/` and `<SCHEMA>` writes `APP_OWNER/`.

That matters when migrating a project from old ADT, which left uppercase folders behind. Set `path_objects: '<SCHEMA>/database/<object_type>/'` and the export writes into the tree you already have, instead of beside it.

`path_apex` carries the same token and reads it the same way, and the two keys are independent, so a project can spell them differently. An `<object_type>` folder has no cased form, because its name is your own text from `object_types`; change the folder there.

**Switching the case moves nothing that is already exported.** On Linux the next run writes a second tree beside the first; on macOS and Windows the case-only difference is invisible to the filesystem until version control reports a rename nobody made. `adtai doctor` reports the mismatch and prints the `git mv` for each folder.

## A token nothing resolves is refused

Old ADT's `{$NAME}` substitution syntax means nothing here. A template such as `'{$INFO_SCHEMA}/database/'`, which is the natural copy-paste when migrating a project, is rejected:

```text
CONFIGURATION INVALID
---------------------
Unresolved placeholder in config path_objects: {$INFO_SCHEMA}
  Value: {$INFO_SCHEMA}/database/
  ADT.ai substitutes only <schema>, <SCHEMA> and <object_type> in path_objects; '{$NAME}' is old ADT syntax and would be written out as a literal folder name.
  A schema token carries its own case, so '<schema>' writes 'app_owner/' and '<SCHEMA>' writes 'APP_OWNER/'. An object type folder is spelled by object_types in config.yaml, so '<object_type>' has no cased form.
  Fix path_objects in config.yaml (e.g. '<schema>/database/<object_type>/').
```

Any other angle-bracket token is refused the same way, and for the same reason: the substitution matches a spelling exactly, so `<Schema>`, `<OBJECT_TYPE>` and a plain typo would all reach the filesystem as a real directory named after the token. `path_apex` resolves only the schema token, so an `<object_type>` there is refused too.

The run stops before writing anything, and the same rejection applies to every command that renders the template, so a guarded command cannot leave an unguarded one exporting into the placeholder folder. A folder an earlier run already built is left on disk untouched.

## Object groups

`-groups` is a **move action**, not an export modifier. With it, `export_db` does not connect or export anything: it scans the files you have already exported and works out which belong in per-group subfolders (`<object_type>/<group>/PREFIX_...`) so a large folder stays navigable. Group folder names are always uppercased.

**It lists, it does not move.** `-groups` on its own is a report you read; `-force` beside it is what applies the listing.

There are three forms:

1. **Auto-detect, bare `-groups`.** Clusters the flat files in each object-type folder by leading prefix. A cluster of at least `groups_min` files (default `5`) becomes a group named after the detected prefix, first by the two-word prefix (`INV_BILLING`), then falling back to the one-word prefix (`INV`).
2. **Single prefix, `-groups INV_BILLING`.** Routes only the files whose name starts with that prefix.
3. **Prefix list, `-groups INV_BILLING ORD, AP`.** Takes a space- and comma-separated list and routes only those.

Naming the prefixes also narrows the listing. Bare `-groups` proposes a layout for the whole export, so the files it left flat are part of the proposal and appear under `UNMATCHED (LEFT IN PLACE):`.

```bash
adtai export_db -groups
adtai export_db -groups INV_BILLING
adtai export_db -groups INV_BILLING -force
adtai export_db -groups INV_BILLING ORD, AP -force
adtai export_db -groups INV_BILLING ORD, AP -force BILLING
```

The listing is a `PLANNED MOVES:` section, one line per target group with its files under it:

```text
PLANNED MOVES:
--------------

  AP
    - tables/ap_invoice.sql
    - tables/ap_payment.sql

  INV_BILLING
    - tables/inv_billing_header.sql
    - views/inv_billing_summary.sql
```

Groups sort A to Z and so do the files inside each one. A group gathers what it takes from every object type, which is why `tables/` and `views/` rows sit side by side under one name.

Add `-force` and the same run moves those files and reports `Moved <n> file(s).` **`-force` moves only what the listing showed**, so it cannot touch, flatten or rename a group you did not name, including one you arranged by hand. `-force` without `-groups` is an error rather than a flag that quietly does nothing.

**`-force GROUP` puts every prefix you named into that one folder.** By default each prefix gets a folder of its own, so `-groups INV_BILLING ORD -force` writes `packages/INV_BILLING/` beside `packages/ORD/`; adding a name writes one folder holding both, across every object type the prefixes reach.

It renames the prefixes you listed and nothing else, so `-force GROUP` beside a bare auto-detecting `-groups` is refused, exit `2`: folding a whole detected layout into a single folder is not a layout.

The standalone preview cannot show the merged folder, the name arriving only on the flag that applies, so the run that applies prints its own `PLANNED MOVES:` with the merged name above the move.

Before moving, `export_db` enforces **per-object-type filename uniqueness**. If applying the plan would put the same object name in more than one place under an `<object_type>/` subtree, it reports the collisions and aborts without moving rather than overwriting or duplicating a file.

Two object types can be configured onto one folder, and the shipped config does it twice: `PACKAGE` and `PACKAGE BODY` both write `packages/`, `TYPE` and `TYPE BODY` both write `types/`. Each file belongs to the longest extension it ends with, so a package spec is planned once and never a second time as a body whose `.sql` also matched it.

Hand-arranged subfolders still work on every plain run: move exported files into a `<object_type>/<group>/` subfolder by hand and the folder name becomes the group. The next export learns the shared prefix of those files and routes new matching objects into the same subfolder.

## Duplicate object files

Moving files by hand can leave the same filename in two places under one `<object_type>/` subtree, typically a stale copy in the type folder root plus the live one in a group subfolder. `export_db` exports into whichever copy it finds first, so the other silently rots.

The export does **not** abort on this. It runs to completion and marks the affected object on its own row, replacing the plain object name with one row per location:

```text
               TABLE | INV_BILLING_HEADER | core/tables/billing/inv_billing_header.sql [DUPE]
                     | INV_BILLING_HEADER | core/tables/inv_billing_header.sql [DUPE]
```

Paths are shown relative to the export root with the leading `database/` folder dropped, so the row names the schema, the group subfolder and the file. Delete the copies you do not want and re-run; the marker disappears once one location is left.

The scan is per schema subtree and case-insensitive, and `.fix.sql` sidecars never count as duplicates. The same object name exported from two schemas is not a collision, since each schema owns its own subtree, but a collision present in several schemas is marked in every one.

## Measuring what an environment holds

`patch -hash` builds a patch from what your repository no longer agrees with a **baseline** about, and until this flag every baseline was a belief: `patch -baseline` records your own working tree and calls it the target.

`-baseline` on `export_db` is the other half. It connects, discovers every object the project's filters resolve, renders each one exactly as an export renders it, and then **hashes** it at the path it would have been written to:

```bash
adtai export_db -env DEV -baseline
```

```text
WRITING BASELINE:
-----------------
  - patch_hashes/baseline.DEV.log

   STATUS      FILES
   ---------   -----
   UNCHANGED     398
   MODIFIED        9
   NEW             4
   REMOVED         1
   TOTAL         412
```

The file it writes is the one [patch_hash.md](patch_hash.md) already reads, so nothing about building a patch changes. What changes is what the patch means: instead of everything you altered on DEV, it becomes what this target is actually missing or holding differently.

## What the measured baseline will not do

- **It is full by construction.** `-recent`, `-name`, `-type`, `-by`, `-my` and `-delete` are refused, naming the flag, exit `2`. A narrowed run would write a partial baseline that reads on disk exactly like a complete one.
- **It writes nothing and deletes nothing**: no object files, no `.fix` sidecars, no `auto_delete` sweep. It never reports deleted objects either, because a file the target lacks is a difference for a hash patch to decide about.
- **It advances no stored state.** Neither the `-recent` watermark nor the job signatures move, because both record what an export *wrote*.

It records one `file | commit | hash` line per object, and a `measured` token on the header. That token is the difference between a reading and a belief: `snapshot` means the working tree was assumed, `deployed` means a deploy advanced it, `measured` means somebody asked the database.

The commit column is a **reverse lookup**: the newest scanned commit whose recorded content hash equals the measured one, so a line reads as "the target is at commit 312 for this file". A blank there is the interesting line, meaning the target holds content matching no commit in your scanned window, which is drift.

A run replaces every database-path entry for the schemas it exported and leaves everything else alone. Merging instead of replacing would keep a stale entry for an object the target no longer holds; replacing the whole file would wipe the APEX entries this command never sees.

APEX is that gap, and measuring it needs the same flag on `export_apex`, which does not exist yet.

**The sharp edge.** Against a measured baseline, `DELETED` means the target holds an object your repository does not, and a hash patch generates a DROP helper for it. A hotfix applied straight to the target, or an object another developer owns, is a DROP in your next hash patch. Read the `CHANGED FILES:` table before building.
