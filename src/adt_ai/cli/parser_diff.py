"""The `diff` command's parser (ADT #893).

Split out of `cli/parser_database.py` when `-page` took that module to the 24 KB
context cap. `diff` is the one command there whose flags keep growing, a mode
per card, so it is the one that leaves; `parser_database.add_database_parsers`
still registers it first, so the command order `--help` lists is unchanged.
"""
from __future__ import annotations

from adt_ai.cli.parser_common import SubParsers, add_connection_key_argument


def add_diff_parser(subparsers: SubParsers) -> None:
    diff = subparsers.add_parser(
        "diff",
        description="compare two schemas and generate a SQLcl DIFF deployment artifact",
        help="compare schemas using SQLcl DIFF",
    )
    diff.add_argument("--root", "-root", default=".", help="project root folder")
    diff.add_argument(
        "--config-dir",
        "-config-dir",
        action = "append",
        help   = "folder containing config YAML",
    )
    # Defaulted rather than required (Jan, ADT #773: *"-source ENV should default
    # to current env"*). It falls back to the connection default environment, the
    # same fallback `-env` documents on every other command, so the side you are
    # standing in is the side you compare FROM without saying so.
    diff.add_argument(
        "--source",
        "-source",
        metavar  = "ENV",
        help     = "source environment name (defaults to the connection default)",
    )
    # `-schema`, not `-source-schema`: the usual comparison is one schema across
    # two environments, so the name that reads as "the schema" is the one a user
    # types (Jan, ADT #763). `-target-schema` is the exception it was always
    # meant to be, and defaults to this one rather than to the environment.
    diff.add_argument(
        "--schema",
        "-schema",
        metavar = "SCHEMA",
        help    = "source schema (defaults to environment default)",
    )
    diff.add_argument(
        "--target",
        "-target",
        metavar  = "ENV",
        help     = "target environment name",
    )
    diff.add_argument(
        "--target-schema",
        "-target-schema",
        metavar = "SCHEMA",
        help    = "target schema (defaults to -schema)",
    )
    # A `.zip` path names the artifact, anything else is the folder it lands in
    # (`#773`). One suffix rather than "does it look like a file", so a folder
    # called `releases/v1.2` cannot be mistaken for one. Under `-data` it names
    # the file the untrimmed rows go to, `.log`/`.txt` or a folder (`#886`).
    diff.add_argument(
        "--out",
        "-out",
        metavar = "DIR|FILE",
        help    = "output folder, or a .zip path naming the artifact "
                  "(default: <root>/config/diff); with -data, write every "
                  "differing row untrimmed to a .log/.txt file or into DIR",
    )
    # Multi-pattern, the shape `recompile` and `export_db` take: `-type A B`,
    # `-type A,B` and a repeated `-type A -type B` all work. Both filters narrow
    # the EXPORT as well as the screen, so a filtered run compares less and its
    # artifact holds only what was asked for (`#780`, overruling `#773`).
    diff.add_argument(
        "--type",
        "-type",
        action = "append",
        nargs  = "+",
        help   = "object type pattern(s) to compare, repeatable, comma- or "
                 "space-separated, supports %% wildcards; narrows the export and "
                 "the screen",
    )
    diff.add_argument(
        "--name",
        "-name",
        action = "append",
        nargs  = "+",
        help   = "object name pattern(s) to compare, repeatable, comma- or "
                 "space-separated, supports %% wildcards; a grant matches on the "
                 "object it grants",
    )
    diff.add_argument(
        "--verbose",
        "-verbose",
        action = "store_true",
        help   = "list every changed object, not just the count per object type; "
                 "with -data, each table's differing rows and values",
    )
    # Jan's spelling and scope, asked with chips (ADT #878): `-rest` compares the
    # REST modules, privileges and roles both schemas publish INSTEAD of the
    # schema objects. Same `store_true` shape as `export_apex -rest`, the flag it
    # reuses the export of.
    diff.add_argument(
        "--rest",
        "-rest",
        action = "store_true",
        help   = "compare the REST modules, privileges and roles both schemas "
                 "publish, instead of the schema objects",
    )
    # Jan's spelling and scope, asked with chips (ADT #877): `-data` compares the
    # rows of the tables `export_data` exports, database against database,
    # INSTEAD of the schema objects. `store_true`, the `-rest` shape beside it.
    diff.add_argument(
        "--data",
        "-data",
        action = "store_true",
        help   = "compare the rows of the tables export_data exports, instead of "
                 "the schema objects",
    )
    # Jan's spelling and scope, asked with chips (ADT #778): `-apex` compares the
    # APEX applications and static files both schemas own INSTEAD of the schema
    # objects, with an optional repeatable `-app`. `-app` takes the
    # `export_apex -app` shape, ids and MIN-MAX / MIN+ ranges alike, so the
    # flag parses one way in every module.
    diff.add_argument(
        "--apex",
        "-apex",
        action = "store_true",
        help   = "compare the APEX applications and static files both schemas own, "
                 "instead of the schema objects",
    )
    diff.add_argument(
        "--app",
        "-app",
        action = "append",
        nargs  = "+",
        help   = "with -apex, application id(s), or ranges MIN-MAX / MIN+, to compare",
    )
    # Jan, ADT #893, told one instance cannot hold application 100 twice: *"we
    # should add a new argument -target-app to override the app, this will allow
    # us to compare our app to a working copy."* One id, paired with the one
    # application `-app` names.
    diff.add_argument(
        "--target-app",
        "-target-app",
        type    = int,
        metavar = "ID",
        help    = "with -apex, the target application paired with the one -app names",
    )
    # Jan, ADT #893: *"in -apex -app we should also support -page to check
    # specific page, at that point you would ignore app changes and workspace
    # changes, -page should support the usual ranges combo"*. The
    # `export_apex -page` shape, so the flag parses one way in every module.
    diff.add_argument(
        "--page",
        "-page",
        action = "append",
        nargs  = "+",
        help   = "with -apex, page id(s), or ranges MIN-MAX / MIN+, to compare; "
                 "application-wide and workspace changes are left out",
    )
    # Jan, ADT #883, with chips: audit columns left out on demand, beside the
    # `ignored_columns` config and the identity columns `-data` always skips.
    # The `patch -ignore` shape, so a pattern list reads the same everywhere.
    diff.add_argument(
        "--ignore",
        "-ignore",
        action = "append",
        nargs  = "+",
        help   = "with -data, column pattern(s) to leave out on both sides, "
                 "repeatable, comma- or space-separated, supports %% wildcards",
    )
    # Jan, ADT #883: *"-limit # to limit number of rows as a shortloop"*. A
    # table stops after N differing rows. `#893` made it every mode's, Jan:
    # *"-limit should be applicable across all types, basically used to check
    # if we have changes or not"*: each listing prints at most N rows.
    diff.add_argument(
        "--limit",
        "-limit",
        type    = int,
        default = None,
        metavar = "N",
        help    = "list at most N rows per listing, the counts stay whole; with "
                  "-data, stop each table after N differing rows (0 = all)",
    )
    # Jan, ADT #893: *"Add a new flag ... for all types (db objects, data, rest,
    # apex) and if this is passed, you will resurrect these target versions into
    # their location in current/requested branch"*, and *"Current branch, or
    # -branch override"* for where it lands. Both are MODIFIERS: on `diff`,
    # ACTIONS holds only what a run cannot work without. `#897` renamed it from
    # `-pull`, with no alias, Jan: *"I am inclined to use -restore on both
    # places, since it looks more dramatic (and we are overwriting changes in
    # files/git)"*, and uncommitted work is saved as a WIP commit first.
    diff.add_argument(
        "--restore",
        "-restore",
        action = "store_true",
        help   = "write the target version of everything that differs into -root, "
                 "where the source export keeps it, over a WIP commit of any "
                 "uncommitted work, for git to show",
    )
    diff.add_argument(
        "--branch",
        "-branch",
        metavar = "BRANCH",
        help    = "with -restore, the branch to write on, created from HEAD when new "
                  "(default: the current branch)",
    )
    # NOT "show debug info" (ADT #326), which was the one -debug row of eleven
    # that named neither what is shown nor where it comes from. `-debug` appends
    # itself to the SQLcl DIFF command (`diff/runner.py`), so the extra output is
    # SQLcl's, not ADT.ai's; `-verbose` above is ADT.ai's own screen.
    diff.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters, and pass -debug to the SQLcl DIFF command",
    )
    add_connection_key_argument(diff)
