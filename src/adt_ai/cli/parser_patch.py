from __future__ import annotations

from adt_ai.cli.parser_common import COMMIT_IDENTITY_HELP, SubParsers, add_connection_key_argument
from adt_ai.shared.dates import recent_window
from adt_ai.shared.recent_state import BARE_RECENT


def add_patch_parser(subparsers: SubParsers) -> None:
    patch = subparsers.add_parser(
        "patch",
        description="build and preview deployment patches",
        help="build and preview deployment patches",
    )
    patch.add_argument("--root", "-root", default=".", help="project root folder")
    patch.add_argument(
        "--config-dir",
        "-config-dir",
        action="append",
        help="folder containing config YAML",
    )
    # `-ref` was dropped by ADT #309, with no successor. Every read of it was
    # `args.ref or args.patch_code` and no code path told the two apart, so it
    # was a second spelling of the noun below, and the one whose existence forced
    # `-nosnap` to be invented, because `-ref` was the natural name for that
    # mode. Hard break: the old spelling is rejected on the raw argv.
    #
    # **The VERBS.** Bare flags again since `#465`, which is what they were before
    # `#350`. Neither takes a value, so there is no borrowing rule, no precedence
    # order and no mismatch guard: `-create -deploy` builds then ships in one
    # run, and with neither of them the command looks and prints.
    patch.add_argument(
        "--create",
        "-create",
        action = "store_true",
        help   = "build the patch named by -name",
    )
    patch.add_argument(
        "--deploy",
        "-deploy",
        action = "store_true",
        help   = "deploy the patch named by -name, exactly as it stands on disk",
    )
    # NOT "allow forced patch action" (ADT #326), which restated the flag name
    # and named no action at all. It had one reader until ADT #366 and now has
    # two, one per verb, and the row states both because a reader of `-create`
    # has no way to guess the `-deploy` meaning covers them:
    #
    # * `PatchRunner.deploy_patch` skips the same payload/target only after a
    #   complete successful run, including required verification, unless forced;
    # * `create_database_patch` refuses to rebuild a folder that has already been
    #   deployed unless forced, and with `-force` treats it as a refresh that
    #   keeps the deploy logs (Jan, 2026-08-15) and rebuilds the folder's own
    #   `patch_scripts/` from the project sources (Jan, 2026-08-24, ADT #508).
    #
    # One flag, one idea ("proceed on a patch this target has already run"), two
    # verbs, which is what §Command surface means by a flag not changing meaning
    # with the flag beside it: the behaviour is the same override in both.
    #
    # A third verb since ADT #639: with `-drop` it overrides the ownership check
    # (a sandbox somebody else created, or one recording no creator) and never
    # the rail (a derived sandbox id is the only kind that drops at all). Jan,
    # 2026-09-01: *"drop only the app created by me ... Unless there is -force"*.
    patch.add_argument(
        "--force",
        "-force",
        action = "store_true",
        help   = "proceed on a patch already deployed to this target: with -deploy "
                 "re-run it, with -create refresh it keeping its logs; with -drop "
                 "remove a sandbox somebody else created",
    )
    # `-deldiff` was withdrawn by ADT #356. Dropping the `%$1`/`%$2` leftovers is
    # not a thing to remember: the SQLcl DIFF run that writes them now clears
    # them in its own exception path, and `export_db` sweeps whatever a lost
    # connection left behind.
    patch.add_argument(
        "--continue",
        "-continue",
        action="store_true",
        dest="continue_patch",
        # NOT "continue previous patch action" (ADT #282/#309). That described
        # resuming an interrupted run, which this flag has never done: its only
        # consumer passes it as `continue_on_error` to `deploy_patch`, which
        # swaps `WHENEVER SQLERROR EXIT ROLLBACK` for `CONTINUE`. Jan kept the
        # name on 2026-08-13, so the fix is the text, and the text has to be
        # true on all four surfaces this promise was written on.
        # The comma matches the fourteen other "with -X, ..." rows; this row was
        # the only one using a colon (ADT #326).
        help="with -deploy, keep running the remaining install scripts after one fails",
    )
    # `-window` was withdrawn by ADT #351. How much history the scan walks and
    # how much of it fits on a screen are project settings, not something to
    # retype every run: `patch_scan_commits`, `patch_show_commits` and
    # `patch_show_patches` carry them.
    #
    # `-files` went with ADT #353. It never showed files: it set
    # `PatchRequest.files_only`, which FILTERED the commit list down to commits
    # touching usable files, while its own help and `docs/patch.md` advertised
    # "show commit files". A named patch now lists its files with no flag.
    # `-by` above `-my`, and both worded off one pattern, since ADT #364. Jan,
    # 2026-08-15: "-by and -my attributes are not listed consistently across
    # modules, sometimes the -my is above, other times below and I am not sure if
    # text is the same". `patch` was the only module declaring `-my` first.
    #
    # What each row states after "limit to" is the module's own noun and its own
    # identity source. `#364` wrote a DIFFERENCE in there that did not exist:
    # this `-my` matched `user.name` while `search_repo -my` and `rebuild -my`
    # matched `user.email`, and since all three read one store written with
    # `%ae`, the odd one out simply selected nothing (ADT #467). Both rows name
    # the email now, and both narrow the patch folders as well as the commits.
    patch.add_argument(
        "--by",
        "-by",
        action = "append",
        help   = "limit to commits and patches by AUTHOR, matched against the "
                 "commit author email, repeatable",
    )
    patch.add_argument(
        "--my",
        "-my",
        action = "store_true",
        help   = f"limit to commits and patches by you, {COMMIT_IDENTITY_HELP}",
    )
    # `-recent` on `patch` since ADT #467. Jan, 2026-08-22: "we should add
    # -recent attribute the same way we have it in export_db, but for filtering
    # commits & patch folders, so when I pass '-recent 1', it will show just
    # commits and patches created today".
    #
    # The parser shape is `search_repo -recent`'s, which is the nearest
    # precedent rather than merely the nearest neighbour: both read git history,
    # which carries no export watermark for a bare `-recent` to mean, so both
    # map the shared sentinel back to one day at the edge. Sharing `const` and
    # `type` is what holds all six declarations at one shape for
    # `tests/contracts/test_shared_argument_semantics.py`, which compares
    # `repr(const)` and `type.__name__`.
    patch.add_argument(
        "--recent",
        "-recent",
        nargs = "?",
        const = BARE_RECENT,
        type  = recent_window,
        help  = "only commits and patch folders from the last DAYS days or a "
                "fraction of a day, 1/24 = past hour (bare -recent = 1, today)",
    )
    patch.add_argument(
        "--search",
        "-search",
        action = "append",
        help   = "filter commits by a LIKE pattern, % any run and _ one "
                 "character; a discovery run, so -create beside it lists the "
                 "commits until -commit, -ignore or -force narrows them",
    )
    patch.add_argument(
        "--commit",
        "-commit",
        action = "append",
        nargs  = "+",
        help   = "commit number(s), hash prefix(es), or ranges MIN-MAX / MIN+ to "
                 "include, repeatable, comma- or space-separated",
    )
    # `-ignore` takes `-commit`'s exact shape since ADT #354. It is the same
    # concept pointed the other way, and it was declared as a single-value
    # `append` while its `docs/patch.md` row already advertised "commit numbers
    # or ranges", so a documented `-ignore 12-40` selected nothing at all and
    # said nothing about it. `commit_ref_matches` understood all three spellings
    # the whole time; only the parser and the reader disagreed.
    patch.add_argument(
        "--ignore",
        "-ignore",
        action = "append",
        nargs  = "+",
        help   = "commit number(s), hash prefix(es), or ranges MIN-MAX / MIN+ to "
                 "exclude, repeatable, comma- or space-separated",
    )
    # HASH MODE, rebuilt by ADT #447. `-hash` is declared BEFORE `-baseline`
    # because a section renders its rows in parser order, and Jan asked for that
    # sequence on 2026-08-21: "At help page, it should first list -hash, then
    # -baseline". It reads in the order the two are used, as well, you patch far
    # more often than you re-seed.
    #
    # The name comes back from `-rollout`, which `#309` renamed it to because
    # `search_repo -hash` takes git hash prefixes while `patch -hash` took a
    # commit NUMBER. What the mode does changed under it: the optional value is
    # now the baseline FILE to compare the working tree against, so the two
    # commands genuinely mean different things by the word and the divergence is
    # recorded in `SHAPE_EXCEPTIONS` rather than converged away.
    #
    # `""` is the flag-given-with-no-value sentinel and `None` the flag never
    # given, the same split `-create`/`-deploy` carry: a falsy-only guard would
    # read a bare `-hash` as absent and silently build a commit patch.
    patch.add_argument(
        "--hash",
        "-hash",
        nargs   = "?",
        const   = "",
        default = None,
        metavar = "FILE",
        help    = "patch what no longer matches the baseline, optional baseline FILE",
    )
    # `-locked` has no successor. It meant "trust the exact snapshot, recompute
    # nothing", which only existed because a rollout snapshot held just the
    # changed lines and had to be reassembled from every prior log. A baseline is
    # one complete file, so reading it IS the authoritative source and there is
    # nothing left for a second flag to switch off.
    patch.add_argument(
        "--baseline",
        "-baseline",
        nargs   = "?",
        const   = "",
        default = None,
        metavar = "FILE",
        help    = "record every current file hash as the deployed baseline, optional FILE",
    )
    patch.add_argument(
        "--install",
        "-install",
        action="store_true",
        help="create database install script",
    )
    # `-contents` was withdrawn by ADT #353. Listing what a patch holds is not a
    # mode to ask for, it is what naming a patch means, so every mode prints it.
    # `type=int` until ADT #346, which is why a pattern could not be typed at
    # all. A ref is now the patch's ticket number or a SQL LIKE pattern, told
    # apart by `matches_patch_selector` and never by the parser, so `-archive 66`
    # still means card 66 rather than a pattern that happens to be all digits.
    #
    # "the displayed ID" until ADT #510: `#467` dropped the `ID` column from the
    # folder listing and swept that wording out of `docs/` and the skill, and this
    # string was the one place it survived, describing a column no screen has.
    #
    # "omit refs for all" until ADT #513, which is the same failure one turn on: a
    # bare `-archive` now archives nothing and only lists what is on disk, so the
    # row advertised a sweep the command refuses. `-archive %` is how a sweep is
    # asked for now.
    patch.add_argument(
        "--archive",
        "-archive",
        nargs = "*",
        help  = "archive patch folders by ticket number or LIKE pattern; omit refs to only list",
    )
    # The drop step the derived sandbox id owes (ADT #592). An ACTION beside
    # `-archive`, not a modifier on `-deploy`: removing a sandbox application is
    # the step after a card lands exactly as archiving a patch folder is, and a
    # deploy that drops instead of importing is a deploy that deploys nothing. So
    # it takes no `-name` and no patch folder, only `-target` and the ids.
    #
    # `nargs="+"` rather than `"*"`: a bare `-drop` has no listing to fall back
    # on the way `-archive` does, and an action with no argument that does
    # nothing is a spelling of the default that parses and changes nothing
    # (SOP §Command surface).
    #
    # **`-force` never reaches the rail.** An id is droppable only when it is a
    # DERIVED sandbox id, which a flag must not be able to widen. What it does
    # override is the ownership check behind the rail (ADT #639): the creator
    # APEX recorded for the sandbox has to be the `apex_account` in
    # `config/IDENTITY.yaml`, else the run refuses and names who created it.
    patch.add_argument(
        "--drop",
        "-drop",
        nargs   = "+",
        type    = int,
        metavar = "ID",
        help    = "remove the sandbox APEX applications a -deploy -app run created, "
                  "yours by their recorded creator unless -force",
    )
    # **What the verbs above act ON**, and so the two rows that CLOSE the ACTIONS
    # section, `-name` first and `-target` under it (Jan, 2026-08-30, ADT #599:
    # *"Show -target below -name"*). Sequence inside a section is parser
    # declaration order, which is why both sit here rather than at the top of the
    # file: Jan asked for them last on 2026-08-30 (ADT #598). Their
    # `COMMAND_SECTION_OVERRIDES["patch"]` entries are what puts them in ACTIONS
    # at all, `cli/help.py` grouping by a GLOBAL `dest` that means a rename target
    # and a list filter elsewhere.
    #
    # **The NOUN.** One flag carries the patch name, in every mode, and the verbs
    # above say what happens to it (ADT #465).
    #
    # `#350` had put the name on the verbs instead, so `-patch`, `-create` and
    # `-deploy` each took one, and the rules for reconciling three spellings of
    # one fact were never finished: `-patch X -create` refused outright with
    # `Missing required patch name`, while `-patch Y -create X` and
    # `-patch Y -deploy X` both discarded `Y` in silence, the mismatch guard
    # having only ever compared `-create` against `-deploy`. Jan, 2026-08-21,
    # after hitting the first of those: *"Why we dont rename -patch to -name so
    # it is more clear and remove option to pass names in -create and -deploy? It
    # would look more natural and simpler"*.
    #
    # It is `-name` rather than `-patch` because the command is already `patch`:
    # `adtai patch -patch ABC` says the word twice and neither one says which of
    # the three things is about to happen to ABC.
    #
    # Hard break, no fallback spelling. Jan, same turn: *"Patch was not released
    # to users, so no fallbacks!"* `-patch`, `-create NAME` and `-deploy NAME`
    # simply stop parsing.
    patch.add_argument(
        "--name",
        "-name",
        dest    = "name",
        metavar = "PATCH_NAME",
        help    = "the patch to act on: its id, its patch code or its folder name",
    )
    # NOT "deployment target", which named the flag back at itself and left the
    # reader guessing between an environment, a schema and a patch folder
    # (ADT #326). The only readers are `commands_patch_deploy.py:59,130,140`,
    # every one of them `args.target or <the connection default>`.
    patch.add_argument(
        "--target",
        "-target",
        help="environment to deploy into (default: the connection file's default)",
    )
    # NOT "filter or select branch" (ADT #326): the "or" advertised two
    # behaviours where there is one. The single reader is
    # `commands_patch.py:268,279`: it validates the ref exists, then passes it
    # as the scan's branch, so a name that resolves to nothing fails loudly
    # instead of falling back to HEAD and patching the wrong history.
    patch.add_argument(
        "--branch",
        "-branch",
        nargs = "?",
        const = "",
        help  = "branch whose history the commit scan walks; an unknown name stops the run",
    )
    # `-fetch` was withdrawn by ADT #598. It carried one behaviour, a
    # `git fetch --prune origin` ahead of everything that reads history, and the
    # run that wants it is the run asking for the newest version of a file. Jan,
    # 2026-08-30: *"When I ask for -head, you will do the -fetch first,
    # obviously, then use HEAD commits!"* So `-head` owns the fetch and there is
    # no separate spelling of it left to type.
    #
    # Which version of each file `-create` snapshots (ADT #280). Mutually
    # exclusive, and the default, the committed version at each file's own
    # commit, has no flag of its own, so there is no spelling of the default
    # that parses and changes nothing (SOP §Command surface).
    patch.add_argument(
        "--local",
        "-local",
        action = "store_true",
        help   = "snapshot the working-tree file instead of its committed version",
    )
    patch.add_argument(
        "--head",
        "-head",
        action = "store_true",
        help   = "fetch from origin, then snapshot each file's newest committed "
                 "version, branch or remote, and skip the newer-commit warning",
    )
    patch.add_argument(
        "--nosnap",
        "-nosnap",
        action = "store_true",
        help   = "write no snapshots; link each repo file where it already lives",
    )
    # `-fullapp` folded into `-app` (ADT #592, Jan 2026-08-29). The two flags
    # answered one question between them: `-fullapp` said WHICH applications ship
    # whole, and the APEXlang deploy needed a way to say WHICH id they land on.
    # Jan: *"you can pass any number you like; if you dont pass the number, that
    # means no app id changes"*. So the value is the TARGET, and its absence is
    # what preserves today's behaviour rather than a second flag doing so.
    #
    # `-full` -> `-fullapp` was `#292`'s rename and `-fullapp` -> `-app` is that
    # one, so both old spellings are on `REMOVED_COMPATIBILITY_FLAGS`: `-full` is
    # a prefix of `-fullapp` AND of nothing else now, and argparse resolves an
    # unambiguous prefix differently across Python versions (see `cli/constants`).
    #
    # `None` is the flag never given and `[]` the flag given with no ids, the
    # same sentinel split `-hash` carries above (ADT #576). Defaulting to `[]`
    # under `nargs="*"` gave one value to both questions, so a bare `-fullapp`
    # was byte-identical to not passing the flag at all and the run listed every
    # component of the application it had been asked to ship whole.
    #
    # **Last of the MODIFIERS**, which is where Jan put it on 2026-08-30 (ADT
    # #598), and its override is what takes it out of FILTERS: `-app` selects
    # applications on `export_apex` and `dependencies`, while on `patch` the
    # value is the id the tree lands on, so it tunes the build rather than
    # narrowing what reaches it.
    patch.add_argument(
        "--app",
        "-app",
        nargs   = "*",
        type    = int,
        default = None,
        metavar = "ID",
        help    = "deploy the APEX application whole, optional ID lands it on "
                  "that application id instead of its own",
    )
    # `-rebuild` was declared here until ADT #345 withdrew it. It reached
    # `PatchRequest.rebuild` and was read by nothing, while its help and its
    # `docs/patch.md` row both advertised "rebuild commit cache", a behaviour
    # `patch` has never had: the per-branch commit cache belongs to `rebuild`,
    # and a `patch` run writes only its own internal scan file from a live
    # `git log` walk. Removed rather than implemented because the behaviour is
    # not missing, only differently housed (Jan, 2026-08-15).
    patch.add_argument(
        "--debug",
        "-debug",
        action = "store_true",
        help   = "show input parameters and SQL queries with bind values",
    )
    add_connection_key_argument(patch)
