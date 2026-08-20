# Run utPLSQL Test Suites (adtai ut)

`ut` runs the [utPLSQL](https://www.utplsql.org) v3 test suites installed in a configured Oracle schema, prints the suites it is about to run, shows a progress bar while it runs them, and turns the result into an exit code. `-verbose` swaps the bar for a verdict per test.

Run every suite in the schema:

```bash
adtai ut
```

Run the suites whose package name matches a pattern:

```bash
adtai ut -name ICT_SEC%
```

Run several patterns, and across several schemas:

```bash
adtai ut -name ICT_SEC% ICT_COM%
adtai ut -schema APP CORE
```

## What Counts As A Test Suite

A package is run when **both** are true:

- its name matches **`ut_pattern`**, the naming rule is the selection contract, so production code can never be swept into a test run by a loose pattern; and
- utPLSQL has parsed it as a suite, its spec carries a `%suite` annotation and at least one `%test`, and utPLSQL's annotation cache knows about it.

The two facts come from two different places, and `ut` reads both because neither is sufficient on its own. The data dictionary (`ALL_OBJECTS`) is the only place an **INVALID** test package is visible at all; utPLSQL's own metadata (`ut_runner.get_suites_info`) is the only place the annotations have been parsed.

A matched package that satisfies only the first half, it exists, but did not compile, or utPLSQL parsed no `%test` for it, is **ignored**. It is not a suite, and `ut` reports suites: no row in `SUMMARY PER SUITE:` or in `-verbose`'s `UNIT TESTS SUITES:`, no stanza under `ERRORS & FAILURES:`, and no effect on the exit code. A section headed `ERRORS & FAILURES:` is a list of tests that ran badly, and a package that never ran is not one of them.

The vanished-suite case is still caught, by the zero-test rule below rather than by name: a run that executed no test is a failure, so a schema whose only test package stopped compiling exits non-zero anyway.

## The Naming Convention Is Configuration

Four config values describe how a project names its test packages. All four are **Oracle regular expressions**, and Oracle is what evaluates them: `REGEXP_LIKE` selects the test packages inside the dictionary query, where the old `LIKE` sat, and `REGEXP_SUBSTR` extracts the capture groups in the same pass. Nothing is fetched to be discarded, and there is no second regex engine to disagree. Matching is case-insensitive.

| Key | Default | What it answers |
| --- | ------- | --------------- |
| `ut_pattern` | `'_UT$'` | Which packages are test packages. Nothing else is ever run. |
| `ut_match` | `'^(.+)_UT$'` | Which package a test package tests, capture group 1. |
| `ut_owner` | `''` | Which schema holds the test packages. Empty means the schema being tested. |
| `ut_module` | `'^[^_]+_([^_]+)'` | Which module a suite belongs to, capture group 1, read off the suite's own name in the query that already selected it. Anchor it to nothing that has to follow the module token: an expression ending `_` cannot read `ICT_VPD_UT`, a module whose whole implementation is one package. Set it to `''` to print no `SUMMARY PER MODULE:` table. |

A project whose suites are `TEST_ABC` rather than `ABC_UT` configures `ut_pattern: '^TEST_'` and `ut_match: '^TEST_(.+)$'`, and everything else, discovery, the `COVERAGE` column's pairing, the exclusion of test packages from the coverage listing, follows.

**`ut_owner` is the only one that defaults empty.** The other three ship as working values, because a convention nobody can see is not a feature: an unconfigured project gets the `_UT` suffix and the module roll-ups without editing anything.

**`ut_match` and `ut_pattern` are independent.** A suite the first cannot pair still runs; it simply contributes no verdicts to any coverage row, the same honest silence as a suite that tests something other than its namesake.

### Two more config values, and neither is a regular expression

| Key | Default | What it answers |
| --- | ------- | --------------- |
| `ut_limit_errors` | `20` | How many `ERRORS & FAILURES:` stanzas print. `0` prints every one. |
| `ut_coverage_gate` | `80` | The `COVERAGE` percentage a tested package must reach when `-gate` is passed bare. Gates nothing unless `-gate` is passed. |

They bound what a run prints and what it accepts, they never reach Oracle, and both are described where they are used, §Output for the cap, §The coverage gate for the threshold.

### Test packages in another schema

`ut_owner` names the schema holding the suites when they do not live beside the code they test, `ABC_UT` owning the tests for `ABC`, say. It scopes discovery, utPLSQL's annotation cache, `-refresh`, and the owner-qualified path `ut.run` is given.

**Coverage is always measured in the schema under test**, never in `ut_owner`: which schema holds the tests and which holds the code are different questions, and conflating them would report the coverage of the test packages themselves.

The connected user needs `SELECT` on the test schema's dictionary rows and `EXECUTE` on its packages. `ut` reads `ALL_*` views and the utPLSQL public synonyms only, never `DBA_*`, never a dynamic performance view.

## The Exit Code Is The Deliverable

**utPLSQL does not raise when a test fails.** `ut.run` reports the failure and returns normally, so a caller that only watches for an exception sees a clean run. Every dishonest green is therefore non-zero here:

| Outcome | Exit |
| ------- | ---- |
| Every test passed (skipped tests do not fail the run). | `0` |
| Any test failed or errored. | non-zero |
| A suite ran but the reporter returned nothing, or output that could not be parsed. | non-zero |
| Nothing ran at all, no `_UT` package, none matching `-name`, or none that is a runnable suite. | non-zero |
| A tested package is below the `-gate` threshold. | non-zero |

The zero-test row is the important one: **a zero-test run is a failure, not an empty pass.** An empty green run is exactly what a vanished suite looks like from the outside.

That makes the command usable as a deployment gate directly:

```bash
adtai recompile && adtai ut
```

## Name Patterns Are Oracle LIKE Patterns

`-name` takes Oracle `LIKE` semantics, `%` for any run of characters, `_` for exactly one, `\` to escape either, the same pattern language `recompile -name` and `export_db -name` use, applied through the one shared implementation so a pattern selects identically here and there.

**It selects the suites to run**, so a filtered run costs less than an unfiltered one, and the coverage figures describe whatever those suites reached.

It worked differently until 2026-08-07, when there was a second mode to disagree with: `-coverage` dropped the patterns before discovery and applied them to the printed rows alone, so `-coverage -name ICT_INT%` ran the whole schema's suites and cost the same 38 seconds as `-name ICT%` while looking like it filtered. The reasoning was sound, coverage of a package comes from whatever executed it, so narrowing the run can under-report, and it lost anyway: a flag that silently means two things depending on a second flag is the worse defect. See §Code coverage for what the change costs.

**The run's own section names the filter.** With `-name` passed, `RUNNING TESTS:` reads `RUNNING TESTS FOR <PATTERNS>:`, upper-cased, several patterns joined with commas, so the one line on screen for the length of the run says which part of the schema is running rather than leaving the reader to remember the command they typed. Case carries no meaning in a LIKE pattern against Oracle identifiers, so `-name ict_sec%` and `-name ICT_SEC%` print one heading, not two.

```text
RUNNING TESTS FOR ICT_INT%:
---------------------------
  66 TESTS ................ 31%                                        0:01:38
```

**The two tables below keep their own headings**, `SUMMARY PER SUITE:` and `SUMMARY PER MODULE:`, in every mode. They carry the same columns over the same run and differ only in how the rows are cut, so what a heading is for down there is saying which of the two you are looking at; the filter has already been stated one section up, and a table that shows only what ran would be restating it.

```bash
adtai ut -name %COMMODITY%
adtai ut -name ICT_SEC_SECURITY_UT
adtai ut -name ICT_SEC% ICT_COM%
```

Patterns are repeatable and space- or comma-separated, and a package matching several patterns still runs once. No `-name` at all means every `_UT` suite in the schema.

Matching is at package level, never at individual-test level, and deliberately: utPLSQL runs a suite's `%beforeall` / `%afterall` fixtures once per invocation, so running a subset test-by-test would re-run the fixtures for each one and stop measuring what the suite actually asserts.

## The Annotation Cache

utPLSQL discovers suites by parsing the `%suite` / `%test` annotations out of package source into a cache of its own. **A freshly compiled package is not in that cache yet**, so it is not discoverable and `ut.run` legitimately reports zero tests straight after an install, which, without the rules above, would read as a clean pass.

`-refresh` rebuilds that cache for the connected schema before discovery:

```bash
adtai ut -refresh
```

It is not the default because a rebuild re-parses the schema's source and there is no reason to pay for it on every run. Use it right after deploying or recompiling a test package, and it is the first thing to try when a suite you know exists is **missing from `ut -verbose`'s `UNIT TESTS SUITES:`**. An unparsed package is ignored silently, so its absence from that list is the only signal there is.

The rebuild is the slowest thing this command can be asked to do, and it runs under the connection block above it. It prints no section of its own.

## Output

Between the connection block and `SUMMARY PER SUITE:` the output belongs to the mode, and there are three:

| Mode | What prints there | What it is for |
| ---- | ----------------- | -------------- |
| default | `RUNNING TESTS:` | One dotted bar: how big the run is, how much of it is done, and roughly how long is left. |
| `-verbose` | `UNIT TESTS SUITES:` then `TEST RESULTS:` | The suites that matched, rolled up before anything runs, then a verdict per test grouped under its package. |
| `-silent` | none | Neither listing and no bar; a green run is four lines and a table. |

Everything below, `ERRORS & FAILURES:`, `SUMMARY PER SUITE:`, `SUMMARY PER MODULE:`, the coverage gate and the `TIMER` footer, is identical in all three, with one exception: `-verbose` also prints `COVERAGE CHANGED SINCE LAST RUN:` above the summaries when there is a previous run to compare against. See [What moved since last time](#what-moved-since-last-time).

**The suites roll-up is verbose output** (card `#348`). It answers "what is about to run", and the bar's own `N TESTS` label carries that in one line, so on a schema of ninety suites the table was ninety rows standing between the connection block and the report. The mode that asks for a row per test is the mode that wants the list of what will produce them.

### Where the waits sit

A run reaches the database in four stretches, and each one sits under the heading of the section it belongs to, printed above its own first read. **`ut` mints no string for any of them:**

| Wait | What is on screen through it |
| ---- | ---------------------------- |
| `-refresh` annotation rebuild | `RUNNING TESTS FOR <PATTERNS>:`, or `UNIT TESTS SUITES:` under `-verbose` |
| discovery | the same heading |
| each suite | the dotted bar, or the package heading under `TEST RESULTS:` |
| coverage read | `SUMMARY PER SUITE:`, the table the read fills |

`#359` gave the first, second and fourth a heading and a row each, `REFRESHING THE ANNOTATION CACHE:`, `DISCOVERING SUITES:` and `MEASURING COVERAGE:`. Jan, 2026-08-16, reading two of them: *"WHAT IS THIS SHIT in UT MODULE? ... I DID NOT FUCKING ASKED FOR EATHER OF THIS SHIT!"* What he had asked for on `#359` was the cursor to stop parking on a finished row, and the fix for that is to move existing structure, not to mint three strings.

**Every heading in that table is one the run was going to print anyway; only its position moved** (`#379`). The reply to `#359` had been to leave the bar open through the coverage read, on the reasoning that an open line announces. It does, of the work that will **close** it, and this row's work was over: measured on `adtai ut -name ICT_INT%` against `ICT_OWNER`, the bar reached `100%  0:00:00` at 9.4 s and the screen did not move again until 19.3 s, when the heading and every row printed together. Jan: *"you are waiting on the progress line and only after you fetch summaries you print header and data."* The same reading was hiding the discovery wait behind the last row of a finished connection block, 2.05 s of the same run.

So the bar now closes when the last suite returns and `SUMMARY PER SUITE:` goes up before the profiler is stopped and read, and the run's own heading goes up before discovery. `tests/contracts/test_no_silent_blocking_phase.py` holds the general rule, and `tests/cli/test_ut_phase_sections.py` pins both halves together: the three invented strings absent, and each wait's heading on screen at the moment its query runs.

One consequence worth stating: the bar's closing time is now the suites' own, not the whole run's. The `TIMER` footer is what reports the run.

### The default run

```text
APEX DEPLOYMENT TOOL - UT
--------------------------

CONNECTING TO SCHEMA ICT_OWNER, DEV:
------------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.2.0.0 | ORCLPDB1
             THICK | 23.3


RUNNING TESTS:
--------------
  66 TESTS ................ 31%                                        0:01:38


SUMMARY PER SUITE:
------------------
```

**The label is the tests the run targets; the bar is bumped by finished suites.** The two units are different on purpose. A test is the unit a run is sized in and it is knowable before anything runs, so it is what the row says out loud, the sum of the `TESTS` column `-verbose` tabulates, which is what makes `-name` narrow the label and the axis together. In this mode the label is the only place that count appears. A suite is the only unit utPLSQL reports back, so it is the only thing that can move a bar honestly. The row is indented two spaces and labelled exactly like an `export_apex` action row, and it opens on the line directly under the dashed rule, for the same reason: one bar, not two dialects of one.

**The bar is bumped by finished suites, never by a clock.** The percentage is the suites completed so far, so it moves when a suite returns and at no other moment, nothing animates in between. That is not a rendering preference: utPLSQL buffers a run's whole reporter output until `ut.run` returns (measured 2026-08-13, a 12-test suite's `post-test` events all reached the client in a 0.5 s burst 22 s after `execute()`), so a suite is the smallest unit of progress that exists to report. A per-suite elapsed-seconds ticker shipped for one release under `#301` and Jan rejected it the same day: *"You will print the package name, when you have a result you will print the rest. There is nothing in between."*

