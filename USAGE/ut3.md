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

- its name ends in **`_UT`** — the naming rule is the selection contract, so production code can never be swept into a test run by a loose pattern; and
- utPLSQL has parsed it as a suite — its spec carries a `%suite` annotation and at least one `%test`, and utPLSQL's annotation cache knows about it.

The two facts come from two different places, and `ut3` reads both because neither is sufficient on its own. The data dictionary (`USER_OBJECTS`) is the only place an **INVALID** test package is visible at all; utPLSQL's own metadata (`ut_runner.get_suites_info`) is the only place the annotations have been parsed.

A `_UT` package that satisfies only the first half — it exists, but did not compile, or utPLSQL parsed no `%test` for it — is **ignored**. It is not a suite, and `ut3` reports suites: no row in `UNIT TESTS SUITES:` or `SUMMARY:`, no stanza under `ERRORS & FAILURES:`, and no effect on the exit code. A section headed `ERRORS & FAILURES:` is a list of tests that ran badly, and a package that never ran is not one of them.

The vanished-suite case is still caught, by the zero-test rule below rather than by name: a run that executed no test is a failure, so a schema whose only test package stopped compiling exits non-zero anyway.

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

**It selects the suites to run, and it means that in every mode.** `-name ICT_SEC%` runs the `ICT_SEC%` suites whether or not `-coverage` is passed, so a filtered run costs less than an unfiltered one. Under `-coverage` the same patterns additionally select the packages the report lists, which is the only difference between the two modes.

It worked differently until 2026-08-07: coverage mode dropped the patterns before discovery and applied them to the printed rows alone, so `-coverage -name ICT_INT%` ran the whole schema's suites and cost the same 38 seconds as `-name ICT%` while looking like it filtered. The reasoning was sound — coverage of a package comes from whatever executed it, so narrowing the run can under-report — and it lost anyway: a flag that silently means two things depending on a second flag is the worse defect. See `-coverage`'s own section for what the change costs.

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

CONNECTING TO SCHEMA ict_owner, DEV:
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
    TEST_TOTALS#ADDS_UP ............................................... PASSED
    TEST_LABELS#TRIMS_WHITESPACE ...................................... PASSED
    TEST_TOTALS#ROUNDS_HALF_EVEN ...................................... FAILED
    TEST_LABELS#LOOKUP_RAISES .......................................... ERROR

  ICT_SEC_SECURITY_UT
    TEST_MATCH_COMMODITY#EXACT_PRODUCT_CODE ........................... PASSED
    TEST_MATCH_COMMODITY#PRODUCT_CODE_CASE_FOLDED ..................... PASSED


ERRORS & FAILURES:
------------------

  FAILED > ADT_FIXTURE_UT.TEST_TOTALS#ROUNDS_HALF_EVEN
    Actual: 3 (number) was expected to equal: 2 (number)
    at "ICT_OWNER.ADT_FIXTURE_UT.TEST_TOTALS#ROUNDS_HALF_EVEN", line 19
    ut.expect(ROUND(2.5)).to_equal(2);

  ERROR > ADT_FIXTURE_UT.TEST_LABELS#LOOKUP_RAISES
    ORA-20501: adt_fixture: unhandled error, on purpose
    ORA-06512: at "ICT_OWNER.ADT_FIXTURE_UT", line 32


SUMMARY:
--------

  SUITE PACKAGE         PASSED   FAILED   ERRORED   TIMER
  -------------------   ------   ------   -------   -----
  ADT_FIXTURE_UT             2        1         1     0.3
  ICT_SEC_SECURITY_UT       14                        1.6


