# Rebuild Commit Cache (adtai rebuild)

`rebuild` maintains the local Git commit cache that the history-reading commands consume instead of scanning Git live — one YAML file per branch of commit metadata. It also lists and switches branches (`-reveal`, `-switch`). Run it when the cache should catch up with new commits; scans are incremental by default.

Update the commit cache for the current branch. This is incremental by default:

```bash
adtai rebuild
```

Limit the scan to selected branches and commit count:

```bash
adtai rebuild -branch main -branch feature/foo -limit 200
```

In normal rebuild mode `-limit N` is the per-branch commit cap (this is the flag formerly named `-commits`). The same `-limit` flag means something different in `-reveal` mode — there it caps the number of branch rows listed. See "Inspecting branches (`-reveal`)" below. To bound the window by time instead of count, use `-since` (a date or a number of days back) — see "Bounded window since a date (`-since`)" below.

The command refreshes the multi-branch Git commit cache used by repository search. It writes one cache file per branch using `repo_commits_file` (default `./config/commits/#BRANCH#.yaml`), skips gitlinks safely, and scans the current branch (or the branches named with `-branch`).

Each branch passed to `-branch` is validated before the scan: a name git cannot resolve (a typo such as `-branch mai`) fails fast with `Error: branch 'mai' not found in this repo — run 'adtai rebuild -reveal' to list available branches` and exit 1, rather than a raw git exit-128 dump. Any commit-ish git accepts is allowed — a local branch, `origin/<name>`, a tag, or a SHA.

## Incremental update (default)

By default, `rebuild` reads the existing per-branch cache, takes its newest (highest-numbered) commit as the resume point, and only fetches and hashes commits added after it (`git log <last>..<branch>`). The previously cached records are reused verbatim — never re-hashed — and the new commits append with continuing absolute numbers, so a large branch updates in seconds instead of re-scanning its whole history.

When update mode resumes from a usable cache, the header `COMMITS |` line reads `COMMITS | <total> + <new>`, where `<total>` is the branch's full commit count and `<new>` is the count of commits missing from the cache (e.g. `COMMITS | 84467 + 3`). When a branch has no usable cache and is rebuilt in full, the line uses the plain full-rebuild shape (`COMMITS | <total>`).

It is a per-branch fallback: a branch whose cache file is missing or empty, or whose cached tip no longer exists on the branch (history was rebased or force-pushed), is rebuilt in full. Passing `-limit N` switches that branch to a full bounded window instead of the incremental update. When a branch has no new commits its cache is left as-is.

## Bounded window since a date (-since)

`-since WHEN` rebuilds a full bounded window whose size is "every commit since a date" rather than a fixed commit count. It is the date-based sibling of `-limit`: same full bounded rebuild (never the incremental update), just bounded by time instead of count.

`WHEN` is one of:

- A `YYYY-MM-DD` date — e.g. `adtai rebuild -since 2026-05-01`.
- An integer number of days back — e.g. `adtai rebuild -since 7` rebuilds the last 7 days (resolved to today − 7, then treated as a date).

```bash
adtai rebuild -since 2026-05-01
adtai rebuild -since 7
```

Both forms resolve to an ISO date and bound the window with `git log --since=<date> 00:00:00` (committer date; a bare date is local midnight, so commits made on that day are included — the "first commit on this date"). The header changes from `COMMITS | <total>` to `COMMITS | <count> SINCE <YYYY-MM-DD>`, where `<count>` is the number of commits in the window — the `COMMITS | <n>` column stays aligned with the other modes and the date trails it.

In normal rebuild mode `-since` cannot be combined with `-limit` (`Error: -since and -limit cannot be combined`) — both bound the same window, one by date and one by count. In `-reveal` mode `-since` means something different and composes freely: it keeps only branches whose tip commit is on or after `WHEN`, and `-limit` then caps the survivors (see "Inspecting branches" below). A value that is neither a date nor an integer errors verbatim. In normal mode, like any bounded window, it rewrites the branch cache to just the windowed commits, with absolute commit numbering preserved (see below).

## Commit numbering

