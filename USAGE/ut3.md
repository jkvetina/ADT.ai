# Run utPLSQL Test Suites (adtai ut3)

`ut3` runs the [utPLSQL](https://www.utplsql.org) v3 test suites installed in a configured Oracle schema, prints the suites it is about to run, then prints each suite's verdicts as it finishes — and turns the result into an exit code.

Run every suite in the schema:

```bash
adtai ut3
```

Run the suites whose package name matches a pattern:

```bash
adtai ut3 -name ICT_SEC%
```

Run several patterns, and across several schemas:

```bash
adtai ut3 -name ICT_SEC% ICT_COM%
adtai ut3 -schema APP CORE
```

## What Counts As A Test Suite

A package is run when **both** are true:

- its name matches **`ut_pattern`** — the naming rule is the selection contract, so production code can never be swept into a test run by a loose pattern; and
- utPLSQL has parsed it as a suite — its spec carries a `%suite` annotation and at least one `%test`, and utPLSQL's annotation cache knows about it.

The two facts come from two different places, and `ut3` reads both because neither is sufficient on its own. The data dictionary (`ALL_OBJECTS`) is the only place an **INVALID** test package is visible at all; utPLSQL's own metadata (`ut_runner.get_suites_info`) is the only place the annotations have been parsed.

A matched package that satisfies only the first half — it exists, but did not compile, or utPLSQL parsed no `%test` for it — is **ignored**. It is not a suite, and `ut3` reports suites: no row in `UNIT TESTS SUITES:` or `SUMMARY:`, no stanza under `ERRORS & FAILURES:`, and no effect on the exit code. A section headed `ERRORS & FAILURES:` is a list of tests that ran badly, and a package that never ran is not one of them.

The vanished-suite case is still caught, by the zero-test rule below rather than by name: a run that executed no test is a failure, so a schema whose only test package stopped compiling exits non-zero anyway.

## The Naming Convention Is Configuration

Four config values describe how a project names its test packages. All four are **Oracle regular expressions**, and Oracle is what evaluates them: `REGEXP_LIKE` selects the test packages inside the dictionary query — where the old `LIKE` sat — and `REGEXP_SUBSTR` extracts the capture groups in the same pass. Nothing is fetched to be discarded, and there is no second regex engine to disagree. Matching is case-insensitive.

| Key | Default | What it answers |
| --- | ------- | --------------- |
| `ut_pattern` | `'_UT$'` | Which packages are test packages. Nothing else is ever run. |
| `ut_match` | `'^(.+)_UT$'` | Which package a test package tests — capture group 1. |
| `ut_owner` | `''` | Which schema holds the test packages. Empty means the schema being tested. |
| `ut_module` | `'^[^_]+_([^_]+)'` | Which module a suite belongs to — capture group 1, read off the suite's own name in the query that already selected it. Anchor it to nothing that has to follow the module token: an expression ending `_` cannot read `ICT_VPD_UT`, a module whose whole implementation is one package. Set it to `''` to print no `MODULES:` table. |

A project whose suites are `TEST_ABC` rather than `ABC_UT` configures `ut_pattern: '^TEST_'` and `ut_match: '^TEST_(.+)$'`, and everything else — discovery, the `COVERAGE` column's pairing, the exclusion of test packages from the coverage listing — follows.

**`ut_owner` is the only one that defaults empty.** The other three ship as working values, because a convention nobody can see is not a feature: an unconfigured project gets the `_UT` suffix and the module roll-ups without editing anything.

**`ut_match` and `ut_pattern` are independent.** A suite the first cannot pair still runs; it simply contributes no verdicts to any coverage row, the same honest silence as a suite that tests something other than its namesake.

### Test packages in another schema

`ut_owner` names the schema holding the suites when they do not live beside the code they test — `ABC_UT` owning the tests for `ABC`, say. It scopes discovery, utPLSQL's annotation cache, `-refresh`, and the owner-qualified path `ut.run` is given.

**Coverage is always measured in the schema under test**, never in `ut_owner`: which schema holds the tests and which holds the code are different questions, and conflating them would report the coverage of the test packages themselves.

The connected user needs `SELECT` on the test schema's dictionary rows and `EXECUTE` on its packages. `ut3` reads `ALL_*` views and the utPLSQL public synonyms only — never `DBA_*`, never a dynamic performance view.

## The Exit Code Is The Deliverable

**utPLSQL does not raise when a test fails.** `ut.run` reports the failure and returns normally, so a caller that only watches for an exception sees a clean run. Every dishonest green is therefore non-zero here:

| Outcome | Exit |
| ------- | ---- |
| Every test passed (skipped tests do not fail the run). | `0` |
| Any test failed or errored. | non-zero |
| A suite ran but the reporter returned nothing, or output that could not be parsed. | non-zero |
| Nothing ran at all — no `_UT` package, none matching `-name`, or none that is a runnable suite. | non-zero |

The last row is the important one: **a zero-test run is a failure, not an empty pass.** An empty green run is exactly what a vanished suite looks like from the outside.

That makes the command usable as a deployment gate directly:

```bash
adtai recompile && adtai ut3
```

## Name Patterns Are Oracle LIKE Patterns

`-name` takes Oracle `LIKE` semantics — `%` for any run of characters, `_` for exactly one, `\` to escape either — the same pattern language `recompile -name` and `export_db -name` use, applied through the one shared implementation so a pattern selects identically here and there.

**It selects the suites to run**, so a filtered run costs less than an unfiltered one, and the coverage figures describe whatever those suites reached.

It worked differently until 2026-08-07, when there was a second mode to disagree with: `-coverage` dropped the patterns before discovery and applied them to the printed rows alone, so `-coverage -name ICT_INT%` ran the whole schema's suites and cost the same 38 seconds as `-name ICT%` while looking like it filtered. The reasoning was sound — coverage of a package comes from whatever executed it, so narrowing the run can under-report — and it lost anyway: a flag that silently means two things depending on a second flag is the worse defect. See §Code coverage for what the change costs.

**The section header names the filter.** With `-name` passed, `SUMMARY:` reads `SUMMARY FOR <PATTERNS>:` — upper-cased, several patterns joined with commas — so a roll-up over part of a schema says so in its own heading rather than leaving the reader to remember the command they typed. Case carries no meaning in a LIKE pattern against Oracle identifiers, so `-name ict_sec%` and `-name ICT_SEC%` print one heading, not two. `MODULES:` keeps its own header: it is one section further down and the filter has already been stated.

```bash
adtai ut3 -name %COMMODITY%
adtai ut3 -name ICT_SEC_SECURITY_UT
adtai ut3 -name ICT_SEC% ICT_COM%
```

Patterns are repeatable and space- or comma-separated, and a package matching several patterns still runs once. No `-name` at all means every `_UT` suite in the schema.

Matching is at package level, never at individual-test level, and deliberately: utPLSQL runs a suite's `%beforeall` / `%afterall` fixtures once per invocation, so running a subset test-by-test would re-run the fixtures for each one and stop measuring what the suite actually asserts.

## The Annotation Cache

utPLSQL discovers suites by parsing the `%suite` / `%test` annotations out of package source into a cache of its own. **A freshly compiled package is not in that cache yet**, so it is not discoverable and `ut.run` legitimately reports zero tests straight after an install — which, without the rules above, would read as a clean pass.

`-refresh` rebuilds that cache for the connected schema before discovery:

```bash
adtai ut3 -refresh
```

It is not the default because a rebuild re-parses the schema's source and there is no reason to pay for it on every run. Use it right after deploying or recompiling a test package — and it is the first thing to try when a suite you know exists is **missing from `UNIT TESTS SUITES:`**. An unparsed package is ignored silently, so its absence from that list is the only signal there is.

## Output

The matched suites are rolled up **before** anything runs, then the results print as the run proceeds — one block per suite, opened by the package name as that suite starts and completed by its test rows as it finishes:

```text
APEX DEPLOYMENT TOOL - UT3
-------------------------

CONNECTING TO SCHEMA ICT_OWNER, DEV:
------------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.2.0.0 | ORCLPDB1
             THICK | 23.3

UNIT TESTS SUITES:
------------------

  SUITE PACKAGE         TESTS
  -------------------   -----
  ADT_FIXTURE_UT            4
  ICT_SEC_SECURITY_UT      14


TEST RESULTS:
-------------

  ADT_FIXTURE_UT
    TEST_TOTALS#ADDS_UP ................................................. PASS
    TEST_LABELS#TRIMS_WHITESPACE ........................................ PASS
    TEST_TOTALS#ROUNDS_HALF_EVEN ........................................ FAIL
    TEST_LABELS#LOOKUP_RAISES .......................................... ERROR

  ICT_SEC_SECURITY_UT
    TEST_MATCH_COMMODITY#EXACT_PRODUCT_CODE ............................. PASS
    TEST_MATCH_COMMODITY#PRODUCT_CODE_CASE_FOLDED ....................... PASS


ERRORS & FAILURES:
------------------

  FAIL > ADT_FIXTURE_UT.TEST_TOTALS#ROUNDS_HALF_EVEN
    Actual: 3 (number) was expected to equal: 2 (number)
    at "ICT_OWNER.ADT_FIXTURE_UT.TEST_TOTALS#ROUNDS_HALF_EVEN", line 19
    ut.expect(ROUND(2.5)).to_equal(2);

  ERROR > ADT_FIXTURE_UT.TEST_LABELS#LOOKUP_RAISES
    ORA-20501: adt_fixture: unhandled error, on purpose
    ORA-06512: at "ICT_OWNER.ADT_FIXTURE_UT", line 32


SUMMARY:
--------

  SUITE PACKAGE         PASS   FAIL   ERROR   TIMER   COVERAGE
  -------------------   ----   ----   -----   -----   --------
  ADT_FIXTURE_UT           2      1       1     0.3
  ICT_SEC_SECURITY_UT     14                    1.6       53.1


TIMER: 2s
```

That listing is a real run against `ICT_OWNER@ORCLPDB1`, trimmed only by dropping 12 of the 14 `ICT_SEC_SECURITY_UT` rows, and re-rendered in the current format. The `TIMER` and `COVERAGE` cells are the exception: that run predates both columns, so those figures are illustrative and only the two-second total beneath them was measured. `ADT_FIXTURE_UT`'s blank `COVERAGE` is the real shape for a suite whose `ut_match` target the schema does not hold.

**The four status words are `PASS`, `FAIL`, `ERROR` and `SKIP`**, and each is a single constant that is both the word in a result row and the header of the column that counts it. A row can therefore never read one spelling under a header that reads another. They were `PASSED` / `FAILED` / `ERRORED` / `SKIPPED` until 2026-08-11 (card `#291`).

**`UNIT TESTS SUITES:` is a per-suite roll-up, and it always prints.** Two columns — the suite package and how many tests it holds — so the section answers "what is about to run" at a glance rather than scrolling a row per test. The header and the column row print even when the list is empty: a run that matched nothing reports it in the same shape as a run that matched ten, and the failure is carried by the exit code, not by a block of troubleshooting advice. **Only runnable suites are listed**, and a package that is not one is listed nowhere else either.

**Order is fixed and not the reporter's.** Packages print A-Z. Tests print in the order the **package specification** declares them — `ALL_PROCEDURES.SUBPROGRAM_ID`, not alphabetically and not in the order utPLSQL happened to walk its suite tree — so a results block reads down the same way the source does. The results, the stanzas, and the counts all follow that one order.

**Test rows print the procedure name, never the `%test` description.** A JUnit `testcase name` is not an identifier: utPLSQL puts the description there whenever the annotation carries one, and falls back to the procedure name only for an undescribed test. `ut3` matches the reported value back through `ut_runner.get_suites_info` — which holds both spellings for every discovered test — and prints the **procedure name**, because that is what a reader greps for in the package source.

**`TEST RESULTS:` is printed as the run proceeds, not collected and dumped at the end.** The package name lands on the terminal before `ut.run` is called for that suite, so a slow suite visibly hangs on its own name; its test rows print the moment the verdict is known, and the next package name follows. utPLSQL runs a whole suite in one call, so the individual test rows cannot stream any earlier than that — the suite is the unit of progress.

**`TEST RESULTS:` and `ERRORS & FAILURES:` share one indentation grid.** Both sections have the same shape — a heading naming what follows, then its detail — so a package name sits at two spaces where a stanza heading sits, and a test row at four where a wrapped message line sits. A test row is still fixed-width: the extra indent eats dots, so every verdict stays flush with the right edge the tables align to.

**Free text is a stanza list, not a table column.** A failure message is prose (utPLSQL prints the expectation, the actual value, and the source location), and a shared table column is sized to its widest cell, so one long message widens the whole table past the terminal and destroys the alignment the table existed for. `ERRORS & FAILURES:` therefore carries every non-passing test as a heading with the reason wrapped at 80 columns and indented four beneath it, one blank line above each stanza.

**The status leads the heading** — `ERROR > ADT_FIXTURE_UT.TEST_LABELS#LOOKUP_RAISES`, not the name with the verdict trailing. A reader scanning this section is looking for which ones errored, and a status word parked behind a package-qualified identifier is behind the longest and most variable part of the line. A raised exception reads `ERROR`; a refused expectation reads `FAIL`.

**`SUMMARY:` is the suites table again, with what each suite's tests did.** The same first column, so the two sections read as before-and-after of one list, plus `PASS` / `FAIL` / `ERROR`. **A zero renders as an empty cell** — a column of `0`s competes for the eye with the counts that matter, and the row a reader scans for is the one that is not all-passed.

**There is no `TESTS` column here.** Every test lands in exactly one verdict, so the total was a fourth number derivable from the other three, and `UNIT TESTS SUITES:` above already carries the count. The one thing it said alone was that a `%disabled` test exists — it appears in no verdict column — and that test's own result row still reads `SKIP`, so a suite quietly disabled wholesale shows up as a package with rows and no counts.

**`TIMER` carries that suite's own seconds**, right-aligned, one decimal always present, and no unit — the header names it once. It is wall clock around the suite's `ut.run` call, not the sum of utPLSQL's per-test `time` attributes: a suite spends as much of its time in `%beforeall`, teardown and the round trip as in its assertions, and a column that left those out would not account for the total printed at the bottom of the run. **It is one of the two columns a zero does not blank out of** — a suite that finished inside a tenth of a second reads `0.0`, because it was measured, where an empty cell would claim it was not. Only a suite that never ran has nothing to print. The shared `TIMER: 2s` footer below the table is a different thing: the whole command, including connecting and discovery.

**`COVERAGE` closes the row** with how much of the code that suite tests actually ran. See §Code coverage below for what the figure is and when it blanks.

**`-silent` drops the two listings and nothing else.** `UNIT TESTS SUITES:` and `TEST RESULTS:` are suppressed; the banner, the connection block, `SUMMARY:`, and the timer stay, and so does `ERRORS & FAILURES:` whenever a run has something to put in it. A green run is then four lines and a table. A red one still says which test failed and why — the flag makes a passing run quiet, not a failing run unreadable, and a `FAIL` count whose message is reachable only by re-running without the flag is not a report. `ERRORS & FAILURES:` prints under `-silent` on exactly the same condition as without it: at least one test failed or errored.

### The MODULES table — the same run, grouped

A second table follows `SUMMARY:` whenever `ut_module` is set, which it is by default:

```text
MODULES:
--------

  MODULE NAME   PACKAGES   PASS   FAIL   ERROR   TIMER   COVERAGE
  -----------   --------   ----   ----   -----   -----   --------
  ?                    1      1      1       1     0.3
  COM                  1      1                     0.4       42.0
  SEC                  2      2                     2.3       73.1
                       4      4      1       1     3.0       28.0
```

That block is rendered through the real renderer from constructed figures — it is a shape, not a measurement.

**It answers a different question from the table above it.** `SUMMARY:` says which suite is red; this says which *area* is. On a schema with ninety suites the per-suite table is a list you scroll and this is the one you read.

**`MODULE NAME` replaces `SUITE PACKAGE`, and `PACKAGES` is the new column** — the group's size, and what makes the rest honest: four failures spread over nine suites and four in one are not the same news. Every other column is `SUMMARY:`'s own over the group: the verdicts and `TIMER` summed, `COVERAGE` recomputed rather than averaged (see §Code coverage).

**The last row is the whole run, and its module name is blank.** A `TOTAL` label would be a value in a column of module names — it would sort among them and read as one — so the total is placed rather than labelled. A suite whose name `ut_module` cannot parse groups at the top, and its cell reads `?`: two blank names in one table say nothing about which row is the unattributed group and which is the total, which is exactly how the table was misread (card `#248`). Position is not a label.

## Code coverage

**Every run measures coverage**, and the figure lands as the `COVERAGE` column of `SUMMARY:` and `MODULES:`, immediately after `TIMER`. There is no flag: `-coverage` was a mode until 2026-08-11 (card `#291`) — it replaced the run report with a `CODE COVERAGE:` / `NO CODE COVERAGE:` pair and a roll-up of its own — and Jan folded the figure into the run's own tables.

```bash
adtai ut3
```

```text
SUMMARY:
--------

  SUITE PACKAGE            PASS   FAIL   ERROR   TIMER   COVERAGE
  ----------------------   ----   ----   -----   -----   --------
  ICT_ADM_NUMBERING_UT       12                    2.1       41.9
  ICT_COM_INVOICE_UT         20                    3.4       88.0
  ICT_FRG_FREIGHT_UT          4                    0.9        1.0
  ICT_NTF_PREF_UT             8                    1.2       67.4
  ICT_VPD_POLICY_UT           6                    1.7


MODULES:
--------

  MODULE NAME   PACKAGES   PASS   FAIL   ERROR   TIMER   COVERAGE
  -----------   --------   ----   ----   -----   -----   --------
  ADM                  1     12               3     2.1       41.9
  COM                  1     20                     3.4       88.0
  FRG                  1      4                     0.9        1.0
  NTF                  1      8                     1.2       67.4
  VPD                  1      6                     1.7        0.0
                       5     50               3     9.3       36.6
```

Those blocks are rendered through the real renderer from the per-package figures a 2026-08-06 run against `ICT_OWNER@ORCLPDB1` recorded — 26/62, 22/25, 1/100, 31/46 — regrouped for the current shape. They are a shape, not a measurement of this format.

**`COVERAGE` is a property of the package a suite tests, not of the suite.** The pairing is `ut_match`'s capture group, resolved by Oracle at discovery: `ICT_COM_INVOICE_UT` puts its figure on `ICT_COM_INVOICE`'s block coverage. Two suites testing one package print the same figure, because block coverage records which blocks ran and never which test ran them — attributing execution back to a test is not something any data source records.

**A blank cell means no measurement, and `0.0` means a measurement of zero.** They are different answers and the column keeps them apart:

| Cell | What happened |
| ---- | ------------- |
| `88.0` | Oracle instrumented the package and 88% of its blocks ran. |
| `0.0` | Oracle instrumented the package and nothing entered it. That is a real finding — the suite ran and reached none of its target. |
| blank | Nothing was measured. Either `ut_match` paired the suite to nothing (or to a package the schema does not hold), or the package carries no instrumentation at all — `PLSQL_CODE_TYPE = NATIVE` strips it, so Oracle writes no `dbmspcc_blocks` row however hard the tests hammer it. |

A `NATIVE` marker named that last case until card `#291`; it lived in the removed report's own column and there is no cell left to carry it.

**Coverage is run-scoped, and that is a deliberate trade.** The report is built from the pairings of the suites that ran, so a package no suite tests appears nowhere — no row, no contribution to any module figure, no total. The removed `NO CODE COVERAGE:` table existed precisely to list those packages, and it went with the flag: Jan's call on 2026-08-11 is that the merged column describes what the run reached. **`ut3` no longer answers "what in this schema is untested."** It answers "how much of what these suites test did they reach", which is the question the rest of the table is about.

### The module figure covers the whole group, not its measured part

**A `MODULES:` row is not the average of the rows above it.** It is the group's own figure: covered blocks over measured blocks across the group's target packages, scaled by the share of the group's body lines Oracle measured at all. A group every target of which was measured has a share of 1 and is unchanged, so the scaling can only move a figure that was over-claiming.

The scaling exists because unreached code is invisible to Oracle. A package a suite is supposed to test and never enters produces no `dbmspcc` rows, so it has no denominator to contribute, and pooling the measured blocks alone described the reached part of a group as if it were the group. Jan's run on 2026-08-09 read `COM 3 1639 21 88.0` where `ICT_COM_INVOICE` is 224 well-tested lines and the group's other 1415 had never executed (card `#250`). `LINES` — the `PACKAGE BODY` row count from `ALL_SOURCE`, never the spec and never the test partner's — is the only size every package has, so it is what the scaling uses.

**A group with nothing measured reads `0.0`, not a blank.** Unreached code is 0% covered, and that is the answer the column is scanned for; an empty cell reads as "no data" and files the group under nothing-to-see. Only a group holding no package body at all has nothing to print.

The group rows and the unnamed total go through one helper, so a group and the total beneath it can never be two calculations that drift apart.

### Which coverage source, and why

Oracle has two, and they do not measure the same thing. `DBMS_PROFILER` is **line**-level — "was this source line executed". `DBMS_PLSQL_CODE_COVERAGE` is **basic-block**-level — "was this single-entry single-exit block executed". One line holds several blocks and one block spans several lines, so the two row sets cannot agree, and a report that compares a profiler line count to a block count is comparing different units of measure.

utPLSQL already runs **both**, in parallel, on 12.2 and above, and pairs them in `ut_coverage_runs (coverage_run_id, line_coverage_id, block_coverage_id)`. `ut3` therefore does not start its own collection or reconcile the two by hand — it calls `ut_runner.coverage_start` / `coverage_stop` and reads the result through that mapping. The percentage reported is the **block** figure, which is the finer of the two.

One deliberate disagreement with utPLSQL's own HTML percentage: blocks marked through the `COVERAGE` pragma (`NOT_FEASIBLE`, `NOT_FEASIBLE_START`, `NOT_FEASIBLE_END`) are **excluded from the denominator**. Oracle flags such a block in `dbmspcc_blocks` rather than dropping it, so subtracting it is the reporter's job; utPLSQL has an open defect that leaves pragma-excluded lines in its metrics. A figure that honours the pragma reads higher than utPLSQL's, and that is intended — the pragma exists so an author can say "this block is not coverable".

**`PLSQL_OPTIMIZE_LEVEL` is not a prerequisite.** Level 2 is Oracle's default and block coverage is collected there regardless — the optimizer reshapes the *line* map `DBMS_PROFILER` reads, not the basic-block map this figure is built on. `ut3` flagged anything above level 1 until 2026-08-06, which made every package of every default database read `OPTIMIZE>1` and printed no percentage at all; measured on a schema whose 78 package bodies are all at level 2, 36 of them carried real block coverage.

**Collection costs a session, and every run now pays it.** `-coverage` was opt-in because instrumenting every unit a run touches is not free; the saving that buys it back is the schema-wide package listing, which run-scoping removed along with a per-package compile-settings query.

## Reporting

`ut3` runs each suite through utPLSQL's **JUnit reporter** and parses the XML, rather than pattern-matching the documentation reporter's summary line. The reason is the same one that makes the exit code interesting: the counters are the only in-database signal that a run failed, and reading them out of prose makes the parser — not the tests — the weakest part of the gate. From XML, a test's name, class, duration, and failure message are markup, and a document that cannot be parsed is an obvious error instead of a silent zero.

## Requirements

- **utPLSQL v3 installed**, and the connected schema holding `EXECUTE` on `ut` and `ut_runner` plus the `ut_*` types, with the matching synonyms. This is utPLSQL's standard `ut3_user` grant set.
- Test packages compiled into the schema being tested, or into the `ut_owner` schema when they live in one of their own. `ut3` runs suites; it does not install them.
- With `ut_owner` set, the connected schema also needs `SELECT` on that schema's dictionary rows and `EXECUTE` on its packages.
- The command uses the ordinary query path, never the read-only one: running a test writes to utPLSQL's own output buffer, and a `SET TRANSACTION READ ONLY` session makes the reporter's data producer fail to start (`ORA-20215`) rather than reporting anything.

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | the configured default environment | Connection environment to test against. |
| `-name`, `--name` | Yes | everything | Name pattern(s), comma- or space-separated, with `%` and `_` LIKE wildcards. Selects the suites to run, and names itself in the `SUMMARY FOR <PATTERNS>:` header. LIKE wildcards, not regex — `ut_pattern` is what uses regular expressions. |
| `-schema`, `--schema` | Yes | every configured default schema | Schema(s) to test; repeatable, comma- or space-separated, `%` patterns expanded against the configured schemas. Each schema runs as its own console segment with its own timer. |
| `-refresh`, `--refresh` | No | off | Rebuild utPLSQL's annotation cache for the schema before discovery, so a suite compiled since the last run is found. |
| `-silent`, `--silent` | No | off | Suppress `UNIT TESTS SUITES:` and `TEST RESULTS:`; keep the banner, connection block, `ERRORS & FAILURES:` when a run has any, `SUMMARY:`, and the timer. |
| `-debug`, `--debug` | No | off | Show input parameters and every SQL statement with its bind values, and keep Python tracebacks for troubleshooting. |
| `-key`, `--key` | No | none | Encryption key, or path to a key file, for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

## Notes

- One `ut.run` call per suite, not one per test: the fixtures run once each, and the per-suite call is what lets the progress row stream.
- A **skipped** test (`%disabled`) neither passes nor fails the run. Its row reads `SKIP`, and it lands in no `SUMMARY:` verdict column, so a suite quietly disabled wholesale shows up as a package with test rows and no counts rather than as green.
- Installing test packages is out of scope. `ut3` reads the schema and runs what is there; deploying a suite is the project's own deployment path.
- The naming configuration is per-project, not per-run: there is no flag for `ut_pattern`, `ut_match`, `ut_owner` or `ut_module`. A convention is a property of the codebase, and one that could be overridden per run would make two runs of the same schema disagree about which packages are tests.
- The expressions must be valid **Oracle** regular expressions, since Oracle is what runs them. That is the dialect every Oracle developer already knows, and it is what lets the selection be a predicate in the dictionary query rather than a filter over a fetched schema — on a schema with thousands of packages and a handful of suites, that difference is the round trip.

---

← [USAGE.md](../USAGE.md) index