**The time on the right is what is left, not what has passed.** Before the first suite returns the only thing the command knows is what the last run of this schema and `-name` variant cost, see §Estimating The Time Left. From the first return onward the run measures its own rate, and the two are blended by the completed fraction: early the history knows more, and by the last suite the sample *is* the run. The closing row is the exception, at 100% it shows the run's real elapsed time, because there is nothing left to estimate.

**A run that matched nothing prints the heading and no bar.** A bar has no empty form: every percentage it could show beside no work would be a claim rather than a report. The heading is a different thing and it is already on screen by then, because it is what discovery blocked under (`#379`): it says what was looked for, under the filter that was used. What says what was *found* is the empty `SUMMARY PER SUITE:` and the exit code, which is what carried it all along.

### The verbose run

`-verbose` prints the suites roll-up, then replaces the bar with the per-test section, one block per suite, opened by the package name as that suite starts and completed by its test rows as it finishes:

```text
APEX DEPLOYMENT TOOL - UT
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
    TEST_TOTALS#ADDS_UP .................................................. 0.2
    TEST_LABELS#TRIMS_WHITESPACE ......................................... 0.1
    TEST_TOTALS#ROUNDS_HALF_EVEN ........................................ FAIL
    TEST_LABELS#LOOKUP_RAISES .......................................... ERROR

  ICT_SEC_SECURITY_UT
    TEST_MATCH_COMMODITY#EXACT_PRODUCT_CODE .............................. 0.1
    TEST_MATCH_COMMODITY#PRODUCT_CODE_CASE_FOLDED ........................ 0.1


ERRORS & FAILURES:
------------------

  FAIL > ADT_FIXTURE_UT.TEST_TOTALS#ROUNDS_HALF_EVEN
    Actual: 3 (number) was expected to equal: 2 (number)
    at "ICT_OWNER.ADT_FIXTURE_UT.TEST_TOTALS#ROUNDS_HALF_EVEN", line 19
    ut.expect(ROUND(2.5)).to_equal(2);

  ERROR > ADT_FIXTURE_UT.TEST_LABELS#LOOKUP_RAISES
    ORA-20501: adt_fixture: unhandled error, on purpose
    ORA-06512: at "ICT_OWNER.ADT_FIXTURE_UT", line 32


SUMMARY PER SUITE:
------------------

  SUITE PACKAGE         PASS   FAIL   ERROR   TIMER   COVERAGE
  -------------------   ----   ----   -----   -----   --------
  ADT_FIXTURE_UT           2      1       1     0.3
  ICT_SEC_SECURITY_UT     14                    1.6       53.1


TIMER: 2s
```