Each commit's number is its **absolute position from the root** — i.e. how many commits exist from the first commit up to and including it (`git rev-list --count <commit>`). On a branch with 84,464 commits the newest is `84464`, the one before it `84463`, and so on back to `1` at the root.

`-limit N` only limits how many of the newest commits are scanned; it does **not** renumber them. `adtai rebuild -limit 1000` on that branch caches commits `83465`–`84464`, not `1`–`1000`. The oldest in the window is `total - N + 1`.

Because numbers are derived from git on every rebuild, never from a stored counter:

- Adding a commit gives it the next number (`84465`); every existing commit keeps its number.
- Commit, then undo (reset/drop it), then commit again — the number is reclaimed, leaving no gap. (Old ADT consumed the id on undo and skipped it.)

Stability holds for the normal append-at-tip / undo-at-tip workflow. Rewriting history *below* a commit (rebase, amend an older commit, insert a commit mid-history, or merge in a branch whose commits sort earlier) changes that commit's ancestor count and shifts its number. Linear feature-branch work is stable.

## Inspecting branches (-reveal)

`rebuild -reveal` lists branches without touching the commit cache — a read-only inspector for finding the branch you want before a real rebuild. It reads the branches on the remote (`refs/remotes/origin/*`), not your local heads, so the list is correct no matter which branch you have checked out — running it from a feature branch will not surface stale local branches. A best-effort `git fetch --prune origin` runs first to refresh the remote-tracking refs and drop deleted branches (an offline/failed fetch is non-fatal and falls back to the cached refs). The output is a single `RECENT BRANCHES` list sorted newest-first by committer date, showing only the branch name clipped to 78 characters and two-space-indented under the header — there is no `BRANCH` column header or dashed rule (same for `-my` and word-filtered lists):

```bash
adtai rebuild -reveal
```

The result count is folded into the header. When the list is truncated it reads `RECENT BRANCHES: (20/1958)` (shown / total); when the whole list fits it reads `(1958)`.

Pass one or more filter words to narrow the list. Words are AND-matched — every word must appear in the branch name — and the title switches to `BRANCHES MATCHING <words>`. Each word is a case-insensitive "contains" glob (wrapped with `*` on each end you did not anchor), so `feat 4995` keeps branches whose name holds both `feat` and `4995`, and a single `feat*4995` still works:

```bash
adtai rebuild -reveal feat 4995
adtai rebuild -reveal feat*4995
```

Use `-limit N` to change how many rows are shown (default 20); `-limit 0` lists every match:

```bash
adtai rebuild -reveal feat 4995 -limit 5
adtai rebuild -reveal -limit 0
```

Add `-my` to filter to branches whose tip-commit author email equals your `git config user.email`. The plain list retitles to `MY RECENT BRANCHES`; a word-filtered list keeps the `BRANCHES MATCHING <words> (mine)` form:

```bash
adtai rebuild -reveal -my
adtai rebuild -reveal feat 4995 -my
```

Add `-since WHEN` to keep only branches whose tip commit is on or after `WHEN` (a `YYYY-MM-DD` date or an integer number of days back, same forms as normal mode). It is a tip-commit date filter here, not a commit-window rebuild, and composes with `-limit`, `-my`, and word filters — the date filter runs first, then `-limit` caps the survivors. The title gains a ` SINCE <date>` suffix (e.g. `MY RECENT BRANCHES SINCE 2026-05-01`):

```bash
adtai rebuild -reveal -since 2026-05-01
adtai rebuild -reveal -my -since 7
adtai rebuild -reveal feat 4995 -since 2026-05-01 -limit 5
```

Once the list shows the branch you want, add `-switch [N]` to check the working tree out to it. `N` is the 1-based rank in the filtered list (bare `-switch` means `-switch 1`). The branch list is **not** printed in switch mode — instead the report shows the branch you switched to and its recent commits:

```text
APEX DEPLOYMENT TOOL - REBUILD
-----------------------------

BRANCH SWITCHED:
----------------

  feat/PROJ-4995_da_hy_risk_review_install_threshold


COMMITS:
--------

  2026-06-05 14:31 | Wire install-threshold review
  2026-06-05 11:02 | Add HY risk review DA
```

