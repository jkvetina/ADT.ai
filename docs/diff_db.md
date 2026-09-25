# Compare Schema Objects (adtai diff)

`diff` with no mode flag compares the database objects and grants two schemas hold, and leaves a SQLcl DIFF artifact behind, so the same comparison can be reviewed, shared, or applied later. How the two sides connect, the shared flags and the other modes are on [diff.md](diff.md).

**The artifact changes the source, not the target.** Its scripts carry the source schema's name and bring that side into line with the target, so `-source` is the schema you intend to modify and `-target` is the state you want it to reach. Apply it connected as the source.

<br>

## Examples

List every changed object instead of the counts alone:

```bash
adtai diff -source DEV -target UAT -schema APP -verbose
```

Report only the packages, or only the objects whose name starts with `EMP`:

```bash
adtai diff -source DEV -target UAT -type PACKAGE% -verbose
adtai diff -source DEV -target UAT -name EMP% -verbose
```

Only whether anything differs, at most ten rows per listing:

```bash
adtai diff -source DEV -target UAT -verbose -limit 10
```

Put the artifact somewhere other than `config/diff/`, as a folder or as a file:

```bash
adtai diff -source DEV -target UAT -out ./releases/next
adtai diff -source DEV -target UAT -out ./releases/next/before_release.zip
```

<br>

## Output