That listing is a real run against `ICT_OWNER@ORCLPDB1`, trimmed only by dropping 12 of the 14 `ICT_SEC_SECURITY_UT` rows, and re-rendered in the current format. The `TIMER` and `COVERAGE` cells are the exception: that run predates both columns, so those figures, and the two per-test timers below them, added after this listing was captured, are illustrative, and only the two-second total beneath them was measured. `ADT_FIXTURE_UT`'s blank `COVERAGE` is the real shape for a suite whose `ut_match` target the schema does not hold.

**A passing row shows its own elapsed seconds, not the word `PASS`.** One decimal place, always present, a clean test's own timing is a fact the reader did not already have, where `PASS` only ever restates that the row carries none of `FAIL`/`ERROR`/`SKIP`. `FAIL`, `ERROR` and `SKIP` still print their status word, because that word is the one thing a reader scans this section for. Jan, 2026-08-13, confirmed live against `ICT_OWNER@ORCLPDB1`: a 43-test `ICT_COM_COSTING_UT` run, all passing, printed every row's own time between `0.9` and `1.3` seconds and nowhere the word `PASS`.

**The four status words are `PASS`, `FAIL`, `ERROR` and `SKIP`**, and each is a single constant that is both the header of the column that counts it and, for `FAIL`/`ERROR`/`SKIP`, the word a result row prints. A row can therefore never read one spelling under a header that reads another. They were `PASSED` / `FAILED` / `ERRORED` / `SKIPPED` until 2026-08-11 (card `#291`).