TIMER: 2s
```

That listing is a real run against `ICT_OWNER@ORCLPDB1`, trimmed only by dropping 12 of the 14 `ICT_SEC_SECURITY_UT` rows. The two `TIMER` cells are the one exception: that run predates the column, so the seconds are illustrative and only the two-second total beneath them was measured.

**`UNIT TESTS SUITES:` is a per-suite roll-up, and it always prints.** Two columns — the suite package and how many tests it holds — so the section answers "what is about to run" at a glance rather than scrolling a row per test. The header and the column row print even when the list is empty: a run that matched nothing reports it in the same shape as a run that matched ten, and the failure is carried by the exit code, not by a block of troubleshooting advice. **Only runnable suites are listed**, and a package that is not one is listed nowhere else either.

**Order is fixed and not the reporter's.** Packages print A-Z. Tests print in the order the **package specification** declares them — `USER_PROCEDURES.SUBPROGRAM_ID`, not alphabetically and not in the order utPLSQL happened to walk its suite tree — so a results block reads down the same way the source does. The results, the stanzas, and the counts all follow that one order.

**Test rows print the procedure name, never the `%test` description.** A JUnit `testcase name` is not an identifier: utPLSQL puts the description there whenever the annotation carries one, and falls back to the procedure name only for an undescribed test. `ut3` matches the reported value back through `ut_runner.get_suites_info` — which holds both spellings for every discovered test — and prints the **procedure name**, because that is what a reader greps for in the package source.

**`TEST RESULTS:` is printed as the run proceeds, not collected and dumped at the end.** The package name lands on the terminal before `ut.run` is called for that suite, so a slow suite visibly hangs on its own name; its test rows print the moment the verdict is known, and the next package name follows. utPLSQL runs a whole suite in one call, so the individual test rows cannot stream any earlier than that — the suite is the unit of progress.

**`TEST RESULTS:` and `ERRORS & FAILURES:` share one indentation grid.** Both sections have the same shape — a heading naming what follows, then its detail — so a package name sits at two spaces where a stanza heading sits, and a test row at four where a wrapped message line sits. A test row is still fixed-width: the extra indent eats dots, so every verdict stays flush with the right edge the tables align to.

**Free text is a stanza list, not a table column.** A failure message is prose (utPLSQL prints the expectation, the actual value, and the source location), and a shared table column is sized to its widest cell, so one long message widens the whole table past the terminal and destroys the alignment the table existed for. `ERRORS & FAILURES:` therefore carries every non-passing test as a heading with the reason wrapped at 80 columns and indented four beneath it, one blank line above each stanza.

**The status leads the heading** — `ERROR > ADT_FIXTURE_UT.TEST_LABELS#LOOKUP_RAISES`, not the name with the verdict trailing. A reader scanning this section is looking for which ones errored, and a status word parked behind a package-qualified identifier is behind the longest and most variable part of the line. A raised exception reads `ERROR`; a refused expectation reads `FAILED`.

**`SUMMARY:` is the suites table again, with what each suite's tests did.** The same first column, so the two sections read as before-and-after of one list, plus `PASSED` / `FAILED` / `ERRORED`. **A zero renders as an empty cell** — a column of `0`s competes for the eye with the counts that matter, and the row a reader scans for is the one that is not all-passed.

**There is no `TESTS` column here.** Every test lands in exactly one verdict, so the total was a fourth number derivable from the other three, and `UNIT TESTS SUITES:` above already carries the count. The one thing it said alone was that a `%disabled` test exists — it appears in no verdict column — and that test's own result row still reads `SKIPPED`, so a suite quietly disabled wholesale shows up as a package with rows and no counts.

**`TIMER` closes the row with that suite's own seconds**, right-aligned, one decimal always present, and no unit — the header names it once. It is wall clock around the suite's `ut.run` call, not the sum of utPLSQL's per-test `time` attributes: a suite spends as much of its time in `%beforeall`, teardown and the round trip as in its assertions, and a column that left those out would not account for the total printed at the bottom of the run. **It is the one column a zero does not blank out of** — a suite that finished inside a tenth of a second reads `0.0`, because it was measured, where an empty cell would claim it was not. Only a suite that never ran has nothing to print. The shared `TIMER: 2s` footer below the table is a different thing: the whole command, including connecting and discovery.

**`-silent` drops the two listings and nothing else.** `UNIT TESTS SUITES:` and `TEST RESULTS:` are suppressed; the banner, the connection block, `SUMMARY:`, and the timer stay, and so does `ERRORS & FAILURES:` whenever a run has something to put in it. A green run is then four lines and a table. A red one still says which test failed and why — the flag makes a passing run quiet, not a failing run unreadable, and a `FAILED` count whose message is reachable only by re-running without the flag is not a report. `ERRORS & FAILURES:` prints under `-silent` on exactly the same condition as without it: at least one test failed or errored.

## Code coverage

