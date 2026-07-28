from __future__ import annotations

from adt_ai.cli.constants import REVEAL_DEFAULT_LIMIT
from adt_ai.shared.recent_state import BARE_RECENT


def add_history_parsers(subparsers) -> None:
    calendar = subparsers.add_parser(
        "calendar",
        description="show your Git activity across all branches as a calendar",
        help="show your Git activity across all branches as a calendar",
    )
    calendar.add_argument("--root", "-root", default=".", help="project root folder")
    calendar.add_argument(
        "--branch",
        "-branch",
        help="restrict the report to a single branch instead of every branch",
    )
    calendar.add_argument("--month", "-month", help="month to show, YYYY-MM")
    calendar.add_argument(
        "--calendar",
        "-calendar",
        dest  = "calendar_offset",
        nargs = "?",
        const = 0,
        type  = int,
        help  = "show the calendar for current month or OFFSET months back",
    )
    calendar.add_argument(
        "--by",
        "-by",
        action = "append",
        help   = "author email/name text; default is your own git user.email",
    )
    calendar.add_argument("--list", "-list", action="store_true", help="show day rows")
    search_repo = subparsers.add_parser(
        "search_repo",
        description="search cached Git commit history",
        help="search cached Git commit history",
    )
    search_repo.add_argument("--root", "-root", default=".", help="project root folder")
    search_repo.add_argument("--branch", "-branch", help="branch or ref to search")
    search_repo.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = REVEAL_DEFAULT_LIMIT,
        metavar = "N",
        help    = f"max commits to print (default {REVEAL_DEFAULT_LIMIT}; 0 = all)",
    )
    search_repo.add_argument(
        "--files",
        "-files",
        nargs   = "?",
        type    = int,
        const   = 20,
        default = None,
        metavar = "N",
        help    = (
            "print at most N changed files per commit; file selectors auto-print 20 "
            "(bare -files = 20; 0 = none)"
        ),
    )
    search_repo.add_argument(
        "--summary",
        "-summary",
        nargs = "*",
        help  = "summary word(s), AND-matched case-insensitively",
    )
    search_repo.add_argument(
        "--file",
        "-file",
        nargs = "*",
        help  = "file path word(s), AND-matched case-insensitively",
    )
    # Multi-pattern like export_db/recompile: `-type A B`, `-type A,B`, and a repeated
    # `-type A -type B` are equivalent (shared argument semantics).
    search_repo.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type text, supports multiple arguments",
    )
    search_repo.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name text, supports multiple arguments",
    )
    search_repo.add_argument("--by", "-by", action="append", help="author email/name text")
    search_repo.add_argument("--my", "-my", action="store_true", help="show only my commits")
    search_repo.add_argument(
        "--commit",
        "--commits",
        "-commit",
        "-commits",
        dest   = "commit_refs",
        action = "append",
        nargs  = "+",
        help   = "commit number/hash ref(s); N+ selects N and newer",
    )
    search_repo.add_argument(
        "--hash",
        "-hash",
        action = "append",
        nargs  = "+",
        help   = "commit hash prefix(es)",
    )
    search_repo.add_argument(
        "--recent",
        "-recent",
        nargs = "?",
        # Shares export_db/export_apex/dependencies' sentinel so `-recent` keeps
        # ONE parser shape across every module (the shared-argument-semantics
        # contract compares `repr(const)`). search_repo reads git history, which
        # has no export watermark, so it maps the sentinel back to 1 day at the
        # edge — same shape, its own meaning.
        const = BARE_RECENT,
        type  = int,
        help  = "only commits from recent DAYS (bare -recent = 1)",
    )
    search_repo.add_argument("--since", "-since", help="oldest commit date, YYYY-MM-DD")
    search_repo.add_argument("--until", "-until", help="newest commit date, YYYY-MM-DD")
    search_repo.add_argument(
        "--restore",
        "-restore",
        action = "store_true",
        help   = "write matching historical file versions next to the original files",
    )
    search_repo.add_argument(
        "--stage",
        "-stage",
        action = "store_true",
        help   = "with -restore, restore to original paths and git add them",
    )
    rebuild = subparsers.add_parser(
        "rebuild",
        description="rebuild the git commit cache for the current branch",
        help="rebuild the git commit cache",
    )
    rebuild.add_argument("--root", "-root", default=".", help="project root folder")
    rebuild.add_argument(
        "--branch",
        "-branch",
        action = "append",
        nargs  = "+",
        help   = "branch name(s) to include; default is the current branch",
    )
    rebuild.add_argument(
        "--reveal",
        "-reveal",
        nargs   = "*",
        default = None,
        metavar = "WORD",
        help    = "list the remote branches (origin/*) without touching the cache, "
                  "newest first. Bare '-reveal' shows the latest 10. Any words filter "
                  "by name, AND-matched ('-reveal feat 4995' lists branches whose name "
                  "contains both). Use -limit to change the row count",
    )
    rebuild.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = None,
        metavar = "N",
        help    = "meaning depends on the mode. In reveal mode: max branches to "
                  f"list (default {REVEAL_DEFAULT_LIMIT}; 0 = all), or with -switch "
                  "the max commits to show for the switched branch (same default; "
                  "0 = all). In normal rebuild mode: max commits to read per "
                  "branch, running a full bounded window (default: incremental "
                  "update since the last cached commit)",
    )
    rebuild.add_argument(
        "--since",
        "-since",
        metavar = "WHEN",
        help    = "rebuild every commit since WHEN. WHEN is a YYYY-MM-DD date, or "
                  "an integer number of days back (e.g. '7' = 7 days ago, converted "
                  "to a date). In normal mode it bounds the rebuild window and shows "
                  "'COMMITS | <count> SINCE <date>' in the header (mutually exclusive "
                  "with -limit). In reveal mode it keeps only branches whose tip "
                  "commit is on or after WHEN (composes with -limit)",
    )
    rebuild.add_argument(
        "--my",
        "-my",
        dest   = "my",
        action = "store_true",
        help   = "in reveal mode, limit to branches whose tip commit is yours "
                 "(matched against 'git config user.email')",
    )
    rebuild.add_argument(
        "--switch",
        "-switch",
        nargs   = "?",
        type    = int,
        const   = 1,
        default = None,
        metavar = "N",
        help    = "in reveal mode, check the working tree out to the Nth branch "
                  "in the filtered order (1-based; bare '-switch' = 1), then show "
                  "BRANCH SWITCHED and that branch's recent COMMITS instead of the "
                  "branch list. -limit caps the commits, -my keeps only yours. "
                  "Skips all git ops when already on that branch",
    )
