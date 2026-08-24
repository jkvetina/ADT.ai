from __future__ import annotations

from adt_ai.cli.constants import REVEAL_DEFAULT_LIMIT
from adt_ai.cli.parser_common import COMMIT_IDENTITY_HELP
from adt_ai.shared.dates import recent_window
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
        # "matched on name or email" until ADT #467, which is a promise the data
        # cannot keep: the shared commit store is written with `%ae`, so the
        # author of a stored commit is an email and a name matches nothing. The
        # comment 160 lines below in `calendar/runner.py` already said so.
        help   = "limit to commits by AUTHOR, matched against the commit author "
                 f"email, repeatable; defaults to you, {COMMIT_IDENTITY_HELP}",
    )
    # `-list` was declared here until ADT #345 withdrew it. It reached
    # `CalendarRequest.list_mode` and was read by nothing: the task-centric
    # report replaced the day-row format outright, so the flag had nothing left
    # to switch. `docs/calendar.md` documented it as accepted but inert, which
    # is the accepted-but-unused compatibility flag SOP §Command surface
    # forbids, not an exemption from it.
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
    # `-type`, `-name` and `-by` are SQL LIKE patterns since ADT #474, matched by
    # `shared/sql_like` the way `export_db` matches its own `-type`/`-name`. They
    # were substring tests, so `%` was a wildcard on one command and a literal on
    # the other.
    search_repo.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern (SQL LIKE), repeatable, comma- or space-separated",
    )
    search_repo.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern (SQL LIKE), repeatable, comma- or space-separated",
    )
    search_repo.add_argument(
        "--by",
        "-by",
        action = "append",
        # Same correction as `calendar -by` above (ADT #467): the store holds
        # `%ae` and `runner.py` compares against it, so an author is an email.
        help   = "limit to commits by AUTHOR, a SQL LIKE pattern matched against "
                 "the commit author email, repeatable",
    )
    search_repo.add_argument(
        "--my",
        "-my",
        action = "store_true",
        help   = f"limit to commits by you, {COMMIT_IDENTITY_HELP}",
    )
    search_repo.add_argument(
        "--commit",
        "--commits",
        "-commit",
        "-commits",
        dest   = "commit_refs",
        action = "append",
        nargs  = "+",
        # Three spellings, all resolved by the shared `commit_ref_matches`
        # (`shared/commit_discovery.py:310`) that `patch -commit` also uses, so
        # both rows describe the same range syntax the same way (ADT #326).
        help   = "commit number(s), hash prefix(es), or ranges MIN-MAX / MIN+, "
                 "repeatable, comma- or space-separated",
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
        # edge, same shape, its own meaning.
        const = BARE_RECENT,
        type  = recent_window,
        help  = "only commits from recent DAYS or a fraction of a day, "
                "1/24 = past hour (bare -recent = 1)",
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
        help    = "list remote branches (origin/*) newest first, touching no cache; "
                  "any WORDs filter the name, AND-matched",
    )
    rebuild.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = None,
        metavar = "N",
        help    = "max rows the mode produces: branches revealed, commits shown under "
                  f"-switch, or commits read per branch when rebuilding "
                  f"(default {REVEAL_DEFAULT_LIMIT}; 0 = all)",
    )
    rebuild.add_argument(
        "--since",
        "-since",
        metavar = "WHEN",
        help    = "bound the run to WHEN onward, a YYYY-MM-DD date or a count of days "
                  "back (7 = 7 days ago)",
    )
    rebuild.add_argument(
        "--my",
        "-my",
        dest   = "my",
        action = "store_true",
        help   = "in reveal mode, limit to branches whose tip commit is yours, "
                 f"{COMMIT_IDENTITY_HELP}",
    )
    rebuild.add_argument(
        "--switch",
        "-switch",
        nargs   = "?",
        type    = int,
        const   = 1,
        default = None,
        metavar = "N",
        help    = "in reveal mode, check out the Nth branch of the filtered list "
                  "(1-based; bare -switch = 1) and show its recent commits instead",
    )
    rebuild.add_argument(
        "--verify",
        "-verify",
        action = "store_true",
        help   = "report each branch store's commit numbering without changing it",
    )