`-coverage` replaces the run report with a coverage report. The suites still execute — running the code is how Oracle collects block coverage, and there is no other way to get it — but they do it quietly: `UNIT TESTS SUITES:` and `TEST RESULTS:` are suppressed, exactly as `-silent` suppresses them. `SUMMARY:` still prints, carrying the coverage roll-up rather than the per-suite one.

```bash
adtai ut3 -coverage
```

```text
CODE COVERAGE:
--------------

  PACKAGE             LINES   PASSED   FAILED   ERRORED   COVERAGE
  -----------------   -----   ------   ------   -------   --------
  ICT_ADM_NUMBERING     477                                   41.9
  ICT_COM_INVOICE       224       20                          88.0
  ICT_FRG_FREIGHT      1022                                    1.0
  ICT_NTF_PREF          240                                   67.4
  ICT_VPD               209                                   53.1


NO CODE COVERAGE:
-----------------

  PACKAGE         LINES
  -------------   -----
  CORE             4645
  CORE_CUSTOM        75
  ICT_ADM_ADMIN      91


SUMMARY:
--------

  PACKAGES   LINES   COVERED   COVERAGE
  --------   -----   -------   --------
         8    6983       622       36.6


TIMER: 23s
```

That listing is a real run against `ICT_OWNER@ORCLPDB1`, trimmed to 5 of the 18 covered rows and 3 of the 42 uncovered, and re-rendered in the current format. The five covered rows are the ones whose block pairs that run recorded — 26/62, 22/25, 1/100, 31/46 and 17/32 — so the percentages, the roll-up's included, are checkable by hand. **The `SUMMARY:` row is computed over the trimmed listing above it**, not over the whole run, which rolled up to 18 covered and 42 uncovered packages. Its `COVERED` cell is the one illustrative figure in the block: that run predates the column, so no covered-line count was recorded for it.

**Two tables, because they answer two questions.** The first says how well the covered packages are covered. The second is the work list — every package nothing executed — and it is the reason the section exists at all: a report built from coverage data alone can only describe packages that *were* executed, so the untested package, the one the reader opened the report to find, would silently not be there. Mixing the two into one list buries the gap among the rows that are fine.

**The two tables do not carry the same columns, and that is deliberate.** The split is on whether anything executed the package, so in the second table every column that split determines is constant: the percentage could only ever read as absent, and the verdicts could only ever describe tests that covered nothing. What survives is the two facts that do vary — which package, and how much code is in it. Making the halves symmetrical is symmetry for its own sake, and it costs a wide empty stripe on every row of the list you are actually meant to work from.

**`LINES` is the package body's own row count** — `USER_SOURCE` with `TYPE = 'PACKAGE BODY'`, for the listed package, never its `_UT` partner and never the spec, since a count of declarations is not a measure of code. It is what makes the second table a priority order rather than an alphabet: `CORE` at 4645 untested lines and `ICT_ADM_ADMIN` at 91 are not the same problem. In the first table it says what the percentage beside it is a percentage of. A package with a spec and no body still gets its row, reading blank.

**`_UT` packages are the one exclusion.** They pair 1:1 with the packages under test, so listing them doubles the report and invites you to measure the coverage of the tests themselves.

**`PASSED`, `FAILED` and `ERRORED` come from the package's `_UT` partner by name.** Block coverage records which blocks ran, never which test ran them, so no data source can attribute execution back to a test. `ICT_VPD` above has real coverage and blank verdicts: five suites exercise it — `ICT_VPD_POLICY_UT`, `ICT_VPD_TENANCY_UT` and three more — and not one of them is named `ICT_VPD_UT`, so there is nothing to credit. That asymmetry is honest: the blocks were measured, the ownership was inferred, and only 3 of that schema's 18 covered packages have a namesake suite at all. There is no `TESTS` column, because the three verdicts sum to it.

**`COVERAGE` is right-aligned, one decimal place, and carries no `%`.** It is a number, and a column of numbers reads on its units digit. The unit is on the header, so repeating it on every row bought nothing and cost a character of width on each; variable precision cost more, because with trailing zeros stripped `100`, `88` and `53.1` ended at three different offsets and the figures did not stack under each other even flush right. A fixed decimal place puts every units digit in the same column. Alignment is still declared rather than detected: the table sniffs cells with `isnumeric()`, which rejects the decimal point, so `53.1` is no more numeric to it than `53.13%` was.

