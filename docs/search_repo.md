# Search Repository History (adtai search_repo)

`search_repo` finds the commit that touched a database object, by object name, type, file path, author, or date. Reach for it when you know what changed but not when, and a `git log` over a repository of exported DDL would be too blunt.

It reads the commit store [`rebuild`](rebuild.md) maintains, so run `rebuild` first. It never connects to Oracle.

<br>

## Examples

List the newest commits in the branch store:

```bash
adtai search_repo
adtai search_repo -limit 50
```

Search commit subjects, or the paths a commit touched. Terms inside one flag are AND-matched and case-insensitive:

```bash
adtai search_repo -summary currency
adtai search_repo -file order_api
adtai search_repo -file workflow -files 50
```

Search by database object, resolved from the exported path layout. `-type` and `-name` are SQL LIKE patterns, the same language `export_db` reads, so `%` stands for any run of characters and `_` for a single one:

```bash
adtai search_repo -type VIEW -name MONTHLY_REPORT_V
adtai search_repo -type PACKAGE,VIEW
adtai search_repo -type "PACKAGE%"
adtai search_repo -name "SHOP%"
```

A literal `_` or `%` in a name is escaped with `\`, the same character SQL LIKE always has: `-name 'CORE\_LOCK'` matches only that name, where `-name CORE_LOCK` would also match `COREXLOCK`. Quote the flag, or the shell eats the backslash before ADT.ai ever sees it.

Search by author, branch, commit reference, hash prefix, or date. `-by` is a pattern too, so a partial address needs its own `%`:

```bash
adtai search_repo -by bob@example.com
adtai search_repo -by "bob%"
adtai search_repo -branch feat/PROJ-300-currency -commit 5+
adtai search_repo -hash 565fcf1a
adtai search_repo -since 2026-08-01 -until 2026-08-20
adtai search_repo -recent 7
```

Restore the historical version of a file beside the current one:

```bash
adtai search_repo -file monthly_report_v -commit 7 -restore
```

<br>

## Output

One block per commit, newest first: the store's own commit number and the subject, then the author, the commit timestamp and the short hash. A file selector adds the changed-file rows under each commit:

```text
APEX DEPLOYMENT TOOL - SEARCH_REPO
----------------------------------


COMMITS:
--------
7) PROJ-300: monthly_report_v groups by currency
  dev@example.com | 2026-08-20 09:14 | 565fcf1a
    - SANDBOX/database/views/
      - M | monthly_report_v.sql

2) PROJ-101: widen the report to carry the order total
  dev@example.com | 2026-08-12 09:14 | 19915eed
    - SANDBOX/database/views/
      - M | monthly_report_v.sql

1) PROJ-101: add the monthly report view
  dev@example.com | 2026-08-12 09:14 | f8b645c0
    - SANDBOX/database/views/
      - A | monthly_report_v.sql


