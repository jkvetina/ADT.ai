from __future__ import annotations

import argparse

from adt_ai.cli.constants import REVEAL_DEFAULT_LIMIT
from adt_ai.cli.parser_common import (
    COMMIT_IDENTITY_HELP,
    SubParsers,
    add_connection_key_argument,
)
from adt_ai.shared.dates import recent_window
from adt_ai.shared.recent_state import BARE_RECENT


def add_history_parsers(subparsers: SubParsers) -> None:
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
    search = subparsers.add_parser(
        "search",
        description=(
            "search the cached Git commit history, the object dependency graph and "
            "APEX page links"
        ),
        help="search commit history, object dependencies and page links",
    )
    search.add_argument("--root", "-root", default=".", help="project root folder")
    _add_search_term_arguments(search)
    _add_search_graph_arguments(search)
    search.add_argument("--branch", "-branch", help="branch or ref to search")
    # `-commit` and `-hash` are declared right after `-branch` because a section
    # renders in parser order, and Jan picked the HISTORY FILTERS sequence that
    # names the commits before the words describing them (ADT #894).
    search.add_argument(
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
    search.add_argument(
        "--hash",
        "-hash",
        action = "append",
        nargs  = "+",
        help   = "commit hash prefix(es)",
    )
    search.add_argument(
        "--limit",
        "-limit",
        type    = int,
        # `None` rather than the number, so a `-limit` typed beside a graph
        # query can be told apart from the default and refused; the history
        # run resolves the default itself.
        default = None,
        metavar = "N",
        help    = f"max commits to print, or with -data rows per object "
                  f"(default {REVEAL_DEFAULT_LIMIT}; 0 = all)",
    )
    search.add_argument(
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
    search.add_argument(
        "--summary",
        "-summary",
        nargs = "*",
        help  = "summary word(s), AND-matched case-insensitively",
    )
    search.add_argument(
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
    search.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern (SQL LIKE), repeatable, comma- or "
                 "space-separated; also narrows -app",
    )
    search.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern (SQL LIKE), repeatable, comma- or "
                 "space-separated; also narrows -app and the objects -data searches",
    )
    search.add_argument(
        "--by",
        "-by",
        action = "append",
        # Same correction as `calendar -by` above (ADT #467): the store holds
        # `%ae` and `runner.py` compares against it, so an author is an email.
        help   = "limit to commits by AUTHOR, a SQL LIKE pattern matched against "
                 "the commit author email, repeatable",
    )
    search.add_argument(
        "--my",
        "-my",
        action = "store_true",
        help   = f"limit to commits by you, {COMMIT_IDENTITY_HELP}",
    )
    search.add_argument(
        "--recent",
        "-recent",
        nargs = "?",
        # Shares export_db/export_apex's sentinel so `-recent` keeps
        # ONE parser shape across every module (the shared-argument-semantics
        # contract compares `repr(const)`). search reads git history, which
        # has no export watermark, so it maps the sentinel back to 1 day at the
        # edge, same shape, its own meaning.
        const = BARE_RECENT,
        type  = recent_window,
        help  = "only commits from recent DAYS or a fraction of a day, "
                "1/24 = past hour (bare -recent = 1)",
    )
    search.add_argument("--since", "-since", help="oldest commit date, YYYY-MM-DD")
    search.add_argument("--until", "-until", help="newest commit date, YYYY-MM-DD")
    # ADT #897 folded `-stage` into this one, Jan: *"-restore & -stage has to
    # merge, it has to be consistent with current diff -pull"*. So a version
    # lands on its original path, with no `git add` and no side copy, over a
    # WIP commit of what the checkout held, and `git diff` shows the restore.
    search.add_argument(
        "--restore",
        "-restore",
        action = "store_true",
        help   = "write each matching historical version over its original path, "
                 "over a WIP commit of any uncommitted work, for git to show",
    )
    _add_rebuild_parser(subparsers)


def _add_search_term_arguments(search: argparse.ArgumentParser) -> None:
    """The text search, `adtai search TERM` (ADT #895).

    The one positional any `adtai` command takes. It is optional, so a run
    without it is every mode `search` had before, unchanged. It goes first
    because `-app`, `-page`, `-schema` and `-layer` each take a list, and a
    word typed after one of them is read as one more value of that list.
    """
    search.add_argument(
        "term",
        nargs   = "?",
        metavar = "TERM",
        # Read by `commands_search_term._run_search_term`.
        help    = "find this text in APEX components, static files, exported database "
                  "objects, commits and project files; write it first, before any flag",
    )
    search.add_argument(
        "--layer",
        "-layer",
        action  = "append",
        nargs   = "+",
        metavar = "LAYER",
        # Read by `commands_search_term._term_layers`; the values are
        # `search.term_model.LAYERS`, matched case-insensitively.
        help    = "with TERM, only these layers: APEX, STATIC, DB, GIT or FILES, "
                  "repeatable, comma- or space-separated (default all five)",
    )
    # Jan's spelling and scope, asked with chips (ADT #879): `-data` searches the
    # rows of the tables in the database INSTEAD of the five offline layers,
    # narrowed by `-schema`, `-name` and `-limit`. `store_true`, the shape
    # `diff -data` already has. `#920` widened it to what a SELECT reads.
    search.add_argument(
        "--data",
        "-data",
        action = "store_true",
        help   = "find TERM in the rows of the tables, views, mviews and synonyms "
                 "instead of the five layers, narrowed by -schema, -name and -limit",
    )


def _add_search_graph_arguments(search: argparse.ArgumentParser) -> None:
    """The graph questions `search` answers beside history (`#30`).

    `-from` and `-to` take one string: a value opening on a digit is a page
    written `APP.PAGE`, anything else an object. `-app` lists the objects an
    application uses, narrowed by `-page`. Their dests are their own
    rather than the retired `dependencies` command's `uses`/`used_by`, because
    on a page the answer is a link rather than a dependency, and never
    `search`, which `patch
    -search` owns in the global dest vocabulary `help.py` groups by.
    """
    search.add_argument(
        "--from",
        "-from",
        dest    = "graph_from",
        metavar = "OBJ|APP.PAGE",
        help    = "objects OBJ depends on, or pages reachable from APP.PAGE",
    )
    search.add_argument(
        "--to",
        "-to",
        dest    = "graph_to",
        metavar = "OBJ|APP.PAGE",
        help    = "objects that depend on OBJ, or pages that link into APP.PAGE",
    )
    search.add_argument(
        "--impact",
        "-impact",
        metavar = "OBJ",
        help    = "transitive reverse impact of OBJ",
    )
    search.add_argument(
        "--constraint",
        "-constraint",
        metavar = "CONSTRAINT",
        help    = "foreign-key reference and dependency cascade for CONSTRAINT",
    )
    # `-app` lists what an application uses, from the same mirror the graph
    # questions read (`#30`). Ids and ranges take the shapes `rebuild -app` and
    # `validate -app` take, resolved against the applications the mirror holds.
    search.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "APEX application id(s), or ranges MIN-MAX / MIN+, whose database "
                 "objects to list or whose source TERM searches, repeatable, comma- "
                 "or space-separated",
    )
    search.add_argument(
        "--page",
        "-page",
        action = "append",
        nargs  = "+",
        help   = "with -app, only the objects used on, or the TERM hits on, these "
                 "page id(s) or ranges MIN-MAX / MIN+",
    )
    search.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "owner schema(s) of the object asked about, of the objects -app "
                 "lists, or of the files or tables TERM searches (default the "
                 "configured schemas), repeatable, comma- or space-separated",
    )
    search.add_argument(
        "--format",
        "-format",
        choices = ["table", "yaml", "md"],
        default = "table",
        help    = "output format of a graph answer (default: table)",
    )


def _add_rebuild_parser(subparsers: SubParsers) -> None:
    rebuild = subparsers.add_parser(
        "rebuild",
        description=(
            "rebuild the local caches: the git commit cache for the current branch, "
            "the object dependencies of a schema, and on demand an APEX "
            "application's dependencies and page links"
        ),
        help="rebuild the commit, dependency and page-link caches",
    )
    rebuild.add_argument("--root", "-root", default=".", help="project root folder")
    rebuild.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    rebuild.add_argument("--env", "-env", help="connection environment")
    rebuild.add_argument(
        "--schema",
        "-schema",
        action = "append",
        nargs  = "+",
        help   = "schema(s) whose object dependencies to refresh, repeatable, comma- "
                 "or space-separated; default is the connection's default schema",
    )
    rebuild.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "APEX application id(s), or ranges MIN-MAX / MIN+ resolved against "
                 "discovered apps, whose dependencies and page links to refresh, "
                 "repeatable, comma- or space-separated",
    )
    rebuild.add_argument(
        "--force",
        "-force",
        action = "store_true",
        help   = "wipe the dependency and page-link caches in scope before reloading "
                 "them, instead of reloading only what changed",
    )
    add_connection_key_argument(rebuild)
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