**`-name` narrows the run here exactly as it does without `-coverage`.** `adtai ut3 -coverage -name ICT_VPD%` runs the `ICT_VPD%` suites and lists the `ICT_VPD%` packages — one flag, one meaning, and a filtered run that actually costs less than an unfiltered one.

**The cost is a figure that can read low, and it is deliberate.** Coverage of a package is produced by whatever executed it, which is not necessarily its own `_UT` partner: `ICT_VPD` above is covered by `ICT_VPD_POLICY_UT`, `ICT_VPD_TENANCY_UT` and three more, so a pattern that excludes those suites reports less coverage for `ICT_VPD` than the schema really has. Until 2026-08-07 the run was left unfiltered for precisely that reason; Jan's call (`#231`) is that a `-name` meaning two different things by mode is worse than a figure that is low when you narrow the run. **For the schema's true numbers, run `-coverage` with no `-name`.**

**A pattern matching no suite runs nothing and exits non-zero**, the same as a plain run that matches nothing — an empty green run is what a suite that stopped compiling looks like from the outside, in either mode.

**`CODE COVERAGE:` prints before the suites run, not after.** The run is silent by design here and a real schema spends tens of seconds in it, so a header that waited for the figures left the connection block as the last thing on screen for the whole run — which reads as a hang on connecting rather than as work in progress. Only the header can go first; the table under it is what the run produces.

**`SUMMARY:` closes the section with one row for the whole schema** — `PACKAGES` and `LINES` over the entire listing, both tables together, so the row is the size of the codebase rather than of its tested part. A reader who wants the split has the two tables immediately above it; the roll-up exists to answer the one question they were scrolling towards.

**`COVERED` is source lines that actually executed.** It comes from the block map's own line number — `dbmspcc_blocks` is keyed on `line` and `col`, so counting the distinct lines carrying a covered block is a real executed-line count out of data the report already reads. It is deliberately *not* the line count of every package that has some coverage: a package covered 5% would then contribute all of its lines, and the column would be measuring reach rather than execution. utPLSQL does pair a `DBMS_PROFILER` run through `ut_coverage_runs.line_coverage_id`; reading it would mean a second set of tables for a number the first source already answers, which is the kind of unmeasured Oracle assumption that broke this command once already.

**`COVERED` over `LINES` is not `COVERAGE`, on purpose.** The three count different things, and the row is labelled so nobody has to divide them to find out. `LINES` is every source row of every package body — comments, blank lines and declarations included, none of which Oracle instruments. `COVERED` is instrumented lines that ran. `COVERAGE` is covered blocks over measured blocks, the same figure the per-package column carries. In the sample above that is 622 executed lines against 6983 source lines and a 36.6% block figure; forcing the three to agree would mean either dropping the line counts or computing a percentage against a denominator full of code no coverage tool ever looks at.

**`COVERAGE` blanks when nothing was measured.** The package and line counts are true whether or not a test ever ran, but a percentage is a claim about collected data — the same rule a package row follows. `COVERED` still reads `0` in that case: nothing executed is a measured zero, not an absent measurement.

**`ERRORS & FAILURES:` still prints.** A red run says why, whichever flag asked for the report — the `FAILED` column above would otherwise be a count with no reachable message, which is the same argument that keeps the stanzas under `-silent`. It sits between the tables and `SUMMARY:`, so both modes read the same way round: the detail, then whatever went wrong, then the roll-up. On a green run the output is the two tables and the roll-up.

**`PLSQL_OPTIMIZE_LEVEL` is not a prerequisite.** Level 2 is Oracle's default and block coverage is collected there regardless — the optimizer reshapes the *line* map `DBMS_PROFILER` reads, not the basic-block map this figure is built on. `ut3` flagged anything above level 1 until 2026-08-06, which made every package of every default database read `OPTIMIZE>1` and printed no percentage at all; measured on a schema whose 78 package bodies are all at level 2, 36 of them carried real block coverage.

### Which coverage source, and why

Oracle has two, and they do not measure the same thing. `DBMS_PROFILER` is **line**-level — "was this source line executed". `DBMS_PLSQL_CODE_COVERAGE` is **basic-block**-level — "was this single-entry single-exit block executed". One line holds several blocks and one block spans several lines, so the two row sets cannot agree, and a report that compares a profiler line count to a block count is comparing different units of measure.

