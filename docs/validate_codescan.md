# Code Scan (adtai validate -codescan)

`adtai validate -codescan` runs SQLcl's own `codescan` over the exported database and APEXlang code and fails only on findings that are new since each tree's last clean run. Like a plain `validate` it never connects, so it runs in CI and from any checkout. The command itself is on [validate.md](validate.md).

<br>

## Examples

```bash
adtai validate -codescan
adtai validate -codescan -app 100
adtai validate -codescan -input ./sandbox/database
```

<br>

## Output

`-codescan` scans the trees `codescan_paths` names: `database`, the export_db tree `path_objects` resolves to, one per schema where it carries `<schema>`, and `apex`, every exported `apexlang/` folder. `-app` and `-input` replace that list for one run. One SQLcl session and one row per tree, and it needs SQLcl 26.3 or newer:

```text
APEX DEPLOYMENT TOOL - VALIDATE
-------------------------------

CODESCAN:
---------
  database ........................................................... 0:00:04
  100/ORDERS ......................................................... 0:00:05

BASELINE: 251 known, 0 new, 0 fixed


TIMER: 9s
```

A finding the baseline does not hold prints as a stanza, the shape of a compile message, and fails the run. Its last line names who changed the file last, the author, the commit and its subject, read off the commit store `adtai rebuild` keeps; a project with no store for the branch gets no such line:

```text
NEW VIOLATIONS IN sandbox/database:
-----------------------------------

  package_bodies/demo_pkg.sql:9:3
    G-7120
    Always add the name of the program unit to its end keyword
    story@example.com, fc02fa9b demo_pkg ends its blocks without their names
```

The author reads through `repo_authors`, and a subject too long for the line is cut at 80 columns.

<br>

## The baseline

**Each tree is held to its own baseline**, its last clean run, kept in `config/internal/codescan.db` with twenty runs per tree. A finding is its file, rule, message and target, never its line, so a finding that moved is still the same one. Positions print 1-based; a finding about a whole component prints its file alone.

**A run records only when nothing in it is new**, so a failing run never becomes the baseline and a fixed finding drops out of it: the bar only tightens. A tree's first run has nothing to compare with, so everything it holds is known, and it records and passes.

`codescan_fail_on` decides what fails:

| Value | Listed | Exit |
| ----- | ------ | ---- |
| `new` | `NEW VIOLATIONS IN <tree>:`, the findings the baseline does not hold | non-zero on any |
| `any` | `ERRORS IN <tree>:`, every finding | non-zero on any |
| `none` | `NEW VIOLATIONS IN <tree>:` | `0` |

**A scan that read nothing is not a pass, whatever the setting.** SQLcl exits `0` either way and writes an empty report for a clean tree, so its closing line is the gate: a tree of no files is a `NOTES:` row and output it cannot read is `WARNING - UNRECOGNISED OUTPUT <tree>:`, both non-zero.