**`UNIT TESTS SUITES:` is a per-suite roll-up, and inside this mode it always prints.** Two columns, the suite package and how many tests it holds, so the section answers "what is about to run" at a glance rather than scrolling a row per test. The header and the column row print even when the list is empty: a run that matched nothing reports it in the same shape as a run that matched ten, and the failure is carried by the exit code, not by a block of troubleshooting advice. **Only runnable suites are listed**, and a package that is not one is listed nowhere else either.

**Order is fixed and not the reporter's.** Packages print A-Z. Tests print in the order the **package specification** declares them, `ALL_PROCEDURES.SUBPROGRAM_ID`, not alphabetically and not in the order utPLSQL happened to walk its suite tree, so a results block reads down the same way the source does. The results, the stanzas, and the counts all follow that one order.

**Test rows print the procedure name, never the `%test` description.** A JUnit `testcase name` is not an identifier: utPLSQL puts the description there whenever the annotation carries one, and falls back to the procedure name only for an undescribed test. `ut` matches the reported value back through `ut_runner.get_suites_info`, which holds both spellings for every discovered test, and prints the **procedure name**, because that is what a reader greps for in the package source.

**`TEST RESULTS:` is printed as the run proceeds, not collected and dumped at the end.** The package name lands on the terminal before `ut.run` is called for that suite, so a slow suite visibly hangs on its own name; its test rows print the moment the verdict is known, and the next package name follows. utPLSQL runs a whole suite in one call, so the individual test rows cannot stream any earlier than that, the suite is the unit of progress, which is also why the default mode's bar counts suites.

**`TEST RESULTS:` and `ERRORS & FAILURES:` share one indentation grid.** Both sections have the same shape, a heading naming what follows, then its detail, so a package name sits at two spaces where a stanza heading sits, and a test row at four where a wrapped message line sits. A test row is still fixed-width: the extra indent eats dots, so every verdict stays flush with the right edge the tables align to.

**A test name longer than the grid is trimmed in the middle, never left to overhang it.** `TEST_SEND_FROM_APEX#DISPATCHES_EVERY_PENDING_NOTIFICATION_FOR_THE_INCIDENT` needs 83 of a 78-column row and prints as `TEST_SEND_FROM_APEX#DISPATCHES_E...NG_NOTIFICATION_FOR_THE_INCIDENT`. The middle goes rather than the tail, because a `<procedure>#<test name>` label shares its procedure with every other row in the block and the tail is what tells them apart; the dot leader never falls below two, since one dot reads as the end of a line rather than as a leader. Nothing that already fits is touched. Card `#436`, after Jan measured the overhang: *"some names are too long, you have to trim them."*

**Free text is a stanza list, not a table column.** A failure message is prose (utPLSQL prints the expectation, the actual value, and the source location), and a shared table column is sized to its widest cell, so one long message widens the whole table past the terminal and destroys the alignment the table existed for. `ERRORS & FAILURES:` therefore carries every non-passing test as a heading with the reason wrapped at 80 columns and indented four beneath it, one blank line above each stanza.

**The status leads the heading**, `ERROR > ADT_FIXTURE_UT.TEST_LABELS#LOOKUP_RAISES`, not the name with the verdict trailing. A reader scanning this section is looking for which ones errored, and a status word parked behind a package-qualified identifier is behind the longest and most variable part of the line. A raised exception reads `ERROR`; a refused expectation reads `FAIL`.

**The section is capped at `ut_limit_errors`, and the header says when the cap bit.** With more problems than the limit the heading reads `FIRST 20 ERRORS & FAILURES:`; with fewer it stays plain, the same rule `RUNNING TESTS FOR <PATTERNS>:` follows one section up. This exists because of a measured run: `ICT_OWNER@ORCLPDB1` on 2026-08-11 printed 397 stanzas over 3 060 lines and pushed `SUMMARY PER SUITE:`, header, column row, separator and all, off the terminal's scrollback, so a correct report was unreadable and read like a renderer that had dropped its own headers. **The cap never touches the counts:** `SUMMARY PER SUITE:` and `SUMMARY PER MODULE:` report every failure and error, so the two disagreeing is the signal that there is more detail than the screen. Set `ut_limit_errors: 0` to print every stanza.

**`SUMMARY PER SUITE:` is the suites table again, with what each suite's tests did.** The same first column, so under `-verbose` the two sections read as before-and-after of one list, plus `PASS` / `FAIL` / `ERROR`. **A zero renders as an empty cell**, a column of `0`s competes for the eye with the counts that matter, and the row a reader scans for is the one that is not all-passed.

**There is no `TESTS` column here.** Every test lands in exactly one verdict, so the total is a fourth number derivable from the other three. The one thing it would carry alone is the `%disabled` test, which appears in no verdict column and whose own result row still reads `SKIP`, so a suite quietly disabled wholesale shows up as a package with rows and no counts.