The connection blocks and the `COMPARING SCHEMAS:` row are the ones [diff.md](diff.md#output) prints. What follows counts the differences per object type. The screen ends there: the artifact is written under `-out` and named for the two sides, and no line is spent saying where it went.

```text
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

- **`CHANGES BY OBJECT TYPE:`** counts everything that differs, one row per object type, in the same `OBJECT TYPE` / `COUNT` table [export_db.md](export_db.md#output) opens with. It carries no schema column and no destructive column: this is the whole default screen, and it answers *how much moved and of what*. Which objects, and which side is short of them, are `-verbose`.
- **The type name is singular in this table, as it is everywhere else.** A row here reads `TABLE 3`, and a row in `CHANGED OBJECTS:` or the grant tables below says `TABLE` too. `export_db`, `recompile` and `patch -install` print it the same way, and it is the spelling `-type` takes, so you can copy a type straight off any table into a filter.
- **`CHANGED OBJECTS:`** needs `-verbose`, and then names every object with its type and its **status**, with no cap unless `-limit N` sets one. A capped listing prints its first `N` rows and `LIMIT: N of M rows shown` under them; each grant table is capped the same way. The table is sorted by the columns it leads with, `OBJECT TYPE` then `OBJECT NAME`, so the column you scan runs in order; status is a column rather than a grouping, which is what keeps it that way.
- **The status is read from what the two sides exported, not from the change script.** The column used to print the DDL verb the artifact would run, `REPLACE`, `COMMENT`, `ADD, DROP*`, and, where SQLcl emitted a table and then its constraint, `CREATE, ADD`, which answers a question nobody comparing two environments is asking. `diff` compares the two export trees object by object instead, so every row can name the side it is on and no row can arrive with an empty cell.
- **No header on this screen carries a count, and no listing carries a schema column.** The table under a header is the count, row by row, and a total printed above it is a figure a reader has to reconcile rather than read. The schema cell said the same thing on every row: a run compares one schema pair, named in `COMPARING SCHEMAS:` above.
- **`CHANGED GRANTS:`** needs `-verbose` too, and holds every object grant that differs, apart from the objects. It carries two tables, split by **direction**: the first lists grants *into* your schema and leads with the `OWNER` of the object you were granted, which is why `SYS` and `APEX_*` appear there; the second lists grants *out* of it and carries the `GRANTEE` after the object name. There is no label between them, because the leading column already says which one you are reading. A direction with nothing in it prints no table at all.
- **The compared schema is never a column, because it is on every row.** Each table prints the other end of the grant and nothing else. A grant is `(object, grantee, privilege)`, and the two sides are compared one privilege at a time, so a grant that exists on both sides with different privileges contributes a `MISSING` row and an `EXTRA` row rather than needing a third status. The privilege cell names the first privilege and counts the rest (`SELECT + 3`), so a grant carrying four of them cannot stretch the row.
- **A grant pairs across a `-target-schema` run.** SQLcl spells the compared schema into every grant's file name, as the owner of a grant going out and as the grantee of one coming in, so the target's spelling is read as the source's before the two sides are compared: `GRANT SELECT ON ORDERS TO APP_RO` in `APP` and in `APP_UAT` is no difference, where it used to print as missing from one and extra in the other.
- Compared object types, as SQLcl groups them: tables, views, materialized views and their logs, indexes, comments, constraints, sequences, synonyms, triggers, procedures, functions, package specs and bodies, type specs and bodies, SQL domains, property graphs, MLE environments and modules, and object grants.

<br>

## Filtering with -type and -name

- **`-type` and `-name` narrow the artifact, not just the screen.** The patterns are written into the throwaway project's own `.dbtools/filters/ddl.filters` before either side exports, so SQLcl generates only what you asked for: the zip is smaller, the run is shorter, and the two sides are narrowed identically (a filter on one side alone would report the other side's objects as differences). Both sides always carry SQLcl's own shipped exclusions as well.
- Patterns are SQL LIKE (`%`, `_`), `*` is accepted for `%`, and a name is upper-cased before it reaches the dictionary, the way `export_db -name` reads it, so `-name orders` finds `ORDERS` rather than narrowing both exports to nothing and reporting no differences. A type matches either the name the listing prints (`PACKAGE SPEC`, `GRANT`) or its Oracle name, so `-type PACKAGE` is specifications and `-type PACKAGE%` is both halves.
- **A `-name` matches the object a row hangs off, which is what makes it narrow everything.** A comment is filed against its table, a grant against the object it grants, an index and a trigger against the table they belong to, a materialized-view log against its master, so `-name EMP%` reaches all of them through the column each dictionary view carries that object in. Earlier releases narrowed only the four queries carrying an `object_name` column, so every comment, grant and materialized-view log in the schema was exported, compared and zipped by a run that had asked for one table.
- **A filter that matches nothing prints `NO MATCHING CHANGES:`**, never `NO DIFFERENCES:`. The schemas may differ in forty ways and simply not in the way you asked about.
- The countdown is keyed by the filter as well as the two environments and schemas, because the filter narrows the export rather than only the screen, so a `-name` run and a full one are jobs of different sizes and must not seed each other's clock.
- [`-restore`](diff.md#restoring-the-targets-versions) writes what the filters left on screen: one `export_db` run against the target, narrowed to those objects, so it deletes nothing else and moves no export watermark. A differently named target schema is written under the source's name and folder.

<br>

## How the comparison runs

- **The two exports run at the same time**, in project directories of their own, and the comparison is the longer of the two rather than their sum. `project init` runs once and its output is copied per side, so both sides export through identical settings and filters; the two trees are committed to a branch each afterwards, which is what `project stage` compares.
- **APEX applications and ORDS definitions are not reported.** SQLcl puts the application's install script and the schema's whole ORDS definition in the artifact whether or not the two sides differ, and neither script opens a `CREATE`, `ALTER` or `DROP` of its own, so the rows they yield name nothing that would happen while the counts table above counts each as one more thing that moved.
- Measured on a schema compared with itself: the two export branches byte-identical, `git diff` between them empty, and `releases/ords/<schema>/ords.sql` in the zip regardless. An artifact holding only those payloads therefore reads as `NO DIFFERENCES:`, which is what it is. REST definitions are compared for real by `-rest`, table rows by `-data`, and applications and APEX static files by [`-apex`](diff_apex.md).

<br>

## The artifact

- **The artifact is named `<source_env>_<source_schema>---<target_env>_<target_schema>.zip`**, written under `-out` (default `<root>/config/diff`, beside the other folders ADT.ai generates into a project). Point `-out` at a path ending in `.zip` and that name wins instead.
- SQLcl names the zip after the project that generated it, which says nothing about either environment, so comparing one schema across two environment pairs would otherwise produce two files you cannot tell apart. Re-running the same pair replaces its own zip rather than piling up.
- **The artifact never reaches the screen.** `diff` answers what changed and stops; the zip is in `-out` under the name above. `config/diff/` is git-ignored in the project root, added on the first run if the project was scaffolded before that entry shipped. A DIFF artifact is a zip of two schemas' DDL and does not belong one `git add .` away from a commit.
- The artifact reported is always the one **this run produced**: it is generated inside a throwaway project and moved out under the name above, so zip files already sitting in `-out` are never mistaken for it.
