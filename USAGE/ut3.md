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

`-name` matches the **suite package name** with Oracle `LIKE` semantics — `%` for any run of characters, `_` for exactly one, `\` to escape either — the same pattern language `recompile -name` and `export_db -name` use, applied through the one shared implementation so a pattern selects identically here and there.

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
APEX DEPLOYMENT TOOL: UT3
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

  SUITE PACKAGE         TESTS   PASSED   FAILED   ERRORED
  -------------------   -----   ------   ------   -------
  ADT_FIXTURE_UT            4        2        1         1
  ICT_SEC_SECURITY_UT      14       14


TIMER: 2s
```

That listing is a real run against `ICT_OWNER@ORCLPDB1`, trimmed only by dropping 12 of the 14 `ICT_SEC_SECURITY_UT` rows.

**`UNIT TESTS SUITES:` is a per-suite roll-up, and it always prints.** Two columns — the suite package and how many tests it holds — so the section answers "what is about to run" at a glance rather than scrolling a row per test. The header and the column row print even when the list is empty: a run that matched nothing reports it in the same shape as a run that matched ten, and the failure is carried by the exit code, not by a block of troubleshooting advice. **Only runnable suites are listed**, and a package that is not one is listed nowhere else either.

**Order is fixed and not the reporter's.** Packages print A-Z. Tests print in the order the **package specification** declares them — `USER_PROCEDURES.SUBPROGRAM_ID`, not alphabetically and not in the order utPLSQL happened to walk its suite tree — so a results block reads down the same way the source does. The results, the stanzas, and the counts all follow that one order.

**Test rows print the procedure name, never the `%test` description.** A JUnit `testcase name` is not an identifier: utPLSQL puts the description there whenever the annotation carries one, and falls back to the procedure name only for an undescribed test. `ut3` matches the reported value back through `ut_runner.get_suites_info` — which holds both spellings for every discovered test — and prints the **procedure name**, because that is what a reader greps for in the package source.

**`TEST RESULTS:` is printed as the run proceeds, not collected and dumped at the end.** The package name lands on the terminal before `ut.run` is called for that suite, so a slow suite visibly hangs on its own name; its test rows print the moment the verdict is known, and the next package name follows. utPLSQL runs a whole suite in one call, so the individual test rows cannot stream any earlier than that — the suite is the unit of progress.

**`TEST RESULTS:` and `ERRORS & FAILURES:` share one indentation grid.** Both sections have the same shape — a heading naming what follows, then its detail — so a package name sits at two spaces where a stanza heading sits, and a test row at four where a wrapped message line sits. A test row is still fixed-width: the extra indent eats dots, so every verdict stays flush with the right edge the tables align to.

**Free text is a stanza list, not a table column.** A failure message is prose (utPLSQL prints the expectation, the actual value, and the source location), and a shared table column is sized to its widest cell, so one long message widens the whole table past the terminal and destroys the alignment the table existed for. `ERRORS & FAILURES:` therefore carries every non-passing test as a heading with the reason wrapped at 80 columns and indented four beneath it, one blank line above each stanza.

**The status leads the heading** — `ERROR > ADT_FIXTURE_UT.TEST_LABELS#LOOKUP_RAISES`, not the name with the verdict trailing. A reader scanning this section is looking for which ones errored, and a status word parked behind a package-qualified identifier is behind the longest and most variable part of the line. A raised exception reads `ERROR`; a refused expectation reads `FAILED`.

**`SUMMARY:` is the suites table again, with what each suite's tests did.** The same first two columns, so the two sections read as before-and-after of one list, plus `PASSED` / `FAILED` / `ERRORED`. **A zero renders as an empty cell** — a column of `0`s competes for the eye with the counts that matter, and the row a reader scans for is the one that is not all-passed. A `%disabled` test is counted in `TESTS`, appears in none of the three verdict columns, and its own result row reads `SKIPPED`.

**`-silent` drops the two listings and nothing else.** `UNIT TESTS SUITES:` and `TEST RESULTS:` are suppressed; the banner, the connection block, `SUMMARY:`, and the timer stay, and so does `ERRORS & FAILURES:` whenever a run has something to put in it. A green run is then four lines and a table. A red one still says which test failed and why — the flag makes a passing run quiet, not a failing run unreadable, and a `FAILED` count whose message is reachable only by re-running without the flag is not a report. `ERRORS & FAILURES:` prints under `-silent` on exactly the same condition as without it: at least one test failed or errored.

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
| `-name`, `--name` | Yes | every `_UT` suite | Suite package name pattern(s), comma- or space-separated, with `%` and `_` LIKE wildcards. |
| `-schema`, `--schema` | Yes | every configured default schema | Schema(s) to test; repeatable, comma- or space-separated, `%` patterns expanded against the configured schemas. Each schema runs as its own console segment with its own timer. |
| `-refresh`, `--refresh` | No | off | Rebuild utPLSQL's annotation cache for the schema before discovery, so a suite compiled since the last run is found. |
| `-silent`, `--silent` | No | off | Suppress `UNIT TESTS SUITES:` and `TEST RESULTS:`; keep the banner, connection block, `ERRORS & FAILURES:` when a run has any, `SUMMARY:`, and the timer. |
| `-debug`, `--debug` | No | off | Show input parameters and every SQL statement with its bind values, and keep Python tracebacks for troubleshooting. |
| `-key`, `--key` | No | none | Encryption key, or path to a key file, for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

## Notes

- One `ut.run` call per suite, not one per test: the fixtures run once each, and the per-suite call is what lets the progress row stream.
- A **skipped** test (`%disabled`) neither passes nor fails the run. Its row reads `SKIPPED` and `SUMMARY:` counts it, so a suite quietly disabled wholesale is visible rather than reported as green.
- Installing test packages is out of scope. `ut3` reads the schema and runs what is there; deploying a suite is the project's own deployment path.

---

← [USAGE.md](../USAGE.md) index