TIMER: 0s
```

- The leading number is the commit's position in the branch store, which is what `-commit` takes. It is stable for a given store and unrelated to the hash.
- `A`, `M` and `D` are git's own status letters, stored per commit at rebuild time. Timestamps read `YYYY-MM-DD HH:MI`, never ISO with a `T`.
- The files group under their folder, one row per directory below it and two spaces further in each time, the same shape every file list on `adtai` prints. `nested_files: False` in `config.yaml` gives the flat `    - M | <path>` rows instead; the rule is on [config.md](config.md).
- `-file`, `-type` and `-name` turn the file rows on by themselves, capped at 20 per commit. `-files N` sets another cap and `-files 0` turns them off.
- A search that matches nothing prints `No commits found.` and exits `0`.

<br>

## How filters combine

Terms inside one flag are AND-matched, and different flags are AND-matched with each other, so `-commit 5+ -hash 565fcf1a` keeps only the commit that satisfies both. The exceptions are `-commit` and `-hash`, whose own multiple values are OR-matched.

`-commit` takes a number, a hash, or a range: `7` is that commit, `5+` is that one and everything newer, `2-6` is the inclusive span. A range needs digits on both sides, so a hash prefix is never misread as one.

<br>

## Finding an object rather than a file

`-type` and `-name` read the object out of the exported path rather than out of the file. The layout comes from your configured `path_objects`, so both the shipped `<schema>/database/<object_type>/` and the older `database/<schema>/<object_type>/` resolve.

- `-type` is spelled the way Oracle spells it: `-type "PACKAGE BODY"`, `-type "MATERIALIZED VIEW"`. Several values may be space-separated, comma-separated, or the flag repeated.
- `-name` is the object name, read through the file's own configured extension, so `packages/core.spec.sql` is `CORE` rather than `CORE.SPEC`.
- Both are matched as SQL LIKE patterns, case-insensitively, the way `export_db -type` and `-name` are matched. The pattern is anchored, so `-type PACKAGE` is the spec alone and `-type "PACKAGE%"` is the spec and the body; a partial name is written `-name "SHOP%"` rather than as a bare fragment.

<br>

## Restoring an old version

`-restore` writes each matching historical version beside the original, with the commit number inserted before the extension, so `-file monthly_report_v -commit 7 -restore` writes `monthly_report_v.7.sql`. Restore is the one mode that reads live git, and only to fetch the payloads of versions already selected from the store.

`-stage` writes to the original path instead and runs `git add` on it. When a restore with `-stage` matches more than one version of one file, the newest match wins in the working tree, so name a specific `-commit` or `-hash` when staging.

<br>

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-branch`, `--branch` | No | current branch | Branch store to search, at the `repo_commits_file` path (default `config/commits/<branch>.db`). |
| `-limit`, `--limit` | No | `20` | Maximum commits to print, newest first. `0` prints all matching commits. |
| `-files [N]`, `--files [N]` | No | auto with file selectors | Print changed-file rows. `-file`, `-type` or `-name` prints the first 20 per commit automatically; bare `-files` also prints 20; `-files 50` prints 50; `-files 0` prints none. Rows carry `A`, `M` or `D`. |
| `-summary`, `--summary` | No | none | Commit-subject terms; every word given must match. |
| `-file`, `--file` | No | none | Changed-file path terms; every word given must match. |
| `-type`, `--type` | Yes | none | Object type, resolved through your `object_types` config against the `path_objects` layout. Oracle's own spelling: `-type "PACKAGE BODY"`. A SQL LIKE pattern, so `-type "PACKAGE%"` takes both halves of the pair. Space-separated, comma-separated and repeated forms are equivalent. |
| `-name`, `--name` | Yes | none | Object name, read through the file's own configured extension, so `packages/core.spec.sql` is `CORE`. A SQL LIKE pattern like `-type`, and takes multiple values the same way. |
| `-by`, `--by` | Yes | none | Author email, as a SQL LIKE pattern: `-by bob@example.com` or `-by "bob%"`. Repeatable. |
| `-my`, `--my` | No | off | Keep commits whose author email equals `git config user.email`. |
| `-commit`, `-commits`, `--commit`, `--commits` | Yes | none | Commit numbers or hashes. `N` is that commit, `N+` is that one and newer, `N-M` is the inclusive span. Several values inside this flag are OR-matched. |
| `-hash`, `--hash` | Yes | none | Commit hash prefixes, OR-matched. Combined with `-commit`, both filters must match. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | none | Keep commits newer than today minus DAYS. DAYS may be a fraction of a day, `1/24` for the past hour. A whole-day window compares dates, so `-recent 1` keeps a commit made at 23:00 yesterday; a shorter one compares the commit's own timestamp. Bare `-recent` means one day. |
| `-since`, `--since` | No | none | Oldest commit date, `YYYY-MM-DD`, or a number of days back. |
| `-until`, `--until` | No | none | Newest commit date, `YYYY-MM-DD`, or a number of days back. |
| `-restore`, `--restore` | No | off | Write the matched historical versions beside the originals. |
| `-stage`, `--stage` | No | off | With `-restore`, write to the original paths and `git add` them. |

Shared options (-root, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