**`TIMER` carries that suite's own seconds**, right-aligned, one decimal always present, and no unit, the header names it once. It is wall clock around the suite's `ut.run` call, not the sum of utPLSQL's per-test `time` attributes: a suite spends as much of its time in `%beforeall`, teardown and the round trip as in its assertions, and a column that left those out would not account for the total printed at the bottom of the run. **It is one of the two columns a zero does not blank out of**, a suite that finished inside a tenth of a second reads `0.0`, because it was measured, where an empty cell would claim it was not. Only a suite that never ran has nothing to print. The shared `TIMER: 2s` footer below the table is a different thing: the whole command, including connecting and discovery.

**`COVERAGE` closes the row** with how much of the code that suite tests actually ran. See §Code coverage below for what the figure is and when it blanks.

**`-silent` drops whatever the mode would have printed there and nothing else.** The bar on a default run, and both listings under `-verbose`. The banner, the connection block, `SUMMARY PER SUITE:`, and the timer stay, and so does `ERRORS & FAILURES:` whenever a run has something to put in it. A green run is then four lines and a table. A red one still says which test failed and why, the flag makes a passing run quiet, not a failing run unreadable, and a `FAIL` count whose message is reachable only by re-running without the flag is not a report. `ERRORS & FAILURES:` prints under `-silent` on exactly the same condition as without it: at least one test failed or errored.

**`-silent` outranks `-verbose`**, so `-silent -verbose` is `-silent`. Two flags about one region of the screen, and the one that removes it wins, otherwise the quiet flag would print the largest section the command has.

## Estimating The Time Left

The countdown on the progress bar is seeded from `config/internal/ut_timers.yaml` under the project root, written at the end of every run:

```yaml
ICT_OWNER:
  '%': 142.6
  ICT_SEC%: 8.31
```

**The key is the schema and the `-name` variant together**, because `-name` selects the suites that *run*, not just the rows that print, so a filtered run is a genuinely smaller job. Keying on the schema alone would let an eight-second `-name ICT_SEC%` run seed the countdown of a two-minute full run and vice versa. The patterns are upper-cased and sorted into the key, so `-name ict_sec% ict_com%` and `-name ICT_COM% ICT_SEC%`, which select exactly the same suites, accumulate one history rather than two. An unfiltered run keys on `%`.

The stored figure is a rolling `(this run + previous) / 2`, the same average `apex.db` uses for the APEX export bar: a plain overwrite would make the estimate as noisy as the noisiest run, and a mean over all history would stop tracking a suite that genuinely got slower.

**Every mode records, and a run that executed nothing records nothing.** `-verbose` and `-silent` measure the same work the bar does, so a reader who normally runs quiet still has a history the one run they want the estimate for can read. A schema whose suites all stopped compiling, on the other hand, finishes in no time at all, storing that would seed `0:00:00` into the next real run.

`config/internal/` is where ADT.ai keeps everything it writes about a project, the APEX metadata caches, the `-recent` watermark, the dependency and flow stores, separated from the `config/` files a human edits. It is gitignored by the shipped `.gitignore`. Deleting the timers file costs one run's worth of estimate: the next run counts down from `0:00:00` until its first suite returns, then measures itself.

### The per-module table, the same run, grouped

A second table follows `SUMMARY PER SUITE:` whenever `ut_module` is set, which it is by default:

```text
SUMMARY PER MODULE:
-------------------

  MODULE NAME   PACKAGES   LINES   PASS   FAIL   ERROR   TIMER   COVERAGE
  -----------   --------   -----   ----   ----   -----   -----   --------
  ?                    1            1      1       1     0.3
  COM                  1     224    1                     0.4       42.0
  SEC                  2     880    2                     2.3       73.1
                       4    1104    4      1       1     3.0       28.0
```

That block is rendered through the real renderer from constructed figures, it is a shape, not a measurement.

**It answers a different question from the table above it.** `SUMMARY PER SUITE:` says which suite is red; this says which *area* is. On a schema with ninety suites the per-suite table is a list you scroll and this is the one you read.

**`MODULE NAME` replaces `SUITE PACKAGE`, and `PACKAGES` and `LINES` are the two new columns**, the group's size in suites and in code, and together they are what make the rest honest: four failures spread over nine suites and four in one are not the same news, and neither are ninety percent of forty lines and ninety percent of four thousand. Every other column is `SUMMARY PER SUITE:`'s own over the group: the verdicts and `TIMER` summed, `COVERAGE` recomputed rather than averaged (see §Code coverage).

**`LINES` counts the packages the group's suites test, not the suites**, and counts each package once however many suites name it, the same deduplicated set `COVERAGE` beside it is computed over, so the two columns can never describe two different bodies of code. It is the denominator the group's coverage is scaled by, printed rather than left to be inferred. A group whose suites pair to nothing has no lines to count and blanks, exactly where its `COVERAGE` blanks too.

**The last row is the whole run, and its module name is blank.** A `TOTAL` label would be a value in a column of module names, it would sort among them and read as one, so the total is placed rather than labelled. A suite whose name `ut_module` cannot parse groups at the top, and its cell reads `?`: two blank names in one table say nothing about which row is the unattributed group and which is the total, which is exactly how the table was misread (card `#248`). Position is not a label.

## Code coverage

**Every run measures coverage**, and the figure lands as the `COVERAGE` column of `SUMMARY PER SUITE:` and `SUMMARY PER MODULE:`, immediately after `TIMER`. There is no flag: `-coverage` was a mode until 2026-08-11 (card `#291`), it replaced the run report with a `CODE COVERAGE:` / `NO CODE COVERAGE:` pair and a roll-up of its own, and Jan folded the figure into the run's own tables.

