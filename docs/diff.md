# Compare Two Schemas (adtai diff)

![Make UAT look like DEV, not like yesterday's zip.](images/diff.png)

`diff` compares two live schemas, usually the same one across two environments, and reports what differs: which objects changed, which would be removed, and whether the two match at all. It leaves a SQLcl DIFF artifact behind as well, so the same comparison can be reviewed, shared, or applied later.

**The artifact changes the source, not the target.** Its scripts carry the source schema's name and bring that side into line with the target, so `-source` is the schema you intend to modify and `-target` is the state you want it to reach. Apply it connected as the source.

Run it before a release to see what the deployment would really change, or after one to confirm the environments converged. It connects to both sides itself and reads nothing from exported files.

<br>

## Examples

Compare the default schemas of two environments, from your project folder:

```bash
adtai diff -source DEV -target UAT
```

Bring UAT into line with DEV, so UAT is the side that changes and therefore the source:

```bash
adtai diff -source UAT -target DEV
```

Name the schema when the environment defaults are not what you mean. The target side uses the same schema unless you say otherwise, because one schema across two environments is the usual comparison:

```bash
adtai diff -source DEV -target UAT -schema APP
```

Name both when the two sides are spelled differently:

```bash
adtai diff -source DEV -schema APP -target UAT -target-schema APP_UAT
```

Leave `-source` out and it is the environment your connections file declares first:

```bash
adtai diff -target UAT
```

List every changed object instead of the counts alone:

```bash
adtai diff -source DEV -target UAT -schema APP -verbose
```

Report only the packages, or only the objects whose name starts with `EMP`:

```bash
adtai diff -source DEV -target UAT -type PACKAGE% -verbose
adtai diff -source DEV -target UAT -name EMP% -verbose
```

Put the artifact somewhere other than `config/diff/`, as a folder or as a file:

```bash
adtai diff -source DEV -target UAT -out ./releases/next
adtai diff -source DEV -target UAT -out ./releases/next/before_release.zip
```

<br>

## Output

A connection block per side, then the comparison crawling under its own row, then what differs. The screen ends there: the artifact is written under `-out` and named for the two sides, and no line is spent saying where it went.

```text
APEX DEPLOYMENT TOOL - DIFF
---------------------------

CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.3.0.0 | FREEPDB1

CONNECTING TO SCHEMA DEMO, UAT:
-------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.3.0.0 | FREEPDB1

COMPARING SCHEMAS:
------------------

  DEV.SANDBOX -> UAT.DEMO ............................... 100%  0:00:37

CHANGES BY OBJECT TYPE:
-----------------------

  OBJECT TYPE    COUNT
  ------------   -----
  FUNCTION           8
  PACKAGE BODY       1
  TABLE              1

TIMER: 24s
```

`-verbose` adds every changed object under the table, in the same run:

```text
CHANGED OBJECTS:
----------------

  OBJECT TYPE    OBJECT NAME     STATUS
  ------------   -------------   -------
  FUNCTION       CALC_BONUS      MISSING
  PACKAGE BODY   EMPLOYEES_DML   CHANGED
  TABLE          EMPLOYEES       CHANGED
  VIEW           ORDERS_V        EXTRA
```

Object grants follow, in two tables: what was granted **to** your schema, then what your schema granted **out**.

```text
CHANGED GRANTS:
---------------

  OWNER     OBJECT TYPE    OBJECT NAME   PRIVILEGE    STATUS
  -------   ------------   -----------   ----------   -------
  APEX_X    PACKAGE SPEC   APEX_UTIL     EXECUTE      EXTRA
  SYS       PACKAGE SPEC   DBMS_CRYPTO   EXECUTE      MISSING

  OBJECT TYPE   OBJECT NAME   GRANTEE   PRIVILEGE    STATUS
  -----------   -----------   -------   ----------   -------
  TABLE         ORDERS        APP_RO    SELECT + 3   MISSING
  VIEW          ORDERS_V      APP_RO    SELECT       EXTRA


LEGEND:
-------
  CHANGED   on both sides, and the two do not match
  MISSING   the source has it, the target does not
  EXTRA     the target has it, the source does not
```

- **`COMPARING SCHEMAS:`** opens before the comparison starts and its row counts down while SQLcl works, so a long run is never a blank screen. The row names both sides in full, `<source env>.<source schema> -> <target env>.<target schema>`, and that is the **only** place the direction is written: every status below is relative to the target, so spelling the target out per row would repeat one word down the page. The countdown runs against what this schema pair **and this filter** cost last time, kept in `config/internal/diff_timers.yaml` and folded through a rolling average; a combination you have never run falls back to a long estimate so the bar still crawls. The filter is part of the key because it narrows the export rather than only the screen, so a `-name` run and a full one are jobs of different sizes and must not seed each other's clock. The row holds at 99% until the run really ends, and the time on the closed row is the true elapsed. `-debug` prints the header and skips the row, leaving the raw transport unobscured.
- **`CHANGES BY OBJECT TYPE:`** counts everything that differs, one row per object type, in the same `OBJECT TYPE` / `COUNT` table [export_db.md](export_db.md#output) opens with. It carries no schema column and no destructive column: this is the whole default screen, and it answers *how much moved and of what*. Which objects, and which side is short of them, are `-verbose`.
- **The type name is singular in this table, as it is everywhere else.** A row here reads `TABLE 3`, and a row in `CHANGED OBJECTS:` or the grant tables below says `TABLE` too. `export_db`, `recompile` and `patch -install` print it the same way, and it is the spelling `-type` takes, so you can copy a type straight off any table into a filter.
- **`CHANGED OBJECTS:`** needs `-verbose`, and then names every object with no cap at all, with its type and its **status**. `MISSING` means the source has it and the target does not; `EXTRA` means the target has it and the source does not; `CHANGED` means both have it and the two definitions differ. The table is sorted by the columns it leads with, `OBJECT TYPE` then `OBJECT NAME`, so the column you scan runs in order; status is a column rather than a grouping, which is what keeps it that way.
- **The status is read from what the two sides exported, not from the change script.** The column used to print the DDL verb the artifact would run, `REPLACE`, `COMMENT`, `ADD, DROP*`, and, where SQLcl emitted a table and then its constraint, `CREATE, ADD`, which answers a question nobody comparing two environments is asking. `diff` compares the two export trees object by object instead, so every row can name the side it is on and no row can arrive with an empty cell.
- **No header on this screen carries a count, and no listing carries a schema column.** The table under a header is the count, row by row, and a total printed above it is a figure a reader has to reconcile rather than read. The schema cell said the same thing on every row: a run compares one schema pair, named in `COMPARING SCHEMAS:` above.
- **`CHANGED GRANTS:`** needs `-verbose` too, and holds every object grant that differs, apart from the objects. It carries two tables, split by **direction**: the first lists grants *into* your schema and leads with the `OWNER` of the object you were granted, which is why `SYS` and `APEX_*` appear there; the second lists grants *out* of it and carries the `GRANTEE` after the object name. There is no label between them, because the leading column already says which one you are reading. A direction with nothing in it prints no table at all.
- **The compared schema is never a column, because it is on every row.** Each table prints the other end of the grant and nothing else. A grant is `(object, grantee, privilege)`, and the two sides are compared one privilege at a time, so a grant that exists on both sides with different privileges contributes a `MISSING` row and an `EXTRA` row rather than needing a third status. The privilege cell names the first privilege and counts the rest (`SELECT + 3`), so a grant carrying four of them cannot stretch the row.
- **`LEGEND:`** closes the screen whenever something was listed, and spells out only the statuses that actually appeared. It sits last because you meet the tables first and want the definition when a cell puzzles you; a run that found no differences prints no legend.
- **No table on this screen runs past 80 characters, and the cap is absolute.** Oracle object names reach 128, so the width is budgeted rather than hoped for: the name columns give up characters first, then the object type, and a trimmed cell ends in `...` so a shortened name can never read as a real one. A row wide enough to exhaust that budget keeps giving until the line fits, because a guarantee that breaks on a wide row is not a guarantee. `STATUS` and `PRIVILEGE` never give way, a trimmed status would be a guess.
- **`-type` and `-name` narrow the artifact, not just the screen.** The patterns are written into the throwaway project's own `.dbtools/filters/ddl.filters` before either side exports, so SQLcl generates only what you asked for: the zip is smaller, the run is shorter, and the two sides are narrowed identically (a filter on one side alone would report the other side's objects as differences). Both sides always carry SQLcl's own shipped exclusions as well. Patterns are SQL LIKE (`%`, `_`), `*` is accepted for `%`, and a type matches either the name the listing prints (`PACKAGE SPEC`, `GRANT`) or its Oracle name, so `-type PACKAGE` is specifications and `-type PACKAGE%` is both halves.
- **A `-name` matches the object a row hangs off, which is what makes it narrow everything.** A comment is filed against its table, a grant against the object it grants, an index and a trigger against the table they belong to, a materialized-view log against its master, so `-name EMP%` reaches all of them through the column each dictionary view carries that object in. Earlier releases narrowed only the four queries carrying an `object_name` column, so every comment, grant and materialized-view log in the schema was exported, compared and zipped by a run that had asked for one table.
- **A filter that matches nothing prints `NO MATCHING CHANGES:`**, never `NO DIFFERENCES:`. The schemas may differ in forty ways and simply not in the way you asked about.
- Two schemas that already match print `NO DIFFERENCES:` instead, so an identical pair no longer looks like an unexamined one.
- Compared object types, as SQLcl groups them: tables, views, materialized views and their logs, indexes, comments, constraints, sequences, synonyms, triggers, procedures, functions, package specs and bodies, type specs and bodies, SQL domains, property graphs, MLE environments and modules, and object grants.
- **The two exports run at the same time**, in project directories of their own, and the comparison is the longer of the two rather than their sum. `project init` runs once and its output is copied per side, so both sides export through identical settings and filters; the two trees are committed to a branch each afterwards, which is what `project stage` compares.
- **APEX applications and ORDS definitions are not reported.** SQLcl puts the application's install script and the schema's whole ORDS definition in the artifact whether or not the two sides differ, and neither script opens a `CREATE`, `ALTER` or `DROP` of its own, so the rows they yield name nothing that would happen while the counts table above counts each as one more thing that moved. Measured on a schema compared with itself: the two export branches byte-identical, `git diff` between them empty, and `releases/ords/<schema>/ords.sql` in the zip regardless. An artifact holding only those payloads therefore reads as `NO DIFFERENCES:`, which is what it is. Comparing applications, APEX files and REST services for real is separate work.
- **The artifact is named `<source_env>_<source_schema>---<target_env>_<target_schema>.zip`**, written under `-out` (default `<root>/config/diff`, beside the other folders ADT.ai generates into a project). Point `-out` at a path ending in `.zip` and that name wins instead. SQLcl names the zip after the project that generated it, which says nothing about either environment, so comparing one schema across two environment pairs would otherwise produce two files you cannot tell apart. Re-running the same pair replaces its own zip rather than piling up.
- **The artifact never reaches the screen.** `diff` answers what changed and stops; the zip is in `-out` under the name above. `config/diff/` is git-ignored in the project root, added on the first run if the project was scaffolded before that entry shipped. A DIFF artifact is a zip of two schemas' DDL and does not belong one `git add .` away from a commit.
- The artifact reported is always the one **this run produced**: it is generated inside a throwaway project and moved out under the name above, so zip files already sitting in `-out` are never mistaken for it.
- A failed run prints `DIFF FAILED:` with SQLcl's own output underneath and exits non-zero.

<br>

## Two PDBs of one container database

**A schema compares against its own copy in a sibling PDB.** This is the ordinary multitenant shape (one CDB, one PDB per environment, the same schema name in each), and SQLcl's own `DIFF` command refuses it: it decides the two sides are the same connection when their current schema matches and their database identity matches, and it reads that identity from `SYS_CONTEXT('USERENV','DB_UNIQUE_NAME')`, which is scoped to the **container**, not the PDB. Two PDBs of one CDB therefore answer the same value, `CON_NAME` is never consulted, and the run ends on `Source and target connections are the same. Nothing to diff.` with nothing compared.

`diff` does not send that command. It drives the four `project` commands `DIFF` is a wrapper over (`project init`, `project export` per side, then `project stage` and `project gen-artifact`), each against the session it belongs to.

None of them consults a connection identity, so the comparison runs and the artifact is the same Liquibase release `DIFF` would have produced. Nothing about the command surface changes and nothing is needed from you: a same-CDB comparison simply works.

One visible consequence: the two exports are serial, because a git branch has to be committed between them. A same-CDB pair that could not be compared at all is worth more than a faster one that cannot.

<br>

## How the two sides connect

Both sides connect through **named SQLcl connections** (`ADT_…`, registered automatically, password held in SQLcl's own secure store) rather than credentials written into the generated script. See [connection.md](connection.md#named-sqlcl-connections). Set `sqlcl_named_connections: false` in `config.yaml` to fall back to inline connect lines.

Each side also runs the same session setup as every other ADT.ai connection: the `DBMS_SESSION.SET_IDENTIFIER` block composed from `config/IDENTITY.yaml`, then `config/STARTUP.sql`, replayed after each connect and before the comparison. That holds on the named path too, where the script carries no credentials at all. See [config.md](config.md#session-startup-script).

The comparison is four SQLcl sessions rather than two, one per `project` command, and each carries the same connect block and the same session setup.

**Connecting is not what a comparison costs, which is why the two connection blocks stay.** Timed apart from the `project` phases across three runs on a live SANDBOX pair: the whole command took 14.2s, 15.3s and 16.3s, and the two blocks the screen prints before the work accounted for 0.1s to 0.2s of each. The four phases are the rest, the slowest run splitting as init 2.9s, the two exports 7.6s and 7.8s in parallel, stage 4.6s. Dropping the blocks would buy a tenth of a second and cost the screen the two version tables naming the databases about to be compared. What a run can actually save is export time, which is what `-name` and `-type` narrow.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-source`, `--source` | No | connection default environment | Source connection environment. |
| `-target`, `--target` | No | required | Target connection environment. |
| `-target-schema`, `--target-schema` | No | `-schema` | Target schema, for the case where the two sides are spelled differently. |
| `-out`, `--out` | No | `<root>/config/diff` | Output folder for the SQLcl DIFF artifact, or a `.zip` path naming the artifact itself. |
| `-type`, `--type` | Yes | all | Object type pattern(s) to compare; narrows the export and the screen. Comma- or space-separated, `%` wildcards. |
| `-name`, `--name` | Yes | all | Object name pattern(s) to compare; narrows the export and the screen. Comma- or space-separated, `%` wildcards. |
| `-verbose`, `--verbose` | No | off | List every changed object, uncapped, under `CHANGED OBJECTS:` and `GRANTS:`. |

`-schema` is the shared flag and it names the SOURCE schema here. It also supplies the target schema, because the usual comparison is one schema across two environments; `-target-schema` above is how you say the two sides differ.

Shared options (-root, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