utPLSQL already runs **both**, in parallel, on 12.2 and above, and pairs them in `ut_coverage_runs (coverage_run_id, line_coverage_id, block_coverage_id)` so a line the profiler saw execute can be marked *partially* covered when only some of its blocks were hit. `ut3` therefore does not start its own collection or reconcile the two by hand — it calls `ut_runner.coverage_start` / `coverage_stop` and reads the result through that mapping. The percentage reported is the **block** figure, which is the finer of the two.

One deliberate disagreement with utPLSQL's own HTML percentage: blocks marked through the `COVERAGE` pragma (`NOT_FEASIBLE`, `NOT_FEASIBLE_START`, `NOT_FEASIBLE_END`) are **excluded from the denominator**. Oracle flags such a block in `dbmspcc_blocks` rather than dropping it, so subtracting it is the reporter's job; utPLSQL has an open defect that leaves pragma-excluded lines in its metrics. A figure that honours the pragma reads higher than utPLSQL's, and that is intended — the pragma exists so an author can say "this block is not coverable".

## Reporting

`ut3` runs each suite through utPLSQL's **JUnit reporter** and parses the XML, rather than pattern-matching the documentation reporter's summary line. The reason is the same one that makes the exit code interesting: the counters are the only in-database signal that a run failed, and reading them out of prose makes the parser — not the tests — the weakest part of the gate. From XML, a test's name, class, duration, and failure message are markup, and a document that cannot be parsed is an obvious error instead of a silent zero.

## Requirements

- **utPLSQL v3 installed**, and the connected schema holding `EXECUTE` on `ut` and `ut_runner` plus the `ut_*` types, with the matching synonyms. This is utPLSQL's standard `ut3_user` grant set.
- Test packages compiled into the schema being tested. `ut3` runs suites; it does not install them.
- The command uses the ordinary query path, never the read-only one: running a test writes to utPLSQL's own output buffer, and a `SET TRANSACTION READ ONLY` session makes the reporter's data producer fail to start (`ORA-20215`) rather than reporting anything.

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | the configured default environment | Connection environment to test against. |
| `-name`, `--name` | Yes | everything | Name pattern(s), comma- or space-separated, with `%` and `_` LIKE wildcards. Selects the `_UT` suites to run, in every mode; with `-coverage` the same patterns also select the packages the report lists. |
| `-schema`, `--schema` | Yes | every configured default schema | Schema(s) to test; repeatable, comma- or space-separated, `%` patterns expanded against the configured schemas. Each schema runs as its own console segment with its own timer. |
| `-refresh`, `--refresh` | No | off | Rebuild utPLSQL's annotation cache for the schema before discovery, so a suite compiled since the last run is found. |
| `-coverage`, `--coverage` | No | off | Report coverage instead of the run: the suites still execute, quietly, under a `CODE COVERAGE:` header printed before they start. The output is `CODE COVERAGE:` (package, body lines, verdicts, percent), `NO CODE COVERAGE:` (package and body lines only), and a one-row `SUMMARY:` — packages, body lines, executed lines, percent — for the whole schema. Every package in the schema is in one table or the other. |
| `-silent`, `--silent` | No | off | Suppress `UNIT TESTS SUITES:` and `TEST RESULTS:`; keep the banner, connection block, `ERRORS & FAILURES:` when a run has any, `SUMMARY:`, and the timer. |
| `-debug`, `--debug` | No | off | Show input parameters and every SQL statement with its bind values, and keep Python tracebacks for troubleshooting. |
| `-key`, `--key` | No | none | Encryption key, or path to a key file, for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

## Notes

- One `ut.run` call per suite, not one per test: the fixtures run once each, and the per-suite call is what lets the progress row stream.
- A **skipped** test (`%disabled`) neither passes nor fails the run. Its row reads `SKIPPED`, and it lands in no `SUMMARY:` verdict column, so a suite quietly disabled wholesale shows up as a package with test rows and no counts rather than as green.
- Installing test packages is out of scope. `ut3` reads the schema and runs what is there; deploying a suite is the project's own deployment path.

---

← [USAGE.md](../USAGE.md) index