```bash
adtai ut
```

```text
SUMMARY PER SUITE:
------------------

  SUITE PACKAGE            PASS   FAIL   ERROR   TIMER   COVERAGE
  ----------------------   ----   ----   -----   -----   --------
  ICT_ADM_NUMBERING_UT       12                    2.1       41.9
  ICT_COM_INVOICE_UT         20                    3.4       88.0
  ICT_FRG_FREIGHT_UT          4                    0.9        1.0
  ICT_NTF_PREF_UT             8                    1.2       67.4
  ICT_VPD_POLICY_UT           6                    1.7


SUMMARY PER MODULE:
-------------------

  MODULE NAME   PACKAGES   PASS   FAIL   ERROR   TIMER   COVERAGE
  -----------   --------   ----   ----   -----   -----   --------
  ADM                  1     12               3     2.1       41.9
  COM                  1     20                     3.4       88.0
  FRG                  1      4                     0.9        1.0
  NTF                  1      8                     1.2       67.4
  VPD                  1      6                     1.7        0.0
                       5     50               3     9.3       36.6
```

Those blocks are rendered through the real renderer from the per-package figures a 2026-08-06 run against `ICT_OWNER@ORCLPDB1` recorded, 26/62, 22/25, 1/100, 31/46, regrouped for the current shape. They are a shape, not a measurement of this format.

**`COVERAGE` is a property of the package a suite tests, not of the suite.** The pairing is `ut_match`'s capture group, resolved by Oracle at discovery: `ICT_COM_INVOICE_UT` puts its figure on `ICT_COM_INVOICE`'s block coverage. Two suites testing one package print the same figure, because block coverage records which blocks ran and never which test ran them, attributing execution back to a test is not something any data source records.

**A derived name that is not a package falls back to the longest one it prefixes.** A regular expression cannot know which of a schema's names exist, so `ut_match` answers with a name and the schema's own package list decides whether it means anything. `ICT_INT_ARIBA_PUSHBACK_UT` derives `ICT_INT_ARIBA_PUSHBACK`, no such package, and its 13 tests exercise `ICT_INT_ARIBA`; the walk drops one trailing token at a time until a package answers, so that suite reports `ICT_INT_ARIBA`'s figure. It is longest-first and stops on the first hit, so a suite whose derived name **is** a package keeps it and nothing that already paired can be re-pointed.

**A suite that resolves to nothing reads `?`, and that is not a blank.** `ICT_INT_FUSION_ARIBA_UT` walks `ICT_INT_FUSION_ARIBA`, `ICT_INT_FUSION` and `ICT_INT` and finds none of them; attaching it to a near name would put a figure another suite earned beside a suite that did not earn it. The three states are kept apart:

| Cell | What happened |
| ---- | ------------- |
| `88.0` | Oracle instrumented the package and 88% of its blocks ran. |
| `0.0` | Oracle instrumented the package and nothing entered it. That is a real finding, the suite ran and reached none of its target. |
| blank | The package was listed and nothing was measured, `PLSQL_CODE_TYPE = NATIVE` strips instrumentation, so Oracle writes no `dbmspcc_blocks` row however hard the tests hammer it. |
| `?` | The suite pairs to no package at all, so the run never got as far as having something to measure. |

`?` is the same marker, and the same argument, as the `MODULE NAME` column's: a column of short values has no room for a word, and it cannot be mistaken for a figure the way `0` or `-` can.

Those two were one cell until card `#436`. Jan, 2026-08-20: *"we still have packages like ICT_INT_ARIBA_PUSHBACK_UT which has multiple tests, yet no code coverage!"* Six of the eight `ICT_INT%` suites printed a blank beside 71 green tests, and both halves of this section are why. A `NATIVE` marker named the blank case until card `#291`; it lived in the removed report's own column and there is no cell left to carry it.

**Coverage is run-scoped, and that is a deliberate trade.** The report is built from the pairings of the suites that ran, so a package no suite tests appears nowhere, no row, no contribution to any module figure, no total. The removed `NO CODE COVERAGE:` table existed precisely to list those packages, and it went with the flag: Jan's call on 2026-08-11 is that the merged column describes what the run reached. **`ut` no longer answers "what in this schema is untested."** It answers "how much of what these suites test did they reach", which is the question the rest of the table is about.

### The module figure covers the whole group, not its measured part

**A `SUMMARY PER MODULE:` row is not the average of the rows above it.** It is the group's own figure: covered blocks over measured blocks across the group's target packages, scaled by the share of the group's body lines Oracle measured at all. A group every target of which was measured has a share of 1 and is unchanged, so the scaling can only move a figure that was over-claiming.

The scaling exists because unreached code is invisible to Oracle. A package a suite is supposed to test and never enters produces no `dbmspcc` rows, so it has no denominator to contribute, and pooling the measured blocks alone described the reached part of a group as if it were the group. Jan's run on 2026-08-09 read `COM 3 1639 21 88.0` where `ICT_COM_INVOICE` is 224 well-tested lines and the group's other 1415 had never executed (card `#250`). `LINES`, the `PACKAGE BODY` row count from `ALL_SOURCE`, never the spec and never the test partner's, is the only size every package has, so it is what the scaling uses.

**A group with nothing measured reads `0.0`, not a blank.** Unreached code is 0% covered, and that is the answer the column is scanned for; an empty cell reads as "no data" and files the group under nothing-to-see. Only a group holding no package body at all has nothing to print.

The group rows and the unnamed total go through one helper, so a group and the total beneath it can never be two calculations that drift apart.

