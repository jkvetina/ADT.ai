# Export Database Objects (adtai export_db)

`export_db` brings an Oracle schema out of the database and into your repository as one DDL file per object, in a folder tree you configure. Run it after making database changes and version control shows exactly what moved, per object.

The output is normalized, so repeated exports of an unchanged object are byte-identical and a diff is a real change rather than the export moving things around. Where the files land, and how to reorganize them, is on [export_db_layout.md](export_db_layout.md).

<br>

## Examples

Export the whole schema from your project folder:

```bash
adtai export_db -env DEV -schema SANDBOX
```

Export one or more schemas, space-separated, comma-separated, or by pattern:

```bash
adtai export_db -schema CORE APP
adtai export_db -schema APP,CORE%
```

Narrow by object name or by object type:

```bash
adtai export_db -name APP_% TMP_%
adtai export_db -type PACKAGE% VIEW%
```

Export only what changed recently, or only what changed since your last export:

```bash
adtai export_db -recent 7
adtai export_db -recent 1/24
adtai export_db -recent
```

Clean the existing object files first, keeping `DATA`:

```bash
adtai export_db -delete
```

Replace the per-object rows with one moving bar, for a whole-schema run:

```bash
adtai export_db -compact
```

<br>

## Output

The run prints the connection block, an overview of what it found, and then a row per object:

```text
APEX DEPLOYMENT TOOL - EXPORT_DB
--------------------------------

CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.3.0.0 | FREEPDB1

OBJECTS OVERVIEW:
-----------------

  OBJECT TYPE   COUNT
  -----------   -----
  PROCEDURE         3
  TABLE             1
  TRIGGER           1
  VIEW              1
  GRANT             4

EXPORTING 6 OBJECTS:
--------------------

           PROCEDURE | ADT_FIXTURE_OWNED_PRC                                 
                     | ADT_FIXTURE_RECENT_PRC                                
                     | ADT_FIXTURE_SHARED_PRC                                
                     |
               TABLE | ADT_FIXTURE_DDL_LOG                                   
                     |
             TRIGGER | ADT_FIXTURE_DDL_TRG                                   
                     |
                VIEW | ADT_FIXTURE_DDL_LOG_V                                 
                     |

TIMER: 1s
```

