# Export Database Objects (adtai export_db)

`export_db` brings an Oracle schema out of the database and into your repository as one DDL file per object, in a folder tree you configure. Run it after making database changes and version control shows exactly what moved, per object.

The output is normalized, so repeated exports of an unchanged object are byte-identical and a change on screen is a real change rather than the export moving things around. Where the files land, and how to reorganize them, is on [export_db_layout.md](export_db_layout.md).

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
          DATABASE | 23.26.1.0.0 | FREEPDB1


OBJECTS OVERVIEW:
-----------------

  OBJECT TYPE   COUNT
  -----------   -----
  PROCEDURE         3
  TABLE             1
  TRIGGER           1
  VIEW              1
  GRANT


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
- **`GRANT` is in the overview and not in the header count.** The four grant artifacts (grants made, grants received, user privileges, directories) export like any other file, so the type belongs in the listing, but they are not schema objects and have no `USER_OBJECTS` row. Its count is blank because grants received write one file per owner.
- **The `GRANT` row prints only when a grant actually moved.** None of the four has a `LAST_DDL_TIME`, so every run re-reads all four and the comparison against what is on disk decides what the screen says. The files are rewritten either way.
- **The whole table waits on those reads.** The header goes up first and the reads run under it. A run where neither an object nor a privilege changed prints its header and stops: no column headings over an empty table, and no `EXPORTING 0 OBJECTS:` under it.
- A multi-schema run executes schema by schema, with its own connection block and its own `TIMER`, and prints the banner once.

<br>

## Windows: what changed, and since when

`-recent DAYS` reaches the query as `SYSDATE - DAYS`, and Oracle counts a `DATE` in days, so a fraction is a shorter window: `1/24` is the past hour and `5/1440` the past five minutes. A whole-day window keeps its `CHANGED SINCE <date>` header; a shorter one reports the instant it starts at, read off the database clock rather than yours.

Bare `-recent` exports everything changed since that schema's last successful covering export, the per-schema watermark in `config/internal/recent.yaml`, shown as `CHANGED SINCE LAST EXPORT AT <timestamp>`. A schema with no watermark yet is exported in full and seeded, with a visible `NO PREVIOUS EXPORT RECORDED:` note. Narrowed runs never advance the watermark.

Every type a window narrows is narrowed by a column that dates a **change**, which for anything in `user_objects` is `LAST_DDL_TIME`. Three types needed looking at separately:

- **An mview log** needs nothing special: its `LOG_TABLE` is an ordinary table, and a table's `LAST_DDL_TIME` is a real DDL timestamp that DML never moves.
- **An index** is dated by its own `user_objects` row. It is deliberately not dated by `user_indexes.LAST_ANALYZED`, which records when statistics were gathered rather than when the index changed.
- **A job** has no change timestamp anywhere in the dictionary, so the signal is built: the listing returns a SHA-256 of exactly the columns the exported file is rendered from, hashed inside the database. A windowed run exports the jobs whose signature moved and remembers the rest in `config/internal/job_signatures.yaml`.

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

<br>

## Watching a long export

The default screen prints a row per object, which is what you want while watching a handful. On a whole schema it is hundreds of rows, and the overview has left the scrollback long before the export ends. `-compact` keeps the overview and replaces the rows with one line that moves:

```text
EXPORTING 6 OBJECTS:
--------------------

   0%                                                                  0:00:01 
  PROCEDURES  0%                                                       0:00:00 
  PROCEDURES ........ 17%                                              0:00:00 
  PROCEDURES ................ 33%                                      0:00:00 
  PROCEDURES ......................... 50%                             0:00:00 
  TABLES .................................. 67%                        0:00:00 
  TRIGGERS .......................................... 83%              0:00:00 
  VIEWS ................................................... 100%       0:00:00 
```

- **The row names the type in the plural**, because it heads the whole batch rather than naming the object in flight. Only the label reads that way: `-type` still takes Oracle's singular spelling, and so does the overview.
- The bar advances when an object's DDL comes back, not on a clock, and the time on the right is what is left rather than what has passed. A multi-schema export draws one bar per schema.
- **The countdown is seeded by what your last export of that schema cost.** Every run records how long an object of each type took, per environment and schema, in `config/internal/recent.yaml`. The unit is per object type on purpose: a sequence costs a fiftieth of what a table with constraint blocks costs.
- A first export of a schema has no history, so the row reads `0:00:00` until the first object returns. Deleting `config/internal/recent.yaml` resets the rates and the watermarks together.
- `-silent` outranks `-compact`, since it removes the very rows the bar stands in for.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-type`, `--type` | Yes | configured object types | Object type pattern or patterns to export, with SQL-like `%` and `_` wildcards plus comma lists. Oracle type names, resolved exactly as on `recompile`: a bare `PACKAGE` exports specifications only, `PACKAGE BODY` bodies only, and `MVIEW`/`MATERIALIZED` both mean `MATERIALIZED VIEW`. See [recompile](recompile.md#object-types). |
| `-name`, `--name` | Yes | all names | Object name pattern or patterns to export, with SQL-like `%` and `_` wildcards plus comma lists, for example `APP_%,TMP_%`. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | all objects | Export objects changed in the last `DAYS` days, or a fraction of a day (`1/24` is the past hour). Bare `-recent` exports everything changed since that schema's last covering export. Narrowed runs never advance the watermark. `JOB` is filtered on a content signature instead of a timestamp. |
| `-by`, `--by` | No | all authors | Export only objects an author has changed, resolved by joining the export set against the configured `audit:` source. Requires that block in `config.yaml`. |
| `-my`, `--my` | No | off | Export only objects you have changed, taking the schema from `config/IDENTITY.yaml`. Same audit resolution as `-by`. |
| `-groups`, `--groups` | No | off | Move action: reorganize already-exported files into `<object_type>/<group>/` subfolders. Never connects or exports, and moves nothing until `-force`. See [export_db_layout.md](export_db_layout.md). |
| `-force [GROUP]`, `--force [GROUP]` | No | off | With `-groups`, apply the listed moves. `-force GROUP` lands every prefix named in one uppercased folder instead of one per prefix, so it needs named prefixes. Without `-groups` it is an error, exit `2`. |
| `-delete`, `--delete` | No | off | Delete existing object files before export, excluding `DATA`. |
| `-silent`, `--silent` | No | off | Suppress per-object names and progress callbacks, keeping the banner, connection block, overview, export header and timer. |
| `-compact`, `--compact` | No | off | Replace the per-object rows with one dotted progress bar per schema, labelled with the plural of the type being pulled. `-silent` outranks it. |

Shared options (-root, -env, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [arguments.md](arguments.md).
