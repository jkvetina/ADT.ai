# Git Activity Calendar (adtai calendar)

![A month of commits. One glance.](images/calendar.png)

`calendar` shows who committed what, when, as a month grid of tickets and commit counts. Reach for it when you need to account for a month of work, your own or a colleague's, without scrolling a git log. It reads the commit store `rebuild` maintains rather than walking every branch live, and it never connects to Oracle.

## Examples

Show the current month for yourself:

```bash
adtai calendar
```

Show a named month, or an older one by offset:

```bash
adtai calendar -month 2026-06
adtai calendar -calendar 1
```

Look at somebody else, or narrow to one branch:

```bash
adtai calendar -by bob@example.com
adtai calendar -branch feat/PROJ-300-currency
```

## Output

Weeks are rows and Monday to Friday are columns. Each active day stacks one `<ticket> (<count>)` line per ticket, so the grid says how many commits went to which ticket on which day:

```text
APEX DEPLOYMENT TOOL - CALENDAR
-------------------------------

MONTHLY OVERVIEW 2026-08 (PROJ):
--------------------------------
  dev@example.com                                   9

9 COMMITS BY dev@example.com (4 tickets, 0 PRs):
------------------------------------------------
2026-08-03         | 2026-08-04         | 2026-08-05         | 2026-08-06         | 2026-08-07        

2026-08-10         | 2026-08-11         | 2026-08-12         | 2026-08-13         | 2026-08-14        
                   |                    | PROJ-101 (2)       | PROJ-118 (1)       | PROJ-118 (1)      

2026-08-17         | 2026-08-18         | 2026-08-19         | 2026-08-20         | 2026-08-21        
PROJ-204 (1)       |                    | PROJ-204 (1)       | PROJ-300 (1)       | PROJ-300 (2)      
```

- `MONTHLY OVERVIEW` names the month and the active `jira_prefix`, then one row per author with their commit total.
- The per-author header counts distinct tickets and pull requests, never commits per row: commits are aggregated per ticket and per day.
- **Saturday and Sunday fold into the preceding Friday.** The last Friday cell above carries two commits for that reason, its own and the weekend's.
- Every week that touches the month prints, active or not, so the grid keeps the shape of a wall calendar. Only a week lying entirely outside the month is dropped.
- A cell shows the ticket id, a `PR#<n>` marker for a pull request, or a branch-derived label when neither is available.

## Topping up the store

Before it reads anything, `calendar` tops the commit store up for exactly the branches it needs: the default branch, plus every branch whose name carries the configured `jira_prefix`. With no prefix set, that is every branch.

The top-up runs `rebuild` in update mode, so a steady-state run reads only the handful of commits since last time. The store lives where `repo_commits_file` points in `config/config.yaml`, one file per branch, and it is the same store [`rebuild`](rebuild.md) and [`search_repo`](search_repo.md) use.

## Scoping to one project

Set `jira_prefix` in `config/config.yaml` (for example `jira_prefix: 'PROJ'`) to keep the report on one project. With a prefix configured, a commit counts when any one of these holds:

- Its message carries a matching ticket (`PROJ-300`, case-insensitive, the dash optional).
- It sits on a branch whose **name** carries the prefix. The whole branch counts, including commits whose message names no ticket.
- It is a pull request. Those are surfaced whatever the prefix says.

Leave `jira_prefix` empty to count every commit you authored across all branches, in which case every branch is cached rather than only the prefixed ones.

## Choosing the author

The default author is your own `git config user.email`, and activity is sourced from the stored commits you wrote. `-by` replaces that with an email substring, and it is repeatable, so several people can share one grid.

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-branch`, `--branch` | No | all branches | Restrict the report to a single branch instead of every branch. |
| `-month`, `--month` | No | current month | Month to show, in `YYYY-MM` format. |
| `-calendar [OFFSET]`, `--calendar [OFFSET]` | No | `0` | Month selector by offset. Bare `-calendar` is the current month; an integer is that many months back. |
| `-by`, `--by` | Yes | your `git config user.email` | Author email substring, repeatable. Replaces the default "my commits" author. |

Shared options (-root, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