### Which coverage source, and why

Oracle has two, and they do not measure the same thing. `DBMS_PROFILER` is **line**-level, "was this source line executed". `DBMS_PLSQL_CODE_COVERAGE` is **basic-block**-level, "was this single-entry single-exit block executed". One line holds several blocks and one block spans several lines, so the two row sets cannot agree, and a report that compares a profiler line count to a block count is comparing different units of measure.

utPLSQL already runs **both**, in parallel, on 12.2 and above, and pairs them in `ut_coverage_runs (coverage_run_id, line_coverage_id, block_coverage_id)`. `ut` therefore does not start its own collection or reconcile the two by hand, it calls `ut_runner.coverage_start` / `coverage_stop` and reads the result through that mapping. The percentage reported is the **block** figure, which is the finer of the two.

One deliberate disagreement with utPLSQL's own HTML percentage: blocks marked through the `COVERAGE` pragma (`NOT_FEASIBLE`, `NOT_FEASIBLE_START`, `NOT_FEASIBLE_END`) are **excluded from the denominator**. Oracle flags such a block in `dbmspcc_blocks` rather than dropping it, so subtracting it is the reporter's job; utPLSQL has an open defect that leaves pragma-excluded lines in its metrics. A figure that honours the pragma reads higher than utPLSQL's, and that is intended, the pragma exists so an author can say "this block is not coverable".

**`PLSQL_OPTIMIZE_LEVEL` is not a prerequisite.** Level 2 is Oracle's default and block coverage is collected there regardless, the optimizer reshapes the *line* map `DBMS_PROFILER` reads, not the basic-block map this figure is built on. `ut` flagged anything above level 1 until 2026-08-06, which made every package of every default database read `OPTIMIZE>1` and printed no percentage at all; measured on a schema whose 78 package bodies are all at level 2, 36 of them carried real block coverage.

**Collection costs a session, and every run now pays it.** `-coverage` was opt-in because instrumenting every unit a run touches is not free; the saving that buys it back is the schema-wide package listing, which run-scoping removed along with a per-package compile-settings query.

## What moved since last time

Every run used to be a console snapshot. Nothing recorded what the figure was last time, so a drop was invisible unless somebody remembered the number, which is what stopped the report from being a ratchet.

Every run now records what it measured, and `-verbose` prints the difference above the summaries:

```text
COVERAGE CHANGED SINCE LAST RUN:
--------------------------------

  SUITE PACKAGE    WAS    NOW   DELTA
  -------------   ----   ----   -----
  ICT_WFL_WORKFLOW_UT            22.5   38.2   +15.7
  ICT_COM_INVOICE_UT             61.0   58.9    -2.1
```

**Only the suites that moved.** Two full summaries already list everything, so a third table repeating them would be the second and worse telling of something already told. What this adds is what changed.

**The comparison is against the last run that was different, not the last run.** Running `ut` changes no coverage, so two runs of unchanged code measure the same thing, and comparing each run against the one immediately before it made the table empty on precisely the run a reader opens it for: the one after a deploy, when they re-run the suite to look. Card `#436` measured it on a real store, where **18 of 20 retained runs** were identical to their predecessor, and Jan's report was *"this table is always empty, even after you added some tests."* The walk stops on its first candidate, so a run that follows a real move costs nothing extra.

**Ordered by the size of the move, largest first**, drops and gains together, so a regression cannot hide under a long tail of rounding.

Five rules, and each is the same rule the `COVERAGE` column already follows:

- **`-verbose` only.** The history is recorded on every run, including `-silent` ones: a quiet run still moves the figure, and a store that only remembered verbose runs would compare against whenever somebody last passed the flag rather than against last time.
- **`-silent` outranks it**, like every other section this flag pair governs.
- **A first run prints nothing.** There is no comparison to draw, and a table of nothing above two full summaries would be chrome.
- **A run whose whole history agrees with it prints the header and no rows.** That is the ratchet holding, and it is a different report from having nothing to compare against, which prints no table at all.
- **`WAS` and `DELTA` blank together** for a package the baseline run did not measure. A package appearing for the first time has no comparison rather than a gain of its whole figure, exactly as an unmeasured package blanks rather than reading `0.0`.

**A `-name` run keeps its own history.** It measures the suites it selected and nothing else, so standing it in front of a full run would report every package the filter excluded as having no previous figure — the same store held four single-package runs immediately before a 42-package one. The history is keyed by the selection, the same key `ut_timers.yaml` stores its seconds under, so `-name ICT_INT%` compares against the last `-name ICT_INT%` and an unfiltered run compares against the last unfiltered one.

The history lives in `config/internal/ut.db`, beside `apex.db` and `dependencies.db`, and is gitignored with them. It keeps **the last 20 runs per schema** and prunes the rest on every write, so the file cannot grow unbounded; a schema is keyed upper-case, so `-schema ict_owner` and `-schema ICT_OWNER` read one history rather than two. A root ADT.ai cannot write still runs, reports and exits normally — only the history is skipped.

**Runs recorded before `#436` are not compared against.** They carry no record of what they selected, so nothing stored can say whether one was a full run or a `-name` run: reading them all as full runs puts a single-package run straight back into the baseline position, and reading them all as filtered discards real history. The first run after upgrading therefore reports no comparison and the second compares normally, which costs one round and cannot report a wrong one.

## The coverage gate

`-gate` turns the `COVERAGE` column into a pass/fail condition. It takes an optional value, and the three states are distinct:

```bash
adtai ut -gate 90       # this run's threshold is 90
adtai ut -gate          # the threshold is config ut_coverage_gate (ships at 80)
adtai ut                # nothing gates; the run behaves exactly as it did before the flag
```

