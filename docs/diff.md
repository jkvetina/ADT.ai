# Compare Two Schemas (adtai diff)

![Make UAT look like DEV, not like yesterday's zip.](images/diff.png)

`diff` compares two live schemas, usually the same one across two environments, and reports what differs: which objects, REST services or table rows changed, and whether the two match at all.

Run it before a release to see what the deployment would really change, or after one to confirm the environments converged. It connects to both sides itself and reads nothing from exported files.

What it compares is picked by one flag, and each mode has a page of its own:

| Mode | Compares |
| --- | --- |
| objects | The database objects and grants, and leaves a SQLcl DIFF artifact behind. The default, with no mode flag. |
| `-rest` | The REST modules, privileges and roles the two schemas publish. |
| `-data` | The rows of the tables `export_data` exports, matched on their keys. |

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

Compare the REST services, or the table rows, instead of the objects:

```bash
adtai diff -source DEV -target UAT -rest -verbose
adtai diff -source DEV -target UAT -data -name APP_% -ignore UPDATED_% -limit 20 -verbose
```

<br>

## Output

Every mode opens the same way: a connection block per side, then the comparison crawling under its own row. What differs follows, in the tables of the mode that ran, shown on diff_db.md, diff_rest.md and diff_data.md.

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
```

- **`COMPARING SCHEMAS:`** opens before the comparison starts and its row counts down while the work runs, so a long run is never a blank screen. The row names both sides in full, `<source env>.<source schema> -> <target env>.<target schema>`, and that is the **only** place the direction is written: every status below is relative to the target, so spelling the target out per row would repeat one word down the page.
- The countdown runs against what this schema pair, in this mode, cost last time, kept in `config/internal/diff_timers.yaml` and folded through a rolling average; a combination you have never run falls back to a long estimate so the bar still crawls. The row holds at 99% until the run really ends, and the time on the closed row is the true elapsed. `-debug` prints the header and skips the row, leaving the raw transport unobscured.
- **Every mode reports the same three statuses.** `MISSING` means the source has it and the target does not; `EXTRA` means the target has it and the source does not; `CHANGED` means both have it and the two differ.
- **`LEGEND:`** closes the screen whenever something was listed, and spells out only the statuses that actually appeared. It sits last because you meet the tables first and want the definition when a cell puzzles you; a run that found no differences prints no legend.
- Two sides that already match print `NO DIFFERENCES:`, so an identical pair never looks like an unexamined one.
- **No table on this screen runs past 80 characters, and the cap is absolute.** Oracle object names reach 128, so the width is budgeted rather than hoped for: the name columns give up characters first, then the object type, and a trimmed cell ends in `...` so a shortened name can never read as a real one. A row wide enough to exhaust that budget keeps giving until the line fits. `STATUS` and `PRIVILEGE` never give way, a trimmed status would be a guess.
- A failed run prints `DIFF FAILED:` with SQLcl's own output underneath and exits non-zero.

<br>

## Connecting

Both sides connect through **named SQLcl connections** (`ADT_…`, registered automatically, password held in SQLcl's own secure store) rather than credentials written into the generated script. See [connection.md](connection.md#named-sqlcl-connections). Set `sqlcl_named_connections: false` in `config.yaml` to fall back to inline connect lines.

Each side also runs the same session setup as every other ADT.ai connection: the `DBMS_SESSION.SET_IDENTIFIER` block composed from `config/IDENTITY.yaml`, then `config/STARTUP.sql`, replayed after each connect and before the comparison. That holds on the named path too, where the script carries no credentials at all. See [config.md](config.md#session-startup-script).

<br>

### Two PDBs of one container database

**A schema compares against its own copy in a sibling PDB.** This is the ordinary multitenant shape (one CDB, one PDB per environment, the same schema name in each), and SQLcl's own `DIFF` command refuses it.

`DIFF` decides the two sides are the same connection when their current schema matches and their database identity matches, and it reads that identity from `SYS_CONTEXT('USERENV','DB_UNIQUE_NAME')`, which is scoped to the **container**, not the PDB.

Two PDBs of one CDB therefore answer the same value, `CON_NAME` is never consulted, and the run ends on `Source and target connections are the same. Nothing to diff.` with nothing compared.

`diff` does not send that command. It drives the four `project` commands `DIFF` is a wrapper over (`project init`, `project export` per side, then `project stage` and `project gen-artifact`), each against the session it belongs to.

None of them consults a connection identity, so the comparison runs and the artifact is the same Liquibase release `DIFF` would have produced. Nothing is needed from you: a same-CDB comparison simply works.

<br>

### What connecting costs

The object comparison is four SQLcl sessions rather than two, one per `project` command, and each carries the same connect block and the same session setup.

**Connecting is not what a comparison costs, which is why the two connection blocks stay.** Timed apart from the `project` phases across three runs on a live SANDBOX pair: the whole command took 14.2s, 15.3s and 16.3s, and the two blocks the screen prints before the work accounted for 0.1s to 0.2s of each.

The four phases are the rest, the slowest run splitting as init 2.9s, the two exports 7.6s and 7.8s in parallel, stage 4.6s. Dropping the blocks would buy a tenth of a second and cost the screen the two version tables naming the databases about to be compared.

What a run can actually save is export time, which is what `-name` and `-type` narrow.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-source`, `--source` | No | connection default environment | Source connection environment. |
| `-target`, `--target` | No | required | Target connection environment. |
| `-target-schema`, `--target-schema` | No | `-schema` | Target schema, for the case where the two sides are spelled differently. |
| `-out`, `--out` | No | `<root>/config/diff` | Output folder for the SQLcl DIFF artifact, or a `.zip` path naming the artifact itself. With `-data`, the file every differing row is written to untrimmed; see diff_data.md. |
| `-type`, `--type` | Yes | all | Object type pattern(s) to compare; narrows the export and the screen. Comma- or space-separated, `%` wildcards. |
| `-name`, `--name` | Yes | all | Object name pattern(s) to compare; narrows the export and the screen. Comma- or space-separated, `%` wildcards. |
| `-verbose`, `--verbose` | No | off | List every changed object, uncapped, under `CHANGED OBJECTS:` and `GRANTS:`. With `-data`, one block per table of its differing rows and values. |
| `-rest`, `--rest` | No | off | Compare the REST modules, privileges and roles both schemas publish instead of the schema objects. See diff_rest.md. |
| `-data`, `--data` | No | off | Compare the rows of the tables `export_data` exports instead of the schema objects. See diff_data.md. |
| `-ignore`, `--ignore` | Yes | none | With `-data`, column pattern(s) to leave out on both sides. Comma- or space-separated, `%` wildcards. |
| `-limit`, `--limit` | No | all | With `-data`, stop each table after N differing rows. |

`-schema` is the shared flag and it names the SOURCE schema here. It also supplies the target schema, because the usual comparison is one schema across two environments; `-target-schema` above is how you say the two sides differ.

Shared options (-root, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