`patch -create` runs the same scan over only the files a patch carries when `patch_codescan` says so, held to these baselines ([patch_install.md](patch_install.md#the-code-scan)).

<br>

## Suppressing a finding

A finding the project has decided to keep goes in `codescan_ignore` in `config/config.yaml`, never in the code. Every entry names the rule `code` and the `reason`, both required. `file` narrows it to one file, a path from the project root, and `component` to one APEX component inside that file, which therefore needs the `file`:

```yaml
codescan_ignore:
  - {code: G-7120, reason: end labels are not our style}
  - code: G-7510
    file: sandbox/apex/100_ORDERS/apexlang/shared-components/app-processes.apx
    component: {type: appProcess, id: order-badge-label}
    reason: htp.p is what this process is for
```

The component's `type` and `id` are the words its `.apx` opens with, `appProcess order-badge-label (`. A component scope is per rule: the same process still reports any other rule it breaks.

**SQLcl reads its settings from inside the folder it scans**, so each run writes them there as a dot-file named `.adt-codescan-<random>.json`, keeps that pattern out of git through `.git/info/exclude`, and removes the file when the scan ends, failed or not. An entry whose `file` sits in another tree is left out of that tree's settings. A single file named with `-input` is scanned without them, since SQLcl looks for its settings inside the path it scans.

An entry missing its `code` or `reason`, a `component` without a `file`, or a key the list does not know stops the run before anything scans, naming the entry by its place in the list:

```text
ERROR - CONFIGURATION INVALID:
------------------------------
  codescan_ignore ENTRY 1 GIVES NO reason

  Each entry needs a code and a reason; file narrows it to a path from the
  project root, component to {type, id} of an APEX component in that file.
```

A suppressed finding is not a finding: it is neither listed nor counted, and the next clean run records the baseline without it. Taking the entry out again brings the finding back as new. The same list applies to the `patch_codescan` gate.

<br>

## The rule profile

`codescan_rules` in `config/config.yaml` names the rules a run reports, by the code SQLcl prints. An empty list, the default, reports every rule. A code closed by `*` takes every rule it starts with, `G-7*` or `APEX-*`, and that is the only wildcard. The same list holds the `patch_codescan` gate.

A code SQLcl does not list is accepted, since every release adds rules; SQLcl's own `codescan -list-rules` prints the ones it knows. A code carrying a space, or a `*` anywhere but its end, stops the run before anything scans.

**The baseline keeps every finding, whatever the profile says.** A run compares and counts only the profile's rules, on both sides, so a rule taken into the profile later finds its old findings already known, and a rule dropped from it is not counted as fixed.

A new finding outside the profile neither fails the run nor stops it recording; it goes into the baseline with the rest. `codescan_ignore` is the other tool: it keeps a finding out of the baseline as well, where the profile only keeps it off the screen.

The shipped `config/config.yaml` carries one profile in its comment, the rules of SQLcl 26.3 that say what the [APEX Blueprint](https://www.oneoracledeveloper.com/2024/06/apex-blueprint-2024-edition.html) coding standards say:

| Rule | The standard it holds |
| ---- | --------------------- |
| `APEX-001` | Every page carries an authorization scheme |
| `APEX-002` | Every page carries an authorization scheme |
| `APEX-029` | The application sets an error handling function, never the default |
| `APEX-049` | Values are bound, never concatenated into SQL |
| `APEX-050` | Values are bound, never concatenated into SQL |
| `APEX-051` | Values are bound, never concatenated into SQL |
| `G-1030` | No dead code |
| `G-1040` | No dead code |
| `G-2110` | `%TYPE` and `%ROWTYPE` wherever a column exists |
| `G-2135` | No dead code |
| `G-3120` | Every column carries its table alias |
| `G-3130` | ANSI joins |
| `G-3210` | No row by row DML in a loop, `BULK COLLECT` and `FORALL` instead |
| `G-3310` | No row by row DML in a loop, `BULK COLLECT` and `FORALL` instead |
| `G-5080` | The error handler logs the backtrace |
| `G-7140` | No dead code |
| `G-7310` | Logic lives in packages |
| `G-7410` | Logic lives in packages |
| `G-7440` | A function returns its value and changes nothing |
| `G-9010` | Explicit conversions, never the session's NLS settings |
| `G-9020` | Explicit conversions, never the session's NLS settings |

Some rules are left out because the standards say the opposite:

- `G-7120`: no name after `END`.
- `G-3180`: `ORDER BY` by position.
- `G-3110`: a whole-record `INSERT ... VALUES rec`.
- `G-5070`: `WHEN NO_DATA_FOUND` in a query function.
- `G-7730`: one compound trigger per table.
- `G-1050`: `0`, `1` and `NULL` need no constant.
- `G-7110`: Oracle's own packages are called by position.
- `PSR-105`: a function sits on the constant side of a predicate.
- `APEX-032`: an item is encrypted only where its data is sensitive.

<br>

## In CI

Export, compile and scan in one line. Each step exits non-zero on what it refuses, so `&&` stops at the first failure and the line's own exit code is the gate:

```bash
adtai export_apex -app 100 -apexlang -files && adtai validate -app 100 && adtai validate -codescan -app 100
```

<br>

## The byte order mark precheck

**SQLcl reads a file opening on a UTF-8 byte order mark as clean**, so every such file is named and the run fails until the mark is gone. Nothing is rewritten:

```text
WARNING - CODESCAN PRECHECK ISSUE:
----------------------------------
  /private/tmp/adt1026/tree
    1 file(s) start with a UTF-8 byte order mark, which codescan reads as clean
    - package_body/bom_pkg.sql
```
