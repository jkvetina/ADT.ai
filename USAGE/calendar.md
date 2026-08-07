# Calendar Activity Report (adtai calendar)

`calendar` shows your Git activity as a monthly author/ticket **calendar grid**. It reads commit metadata from the shared `adtai rebuild` commit cache instead of re-walking every branch live, so it is fast and gets faster the more often you run it. It does not connect to Oracle, so no rebuild is needed first.

Before reading, it tops the cache up for exactly the branches it needs — the default branch plus every branch whose name carries the configured `jira_prefix` (when no prefix is set, every branch). The top-up runs `rebuild` in update mode, so steady-state runs only read the handful of new commits since last time. The cache lives at the path `repo_commits_file` points to in `config/config.yaml` (default `./config/commits/#BRANCH#.yaml`, one file per branch).

By default the author is your own `git config user.email`; activity is sourced from the cached commits you authored. Pass `-by` to look at someone else instead.

Show the current month for yourself:

```bash
adtai calendar
```

Show a specific month or an older month by offset:

```bash
adtai calendar -month 2026-06
adtai calendar -calendar 1
```

Look at another author, or restrict to a single branch:

```bash
adtai calendar -by bob@example.com
adtai calendar -branch feat/PROJ-4995-rework
```

The output is a **month calendar grid**: weeks are rows, Monday–Friday are columns, and each active day's cell stacks one `<ticket> (<count>)` line per ticket — the ticket id and the number of commits attributed to it on that day. Commits are aggregated per ticket, never listed one row per commit:

```text
APEX DEPLOYMENT TOOL - CALENDAR
------------------------------

MONTHLY OVERVIEW: 2026-06 (PROJ)
----------------------------------
  me@example.com                                    5


5 COMMITS BY me@example.com (3 tickets, 0 PRs)
----------------------------------------------
2026-06-01         | 2026-06-02         | 2026-06-03         | 2026-06-04         | 2026-06-05
PROJ-100 (2)     |                    |                    | PROJ-204 (1)     | PROJ-300 (2)
```

Saturday and Sunday commits are folded into the preceding Friday's bucket, so `2026-06-05` above carries both the Friday and weekend commits for `PROJ-300`. Weeks with no activity in the month are skipped.

`-list` is accepted for backwards compatibility but no longer changes the output — the task-centric report shown above is now the only format:

```bash
adtai calendar -month 2026-06 -list
```

## Jira prefix scoping

Set `jira_prefix` in `config/config.yaml` (e.g. `jira_prefix: 'PROJ'`) to scope activity to one project. With a prefix configured, a commit counts when **any** of these hold:

- its message carries a matching ticket (`PROJ-4995`, case-insensitive, dash optional);
- it lives on a branch whose **name** carries the prefix (the whole branch counts — every commit on it, even ones whose message has no ticket);
- it is a **pull request** — PRs always get special attention and are surfaced regardless of prefix.

Each grid cell shows the ticket id, a `PR#<n>` marker for pull requests, or a branch-derived label as a fallback, followed by `(<count>)` — the number of commits attributed to that ticket on that day. The per-author header reports the distinct ticket and PR counts, and the overview header shows the active prefix.

Leave `jira_prefix` empty to count every commit you authored across all branches (in which case every branch is cached, not just the prefixed ones).

## Arguments

| Argument | Required | Default | Notes |
| -------- | -------- | ------- | ----- |
| `-root`, `--root` | No | `.` | Git repository root to read. |
| `-branch`, `--branch` | No | all branches | Restrict the report to a single branch instead of every branch. |
| `-month`, `--month` | No | current month | Month to show, in `YYYY-MM` format. |
| `-calendar [OFFSET]`, `--calendar [OFFSET]` | No | `0` | Old ADT-style month selector. Bare `-calendar` shows the current month; an integer value shows that many months back. |
| `-by`, `--by` | No | your `git config user.email` | Author email/name substring; repeatable. Overrides the default "my commits" author. |
| `-list`, `--list` | No | off | Accepted for backwards compatibility; no longer changes the output (the task-centric report is now the only format). |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