**The report prints in full first, and the gate closes it.** A gate that replaced the numbers with a verdict would be unusable, the reason a package is under the bar is in the tables above it, so `SUMMARY PER SUITE:` and `SUMMARY PER MODULE:` are untouched and a failing run adds one section:

```text
COVERAGE BELOW 80.0:
--------------------

  PACKAGE      COVERAGE
  ----------   --------
  ICT_SRC_PO        0.0
  ICT_SEC_AUTHZ    62.5
```

**One package under the bar fails the whole run**, non-zero exit, error chime, and it fails a run whose tests all passed. Worst first, then A-Z, because the list is a work queue.

**`PACKAGE` names the code, not the suite.** The figure is a property of the package: two suites testing one package are two `SUMMARY PER SUITE:` rows and one row here, and the package is what has to be covered.

**Only a package with a measured figure is compared.** A blank `COVERAGE` cell has nothing to compare, `ut_match` paired the suite to nothing, or the target carries no instrumentation, and gating a blank would fail every real schema permanently from the first run. **A `0.0` does gate**: that is a measurement, of a package Oracle instrumented and nothing entered.

**At the boundary, `>=` passes.** `-gate 80` asks for eighty percent, not more than eighty, so a package measured at exactly `80.0` has met it.

**There are no per-package thresholds.** `-name` already narrows a run, so `adtai ut -name CORE% -gate 90` sets a stricter bar for one group without a second configuration surface.

## Reporting

`ut` runs each suite through utPLSQL's **JUnit reporter** and parses the XML, rather than pattern-matching the documentation reporter's summary line. The reason is the same one that makes the exit code interesting: the counters are the only in-database signal that a run failed, and reading them out of prose makes the parser, not the tests, the weakest part of the gate. From XML, a test's name, class, duration, and failure message are markup, and a document that cannot be parsed is an obvious error instead of a silent zero.

## Requirements

- **utPLSQL v3 installed**, and the connected schema holding `EXECUTE` on `ut` and `ut_runner` plus the `ut_*` types, with the matching synonyms. This is utPLSQL's standard `ut_user` grant set.
- Test packages compiled into the schema being tested, or into the `ut_owner` schema when they live in one of their own. `ut` runs suites; it does not install them.
- With `ut_owner` set, the connected schema also needs `SELECT` on that schema's dictionary rows and `EXECUTE` on its packages.
- The command uses the ordinary query path, never the read-only one: running a test writes to utPLSQL's own output buffer, and a `SET TRANSACTION READ ONLY` session makes the reporter's data producer fail to start (`ORA-20215`) rather than reporting anything.

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | the configured default environment | Connection environment to test against. |
| `-name`, `--name` | Yes | everything | Name pattern(s), comma- or space-separated, with `%` and `_` LIKE wildcards. Selects the suites to run, and names itself in the `RUNNING TESTS FOR <PATTERNS>:` header. LIKE wildcards, not regex, `ut_pattern` is what uses regular expressions. |
| `-schema`, `--schema` | Yes | every configured default schema | Schema(s) to test; repeatable, comma- or space-separated, `%` patterns expanded against the configured schemas. Each schema runs as its own console segment with its own timer. |
| `-refresh`, `--refresh` | No | off | Rebuild utPLSQL's annotation cache for the schema before discovery, so a suite compiled since the last run is found. |
| `-gate [N]`, `--gate [N]` | No | off | Fail the run when a tested package's `COVERAGE` is below a threshold. With a number that number is the threshold; bare it comes from `ut_coverage_gate`; absent nothing gates. Only measured figures are compared, and a package exactly on the threshold passes. |
| `-silent`, `--silent` | No | off | Suppress whatever the mode prints between the connection block and `SUMMARY PER SUITE:`, the `RUNNING TESTS:` bar, or `UNIT TESTS SUITES:` and `TEST RESULTS:` under `-verbose`; keep the banner, connection block, `ERRORS & FAILURES:` when a run has any, `SUMMARY PER SUITE:`, and the timer. Outranks `-verbose`. |
| `-verbose`, `--verbose` | No | off | Print `UNIT TESTS SUITES:`, the matched suites and their test counts, then `TEST RESULTS:`, a row per test under its package heading, instead of the `RUNNING TESTS:` progress bar. The heading is streamed before the suite runs and its rows land once the verdict is known. Every section below is unchanged. Ignored under `-silent`, which removes both outright. |
| `-debug`, `--debug` | No | off | Show input parameters and every SQL statement with its bind values, and keep Python tracebacks for troubleshooting. |
| `-key`, `--key` | No | none | Encryption key, or path to a key file, for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

## Notes

- One `ut.run` call per suite, not one per test: the fixtures run once each, and the per-suite call is what lets the progress row stream.
- A **skipped** test (`%disabled`) neither passes nor fails the run. Its row reads `SKIP`, and it lands in no `SUMMARY PER SUITE:` verdict column, so a suite quietly disabled wholesale shows up as a package with test rows and no counts rather than as green.
- Installing test packages is out of scope. `ut` reads the schema and runs what is there; deploying a suite is the project's own deployment path.
- The naming configuration is per-project, not per-run: there is no flag for `ut_pattern`, `ut_match`, `ut_owner` or `ut_module`. A convention is a property of the codebase, and one that could be overridden per run would make two runs of the same schema disagree about which packages are tests.
- The expressions must be valid **Oracle** regular expressions, since Oracle is what runs them. That is the dialect every Oracle developer already knows, and it is what lets the selection be a predicate in the dictionary query rather than a filter over a fetched schema, on a schema with thousands of packages and a handful of suites, that difference is the round trip.

---

← [docs/README.md](README.md) index