- `Ctrl+C` stops the export cleanly.
- **`GRANT` is in the overview and not in the header count.** The four grant artifacts (grants made, grants received, user privileges, directories) export like any other file, so the type belongs in the listing, but they are not schema objects and have no `USER_OBJECTS` row. Its count is the number of files the type writes, one for grants made, one per owner for grants received, and one each for privileges and directories, so it reads exactly like every row above it. Every type in this table is spelled the way `-type` takes it, singular.
- **The `GRANT` row prints only when a grant actually moved.** None of the four has a `LAST_DDL_TIME`, so every run re-reads all four and the comparison against what is on disk decides what the screen says. The files are rewritten either way.
- **The whole table waits on those reads.** The header goes up first and the reads run under it. A run where neither an object nor a privilege changed prints its header and stops: no column headings over an empty table, and no `EXPORTING 0 OBJECTS:` under it.
- A multi-schema run executes schema by schema, with its own connection block and its own `TIMER`, and prints the banner once.
- Each schema ends with `UPDATING DEPENDENCIES:`, the [mirror](rebuild.md) refreshed for what it wrote.
- **Exported DDL names no schema.** The `CREATE` line and the object a `GRANT` names both drop the owner, so a file installs into whichever schema the deploying session connects as. Set `keep_owner` to write `owner.object` in both instead; [config.md](config.md#naming-the-owning-schema) covers when that is the right trade. A directory never carries an owner either way: it belongs to no schema, and Oracle refuses `CREATE DIRECTORY hr.data_dir`.
- **Each grantee gets its own `GRANT` line.** Two grantees of one object are never folded into one statement, because replaying `GRANT DELETE, SELECT ON orders TO app_user, reporting;` would hand `reporting` a `DELETE` it never held.
- **A materialized view brings its own indexes and none of Oracle's.** An index you created on a materialized view is an ordinary object: it is exported into `indexes/`, and a patch installs it after the view, because `mviews` comes before `indexes` in `patch_map`. What never reaches the repository is what Oracle built to make the view work, the container table under the view's own name, the `MLOG$_<master>` log table, and the `I_SNAP$` / `I_MLOG$` indexes behind them, since a file recreating any of those either duplicates the view definition or conflicts with it. Nothing but the name separates a snapshot index from yours in the dictionary, so the exclusion is by name and never by what the index sits on.
- **A materialized view and its log end on their semicolon, with no `/` after it.** Both are plain SQL and neither can be created twice, so the `;` runs the statement and a `/` under it would submit the same statement again, which fails the deploy on `ORA-12006` / `ORA-12000`. A view or synonym still carries the `/`, harmlessly, because `CREATE OR REPLACE` is idempotent, and a type still needs it because its body is PL/SQL.
- **Rename a file and the export keeps your spelling, inside the file as well as on it.** Files are written lowercase by default. Rename `app_users.sql` to `App_Users.sql`, `APP_USERS.sql`, or anything else, and every later run writes to that same file and spells the object's own name the way the file does, on the `CREATE` line and on the `COMMENT ON` lines under it. Nothing else moves: column names, the body of the object, and every reference to another object keep the casing the database gave them, and an unquoted Oracle identifier is case-insensitive, so this changes how the file reads and never what it deploys.

<br>

## Windows: what changed, and since when

`-recent DAYS` reaches the query as `SYSDATE - DAYS`, and Oracle counts a `DATE` in days, so a fraction is a shorter window: `1/24` is the past hour and `5/1440` the past five minutes. A whole-day window keeps its `CHANGED SINCE <date>` header; a shorter one reports the instant it starts at, read off the database clock rather than yours.

Bare `-recent` exports everything changed since that schema's last successful covering export, the per-schema watermark in `config/internal/recent.yaml`, shown as `CHANGED SINCE LAST EXPORT AT <timestamp>`. A schema with no watermark yet is exported in full and seeded. Narrowed runs never advance the watermark.

Every type a window narrows is narrowed by a column that dates a **change**, which for anything in `user_objects` is `LAST_DDL_TIME`. Three types needed looking at separately:

- **An mview log** needs nothing special: its `LOG_TABLE` is an ordinary table, and a table's `LAST_DDL_TIME` is a real DDL timestamp that DML never moves.
- **An index** is dated by its own `user_objects` row. It is deliberately not dated by `user_indexes.LAST_ANALYZED`, which records when statistics were gathered rather than when the index changed.
- **A job** has no change timestamp anywhere in the dictionary, so the signal is built: the listing returns a SHA-256 of every column the exported file can be rendered from, hashed inside the database. That covers each attribute the file sets (a non-default `JOB_PRIORITY` included) and every argument value, so changing either one moves the signature. A windowed run exports the jobs whose signature moved and remembers the rest in `config/internal/job_signatures.yaml`. A job the database refused keeps the signature of the last export that wrote it, so a windowed run keeps offering it until its file matches the job again.

The signature narrows a window, never an explicit request. `-type JOB` with no `-recent` exports every matching job with no comparison, which is how to re-pull a whole job tree on demand.

<br>

## Exporting one author's work

`-by` and `-my` resolve authorship against the project's configured `audit:` source, a DDL-log table or view, so they need no DBA-level audit-trail access:

```bash
adtai export_db -by SCOTT
adtai export_db -my
```

```yaml
audit:
  source: APP_DDL_LOG     # a table or view of DDL changes
  object_name: object_name
  changed_by: changed_by
  changed_at: changed_at  # optional; the DDL timestamp
```

Without `changed_at` a DDL log has no ordering, so the only question that can be asked is who has ever touched this object. With it configured, two things change and neither adds a flag:

- **Objects someone else changed after you are marked.** They stay in the export, since dropping them would silently lose work you really did, and carry the later author in square brackets, the same shape as `[DUPE]`.
- **`-recent` reaches the audit source too.** `-my -recent 1` means changed within a day, by me, rather than two unrelated sources silently combined.

Oracle records **no** actor for a DDL change, so authorship exists only if the project writes it down at DDL time. In a proxy session every expression a trigger would naturally use returns the proxied schema rather than the proxy user:

| expression | direct session | proxy session |
| --- | --- | --- |
| `USER` | `APP_OWNER` | `APP_OWNER` |
| `ORA_LOGIN_USER` | `APP_OWNER` | `APP_OWNER` |
| `SYS_CONTEXT('USERENV', 'SESSION_USER')` | `APP_OWNER` | `APP_OWNER` |
| `SYS_CONTEXT('USERENV', 'PROXY_USER')` | *(null)* | `SCOTT` |
| `SYS_CONTEXT('USERENV', 'AUTHENTICATION_METHOD')` | `PASSWORD` | `PASSWORD_PROXY` |

A trigger recording `USER` therefore files every developer's work under the shared schema name, and the identity cannot be recovered afterwards. A log covering both the proxy and the direct case records:

```sql
COALESCE(
    SYS_CONTEXT('USERENV', 'CLIENT_IDENTIFIER'),
    SYS_CONTEXT('USERENV', 'PROXY_USER'),
    USER
)
```

`CLIENT_IDENTIFIER` is there because every ADT.ai connection runs `DBMS_SESSION.SET_IDENTIFIER(db_schema)` from `config/IDENTITY.yaml`, which is also where `-my` reads your identity from (see [config.md](config.md#developer-identity)).

<br>

## Permanently excluding objects

`-name` and `-type` narrow a single run. To keep a set of objects out of **every** export, put the pattern in the schema's `export:` block in the connection file:

```yaml
DEV:
  schemas:
    APP:
      export:
        ignore: 'REST_INCOMING_RETRY%,TMP_%'   # SQL LIKE, comma-separated
        prefix: ''                             # inverse: export only matching names
```

Set it with the `connection` command rather than by hand:

```bash
adtai connection -create -env DEV -schema APP -ignore 'REST_INCOMING_RETRY%' -go
```

The patterns are matched by the discovery query, so ignored objects are never listed and never exported. Because a config filter is not a runtime filter they also count as *missing* on the next full run, so `auto_delete` removes the files a previous export already wrote.

That is what makes this the right tool for runtime-generated objects: an application creating one scheduler job per request otherwise adds one file to the repository forever. `export_data` reads the same block.

One kind of generated object needs no pattern: the `DEPSCAN$<n>#<n>` procedures APEX's dependency scan leaves on a schema. They are scratch, never part of the application, so the discovery query skips them and no file is written for one. `recompile` removes any it finds.

<br>

## What the privileges file records

`<SCHEMA>_schema.sql` is what the exported user was granted, in three blocks separated by `--`: roles, system privileges, and, on 23ai and above, schema privileges.

```text
GRANT CONNECT               TO sandbox;
--
GRANT CREATE TABLE                      TO sandbox;
--
GRANT EXECUTE ANY PROCEDURE             ON SCHEMA system TO sandbox WITH ADMIN OPTION;
GRANT SELECT ANY TABLE                  ON SCHEMA core_locks TO sandbox;
```

A schema privilege covers every object of one schema, present and future, so it is the only line here naming a second schema. `WITH ADMIN OPTION` is carried on all three kinds, because a user who can pass a grant on is not the same user as one who cannot.

The schema block needs `user_schema_privs`, which is 23ai. On an older database the read finds no such view and the file simply ends after the system privileges, exactly as it always did.

<br>

## What an assertion exports as

A 23ai assertion is a CHECK constraint spanning more than one table, so the rule it enforces belongs to none of them and none of them carries it. It exports on its own, to `assertions/`:

```text
BEGIN
    DBMS_UTILITY.EXEC_DDL_STATEMENT('DROP ASSERTION F26_QTY_ASSERT');
    ...
END;
/
--
CREATE ASSERTION f26_qty_assert CHECK
(
  NOT EXISTS (
    SELECT 1
    FROM   f26_assert_t
    WHERE  qty > 1000
  )
)
ENABLE
NOT DEFERRABLE
INITIALLY IMMEDIATE
VALIDATE;
```

Oracle has no `CREATE OR REPLACE ASSERTION`, so the file drops before it creates, the same shape a materialized view log takes and for the same reason. The condition between the parentheses is kept exactly as it was written, because it is your SQL rather than something the dictionary formatted.

Two things about this type are unlike every other one, and both are invisible until they bite:

- Its `user_objects` row says `UNDEFINED`, not `ASSERTION`, so it is found by name against `user_assertions`. That view is 23ai; on an older database the read finds nothing and a plain `export_db` carries on exactly as before.
- Its DDL does not come from `DBMS_METADATA`, which refuses the type outright with `ORA-31600`. It comes from `user_assertions.DEFINITION_SQL`.

In a patch it installs in its own `assertions` section, after the tables and views its condition can name and before the data, so a seed row that breaks the rule fails at that row.

<br>

## What the 26ai object types export as

A SQL domain, a property graph, an MLE module and an MLE environment each carry their own `user_objects` row and each has a folder of its own, so a plain `export_db` sweeps them like any other type. None of the four comes from `DBMS_METADATA`, which refuses all four with `ORA-31600`.

What each exports as, and the two repository references that stop dangling once they do, are on export_db_26ai.md.

<br>

## Watching a long export

The default screen prints a row per object, which is what you want while watching a handful. On a whole schema it is hundreds of rows, and the overview has left the scrollback long before the export ends. `-compact` keeps the overview and replaces the rows with one line that moves:

```text
EXPORTING 3 OBJECTS:
--------------------

   0%                                                                  0:00:01 
  PROCEDURE  0%                                                        0:00:00 
  PROCEDURE ................. 33%                                      0:00:00 
  TABLE .................. 33%                                         0:00:00 
  TABLE ..................................... 67%                      0:00:00 
  TRIGGER .................................... 67%                     0:00:00 
  TRIGGER ...................................................... 100%  0:00:00 
  ALL DONE ..................................................... 100%  0:00:00 
```

- **The row names the type being pulled**, in the same singular spelling the overview above it prints and `-type` takes. Once the last object is written the row reads `ALL DONE`, with what the whole export cost.
- The bar advances when an object's DDL comes back, not on a clock, and the time on the right is what is left rather than what has passed. A multi-schema export draws one bar per schema.
- **The dot track is sized against the type on the row**, so a full row always reaches the same column whichever type names it. A shorter label buys itself a longer track, which is why the same percentage draws a different number of dots after the label changes.
- **The percentage travels with the dots**, one space off the last of them, and the whole remainder of the track pads out behind it so the timer lands on the 78-column edge. The timer is the only field on a fixed column: a row relabelling itself shorter hands the leader the columns the label gave up and gets back only its share of them, so the figure sits a little further left under a shorter type name.
- **The countdown is seeded by what your last export of that schema cost.** Every run records how long an object of each type took, per environment and schema, in `config/internal/recent.yaml`. The unit is per object type on purpose: a sequence costs a fiftieth of what a table with constraint blocks costs.
- A first export of a schema has no history, so the row reads `0:00:00` until the first object returns. Deleting `config/internal/recent.yaml` resets the rates and the watermarks together.
- `-silent` outranks `-compact`, since it removes the very rows the bar stands in for.

<br>

## When the database refuses an object

One object Oracle will not describe does not end the export. It is recorded, every other object is still written, the bar keeps its countdown, and the refused objects are listed under a warning straight after the export:

```text
WARNING - OBJECT EXPORT FAILED:
-------------------------------

                 JOB | ADT917_LIGHT_JOB                                      
                     |


UPDATING DEPENDENCIES:
----------------------
```

`ADT917_LIGHT_JOB` is a lightweight scheduler job: `USER_SCHEDULER_JOBS` lists it, and `DBMS_METADATA` has no DDL for it.

- **The rows are the exported listing's rows**, grouped and sorted by type, so a run that loses a view and a job names both.
- `-debug` adds each object's error under its row, indented past the `|`. Without it the warning names the objects and nothing else.
- A failure after the DDL came back, such as a normalizer tripping, is listed the same way, and that object is still the only one lost.
- **An object dropped while the export runs is listed the same way**, since there is no DDL left to read between the listing and its turn. Nothing is written for it.
- The run exits `1`, and a multi-schema run still exports the schemas after it.
- **A lost connection still stops the run where it stands**, since every remaining object would fail the same way.
- **A refused object does not cost you the run.** The `-recent` watermark and a `-baseline` still record everything that was written, so the next `-recent` run starts from here. Only a schema where every object was refused is left unstamped.
- **A refused job is offered again.** Its stored signature stays at what the last export that wrote it recorded, so every `-recent` run retries it until its file matches the job.

<br>

## Asking for a type ADT.ai does not export

`object_types` in `config.yaml` is the list of types `export_db` writes files for, and `-type` selects from it. A pattern that names something outside that list is refused rather than exported, exit `2`:

```text
export_db: -type selected object types export_db does not export: LOB. Its exported types are the 'object_types' keys in config.yaml.
```

The refusal comes before the connection when the config alone settles it, so a type outside the map costs no round trip.

A wildcard is different. `-type %` covers every configured type, so it is a request `export_db` can only judge once the schema has answered, and there the refusal lands under the overview table, naming every type it found no home for.

A typo takes the same path, on purpose. `-type NOSUCHTYPE` used to export nothing and exit `0`, which reads as *this schema has none of those* rather than *that is not a type I export*.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-type`, `--type` | Yes | configured object types | Object type pattern or patterns to export, with SQL-like `%` and `_` wildcards plus comma lists (`\` escapes a literal one, quoted: `-type 'PACKAGE\_%'`). Oracle type names, resolved exactly as on `recompile`: a bare `PACKAGE` exports specifications only, `PACKAGE BODY` bodies only, and `MVIEW`/`MATERIALIZED` both mean `MATERIALIZED VIEW`. See [recompile](recompile.md#object-types). A pattern matching none of the configured `object_types` is an error, exit `2`. |
| `-name`, `--name` | Yes | all names | Object name pattern or patterns to export, with SQL-like `%` and `_` wildcards plus comma lists, for example `APP_%,TMP_%`. `\` escapes a literal `_` or `%`, quoted: `-name 'APP\_SETTINGS'`. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | all objects | Export objects changed in the last `DAYS` days, or a fraction of a day (`1/24` is the past hour). Bare `-recent` exports everything changed since that schema's last covering export. Narrowed runs never advance the watermark. `JOB` is filtered on a content signature instead of a timestamp. |
| `-by`, `--by` | No | all authors | Export only objects an author has changed, resolved by joining the export set against the configured `audit:` source. Requires that block in `config.yaml`. |
| `-my`, `--my` | No | off | Export only objects you have changed, taking the schema from `config/IDENTITY.yaml`. Same audit resolution as `-by`. |
| `-groups`, `--groups` | No | off | Move action: reorganize already-exported files into `<object_type>/<group>/` subfolders. Never connects or exports, and moves nothing until `-force`. See [export_db_layout.md](export_db_layout.md). |
| `-force [GROUP]`, `--force [GROUP]` | No | off | With `-groups`, apply the listed moves. `-force GROUP` lands every prefix named in one uppercased folder instead of one per prefix, so it needs named prefixes. Without `-groups` it is an error, exit `2`. |
| `-delete`, `--delete` | No | off | Delete existing object files before export, excluding `DATA`. |
| `-baseline [FILE]`, `--baseline [FILE]` | No | off | Record what this environment holds as a patch baseline: every object is rendered as an export renders it, then hashed at the path it would have been written to, and each table is stored beside the baseline as that rendered file. No object file is written or deleted. Refused beside a narrowing flag. See [export_db_layout.md](export_db_layout.md). |
| `-silent`, `--silent` | No | off | Suppress per-object names and progress callbacks, keeping the banner, connection block, overview, export header and timer. |
| `-compact`, `--compact` | No | off | Replace the per-object rows with one dotted progress bar per schema, labelled with the type being pulled. `-silent` outranks it. |

Shared options (-root, -env, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
