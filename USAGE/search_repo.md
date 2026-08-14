# Search Repository History (adtai search_repo)

`search_repo` searches the `adtai rebuild` commit cache for ADT-style database/APEX project files. It does not connect to Oracle, and normal search/filtering does not scan live Git history; run `adtai rebuild` first so `config/commits/<branch>.yaml` exists. Restore mode still uses Git only to read the selected historical file payloads.

## Examples

List matching commits:

```bash
adtai search_repo
adtai search_repo -limit 50
```

Search commit summaries and changed file paths. Terms are case-insensitive and AND-matched within each flag:

```bash
adtai search_repo -summary order search
adtai search_repo -file packages order_api
adtai search_repo -file workflow
adtai search_repo -file workflow -files 50
adtai search_repo -file workflow -files 0
```

Filter by ADT-style database object metadata derived from paths such as `<schema>/database/<object_type>/<object>.sql` (the default layout; the legacy `database/<schema>/<object_type>/<object>.sql` layout is also recognised):

```bash
adtai search_repo -type PACKAGE -name ORDER_API
```

Filter by author, branch, commit refs, hash prefixes, and dates. Different flags compose with AND semantics; for example `-commit 42 -hash abc1234` only matches commit 42 when its hash starts with `abc1234`.

```bash
adtai search_repo -by bob@example.com
adtai search_repo -branch feature/search -commit 120+
adtai search_repo -commit 12-40
adtai search_repo -hash abc1234 def5678
adtai search_repo -since 2026-06-01 -until 2026-06-10
adtai search_repo -since 7 -until 1
adtai search_repo -recent 7
```

Restore each matching historical version next to the original file, with the commit number inserted before the extension:

```bash
adtai search_repo -file order_v -commit 42 45 -restore
```

That writes files like `database/app/views/order_v.42.sql`. Use `-stage` to restore the matched version to the original path and stage it with `git add`:

```bash
adtai search_repo -file order_v -commit 42 -restore -stage
```

When `-stage` matches more than one version for the same file, the newest matching record wins in the working tree. Use a specific `-commit` or `-hash` when staging a restore.

## Arguments

| Argument | Repeatable | Default | Notes |
| -------- | ---------- | ------- | ----- |
| `-root`, `--root` | No | `.` | Git repository root to search. |
| `-branch`, `--branch` | No | current branch | Branch cache file to search under `config/commits/<branch>.yaml`. |
| `-limit`, `--limit` | No | `20` | Max commits to print, newest first. `0` prints all matching commits. |
| `-files [N]`, `--files [N]` | No | auto with file selectors | Print changed-file rows. `-file`, `-type`, or `-name` prints the first 20 matching files per commit automatically; bare `-files` also prints the first 20; `-files 50` prints the first 50; `-files 0` prints none. Rows use `D`, `A`, or `M` as delete/add/modify markers. |
| `-summary`, `--summary` | No | none | Commit-summary terms; all provided words must match. |
| `-file`, `--file` | No | none | Changed-file path terms; all provided words must match. |
| `-type`, `--type` | Yes | none | Object type text derived from `<schema>/database/<object_type>/...` (or the legacy `database/<schema>/<object_type>/...`). Accepts multiple arguments: `-type PACKAGE VIEW`, `-type PACKAGE,VIEW`, and a repeated `-type PACKAGE -type VIEW` are equivalent. |
| `-name`, `--name` | Yes | none | Object name text derived from the filename stem. Accepts multiple arguments, like `-type`. |
| `-by`, `--by` | Yes | none | Author email/name substring; repeatable. |
| `-my`, `--my` | No | off | Keep commits whose author email equals `git config user.email`. |
| `-commit`, `-commits`, `--commit`, `--commits` | Yes | none | Commit number/hash refs. `N` is that commit, `N+` is that commit and everything newer, `N-M` is the inclusive span. A range needs digits on both sides, so a hash prefix is never read as one. Multiple refs inside this flag are OR-matched. |
| `-hash`, `--hash` | Yes | none | Commit hash prefixes. Multiple hashes are OR-matched. If combined with `-commit`, both filters must match. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | none | Keep commits newer than today minus DAYS. Bare `-recent` means 1 day. |
| `-since`, `--since` | No | none | Oldest commit date, `YYYY-MM-DD`, or number of days back. |
| `-until`, `--until` | No | none | Newest commit date, `YYYY-MM-DD`, or number of days back. |
| `-restore`, `--restore` | No | off | Write matched historical file versions. |
| `-stage`, `--stage` | No | off | With `-restore`, write to original paths and `git add` them. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