Only the commits **actually made on the branch** are listed — commits it inherited from the default branch at creation are excluded (the list is the `origin/<default>..<branch>` range; the default branch is read from `origin/HEAD`, falling back to `origin/main`/`origin/master`). Switching to the default branch itself lists all of its commits. Commits are listed newest-first by committer date, each line is indented two spaces as `  YYYY-MM-DD HH:MM | <subject>`; every printed line is clipped to 78 characters (the indent counts toward the cap). In switch mode `-limit` no longer caps the branch list (the rank is resolved against the full filtered set) — instead it caps the COMMITS section (default 20, `-limit 0` = all), and `-my` keeps only commits you authored (`git config user.email`):

```bash
adtai rebuild -reveal 4995 -switch            # rank 1, last 20 commits
adtai rebuild -reveal 4995 -switch 2          # rank 2
adtai rebuild -reveal 4995 -switch -limit 5   # rank 1, last 5 commits
adtai rebuild -reveal 4995 -switch -my        # rank 1, only my commits
```

If you are already on the target branch, `-switch` runs **no git operations** — nothing is checked out and any in-flight WiP is left exactly where it is — and still prints the BRANCH SWITCHED / COMMITS view. Otherwise `git checkout` creates a local tracking branch from `origin/<name>` when none exists. A rank outside the filtered range errors without switching, a dirty tree that the checkout would clobber makes git refuse (the error is shown verbatim, your WiP is left untouched), and `-switch` only composes with `-reveal` — using it without `-reveal` errors. Non-conflicting WiP rides along with the checkout.

`-reveal` never seeds or modifies the per-branch commit cache and ignores the normal-mode meaning of `-limit` (in reveal mode `-limit` caps the branch rows, or the COMMITS section under `-switch`; there is no commit scan).

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder containing the Git repository. |
| `-branch`, `--branch` | Yes | current branch | Branch name or names to include. |
| `-reveal`, `--reveal` | No | off | Read-only branch inspector: list the remote branches (`origin/*`, no cache changes) in one `RECENT BRANCHES` table, newest-first, names clipped to 78 chars. Fetches (`--prune`) first. Optional filter `WORD`s are AND-matched against the branch name (`feat 4995` → `BRANCHES MATCHING feat 4995`), each a case-insensitive "contains" glob. |
| `-limit`, `--limit` | No | mode-dependent | Meaning depends on the mode. **Normal mode:** maximum commits to read per branch; runs a full bounded window (this is the flag formerly named `-commits`). Default (no `-limit`) is an incremental update since the last cached commit. **`-reveal` mode:** maximum branch rows to list (default `20`, `0` lists all). **`-reveal -switch` mode:** maximum commits to show for the switched branch (default `20`, `0` = all); the branch rank is resolved against the full filtered list, so `-limit` does not bound it here. |
| `-since`, `--since` | No | off | `WHEN` is a `YYYY-MM-DD` date or an integer number of days back (`7` = 7 days ago). **Normal mode:** rebuild a full bounded window of every commit since `WHEN`; header reads `COMMITS | <count> SINCE <date>`; mutually exclusive with `-limit`. **`-reveal` mode:** keep only branches whose tip commit is on or after `WHEN`; composes with `-limit` (date-filter first, then cap) and adds a ` SINCE <date>` title suffix. |
| `-my`, `--my` | No | off | In `-reveal` mode, limit results to branches whose tip-commit author email equals `git config user.email`. Under `-switch`, it also limits the COMMITS section to commits you authored. |
| `-switch`, `--switch` | No | `1` when given | In `-reveal` mode, check the working tree out to the `N`th branch in the filtered order (1-based; bare `-switch` = `-switch 1`), then print `BRANCH SWITCHED` + that branch's recent `COMMITS` (only commits made on the branch — those inherited from the default branch at creation are excluded; newest-first, every line clipped to 78 chars) instead of the branch list. `-limit` caps the commits; `-my` keeps only yours. Out-of-range rank errors without switching; if already on the target branch it runs no git ops (WiP untouched). Errors if used without `-reveal`. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
