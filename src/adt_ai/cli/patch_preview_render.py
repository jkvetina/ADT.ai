"""Rendering and argument helpers for the `patch` preview, opens no database.

Split out of `commands_patch.py` when ADT #285 pushed that module past the
20 KB context guard (`tests/contracts/test_context_file_size.py`). The same call
`#273` made for `patch_deploy_render.py` and `#272` for
`test_patch_deploy_errors.py`: a module that crosses the guard is split, never
registered as debt. The seam is the one that was already there, everything below
turns arguments and results into strings and rows, and nothing here connects,
reads the repo, or writes a file.

`patch_authors` deliberately lives elsewhere (`patch_inputs.py` since ADT #367):
it shells out to `git config`, and `tests/contracts/test_subprocess_home.py`
confines `subprocess` to the declared adapter modules. Moving it here would have
meant widening that allowlist to buy a few hundred bytes, and would have made the
opening claim above false. That is why `preview_folders` below takes its author
list as an argument rather than resolving one.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from adt_ai.cli.commands_patch_actions import (
    RECENT_PATCH_FOLDERS_HEADER,
    preview_folders,
    print_patch_folders,
    print_patch_plan,
)
from adt_ai.cli.constants import (
    PatchWorkspace,
    outstanding_records,
    preview_rows,
    print_adt_header,
    print_adt_table,
)
from adt_ai.cli.context import _flatten_arg_groups
from adt_ai.patch.content import (
    CONTENT_MODE_COMMITTED,
    CONTENT_MODE_FLAGS,
    CONTENT_MODE_LOCAL,
)
from adt_ai.patch.hashes import HashDiff
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.config import as_int


def patch_limit(config: dict[str, object], key: str, fallback: int) -> int:
    """Read a positive integer screen or scan limit out of config (ADT #351).

    A missing, blank, non-numeric or non-positive value falls back rather than
    raising. These three keys bound how much of a listing prints, so a project
    that typos one should get the shipped default, not a `patch` command that
    refuses to run at all: a broken screen limit is a cosmetic problem and
    should stay one.
    """
    try:
        value = as_int(config.get(key) or fallback)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def patch_show_patches(config: dict[str, object]) -> int:
    return patch_limit(config, "patch_show_patches", 20)


def patch_show_commits(config: dict[str, object]) -> int:
    return patch_limit(config, "patch_show_commits", 40)


def patch_scan_commits(config: dict[str, object]) -> int:
    return patch_limit(config, "patch_scan_commits", 100)


# `PatchAction` and `patch_action()` stood here until ADT #465, and the shape
# they had was itself the defect worth recording.
#
# `#350` moved the patch name off `-patch` and onto whichever verb was acting, so
# three flags could each carry one, and reconciling them needed a borrow rule
# (`#426` let `-deploy` read a name from `-patch`), a precedence order and a
# mismatch guard. The guard was written for one pair only, `-create` against
# `-deploy`, so `-patch Y -create X` and `-patch Y -deploy X` both dropped `Y`
# without a word, while `-patch ABC -create` refused with `Missing required patch
# name` for typing the name on the "wrong" flag. Jan hit the refusal on
# 2026-08-21 and named the fix himself: *"Why we dont rename -patch to -name so
# it is more clear and remove option to pass names in -create and -deploy? It
# would look more natural and simpler"*.
#
# One noun flag answers all of it. `args.name` is the patch, `args.create` and
# `args.deploy` are booleans, and there is nothing left to reconcile: the
# generalisable half is that a fact belongs on one flag, and a rule for merging
# three spellings of it is a design defect wearing an argument-parsing problem.


# The one header that means "no SEARCH TERM narrowed this list". A constant
# rather than the literal in two places: `records_for_header` decides what to
# filter by comparing against it, and a reworded header left in one place would
# silently stop filtering. That rename has now happened, and the constant is what
# made it safe.
#
# `RECENT UNPATCHED COMMITS:` since ADT #510. Jan, 2026-08-24: *"we are actually
# hiding patched commits, right? So lets rename"*. He is right, and the title now
# says so: `records_for_header` routes this header, and only this header, through
# `outstanding_records`. `RELEVANT`/`REQUESTED COMMITS:` keep their names, being
# a listing OF a patch rather than the outstanding-work screen.
RECENT_COMMITS_HEADER = "RECENT UNPATCHED COMMITS:"

# The commits a named patch carries. It used to append ` FOR PATCH <folder>`, so
# the header ran to 62 characters on a real project's patch and the dashed rule under
# it ran the same width, for a fact the screen states elsewhere anyway. Jan,
# 2026-08-21: *"remove the 'FOR PATCH ...' part, make it simple, just RELEVANT
# COMMITS"* (ADT #443). What the folder name was doing there is now `DEPLOYING
# PATCH: <folder>`'s job on the deploy side and the `PATCH FILES:` rows' job on
# the create side, so `#255`'s requirement that a run names the RESOLVED folder
# (never the string that selected it) still holds on both.
RELEVANT_COMMITS_HEADER = "RELEVANT COMMITS:"


@dataclass(frozen=True)
class PreviewListing:
    """What the commit table is CALLED, and which patch it is OF (ADT #424).

    `#417` answered the second question by re-reading the first: it filtered when
    the header string equalled `RECENT COMMITS:` and not otherwise. That works
    for exactly one of the three listings and reads as deliberate for the other
    two, which is how `-patch <NAME>` went on offering commits every other patch
    had already shipped. The identity of a listing is not recoverable from its
    own title, so `_preview_header` states it instead.

    ``folder`` is the patch folder the header NAMES, set only for the
    `<scope> COMMITS FOR PATCH <folder>:` form. `None` covers both the bare
    listing and a `-search`, which name no patch at all.

    ``requested`` says the caller selected commits by hand, with `-commit` or
    `-ignore`. It is a third question because it cuts across the other two: any
    of the three listings can carry one, and a listing that carries one is an
    instruction rather than a candidate set (ADT #438).
    """

    header : str
    folder : str | None = None
    requested : bool = False


# A `-patch <CODE>` preview ends at the commit table. `#274` restored three
# sections old ADT closed it with, `RECENT PATCHES:`, `TODAY'S FOLDERS:` and
# `TO CREATE PATCH SPECIFY SEQUENCE:`, and Jan removed all three on 2026-08-10
# (ADT #284): "If I am asking for a specific patch (65 for example), dont list
# these sections." A deliberate override of old-ADT parity, not a regression for
# the next `#262`-style audit to re-file: old ADT printed them precisely when a
# patch code was given (patch.py:226-231, :260-261, :313-315). Bare
# `adtai patch` (`-patch` was retired by `#350-#356`, folded into `-name`
# since `#465`) still lists the folders (`PATCH FOLDERS:`, `#265`/`#268`),
# which is the question those sections were answering.
def _preview_header(
    args: argparse.Namespace,
    patch_folder: str | None = None,
) -> PreviewListing:
    """`RECENT COMMITS:` only when nothing narrowed the list (old ADT patch.py:1204).

    A header that says "recent" over a filtered table is how a filter that did
    not fire goes unnoticed, which is exactly what `-patch 65` looked like.

    With a patch code the header names the **patch**, the same folder the
    banner just resolved, rather than the string that selected it (ADT #269).
    `-patch 65` used to print `RELEVANT COMMITS FOR "65":` three lines under
    `Next patch folder: .../260810-3-65`, quoting the argument back at a reader
    who already knew it. A deliberate divergence from old ADT, which quoted the
    search term (patch.py:1179-1205). `-search` still names its own terms:
    those, not the patch, are what narrowed the list.

    Since ADT #285 `patch_folder` is the folder that was RESOLVED on disk when
    one matched, so `-patch 260810-1-TASK68_BLOCK_B_TESTS` names itself back
    instead of the `_patch_code`-mangled name of a folder that never existed.
    """
    requested = bool(_flatten_arg_groups(args.commit) or args.ignore)
    terms = list(args.search or []) or ([args.name] if args.name else [])
    if not terms:
        return PreviewListing(RECENT_COMMITS_HEADER, requested=requested)
    scope = "REQUESTED" if requested else "RELEVANT"
    if patch_folder and not args.search:
        return PreviewListing(
            f"{scope} COMMITS FOR PATCH {patch_folder}:", patch_folder, requested
        )
    return PreviewListing(f'{scope} COMMITS FOR "{" ".join(terms)}":', None, requested)


def records_for_header(
    listing: PreviewListing,
    records: list[CommitRecord],
    workspace: PatchWorkspace,
) -> list[CommitRecord]:
    """Which commits the chosen listing actually covers (ADT #417, #424).

    Two of the three listings drop every commit a patch on disk already carries.
    `RECENT COMMITS:` is the work still to be addressed. A `<scope> COMMITS FOR
    PATCH <folder>:` listing is the candidate set for that patch, so a commit
    another patch already ships is noise there for the same reason, which is
    Jan's own wording on `#417`: *"only commits which needs to be addressed
    without the noise of already processed ones"*, a sentence that named no
    listing. Measured on MY_APP_DEV 2026-08-20, `-patch TEST` printed 16 rows of
    which 14 were already patched.

    **The patch the header names is exempt from its own filter**, which is the
    whole reason `#417` exempted the header instead and shipped this defect. A
    listing OF an existing patch is mostly commits that patch already carries:
    `-patch TASK131_REPLAY_CHAR` holds exactly one, commit 283, carried by
    `260819-17-TASK131_REPLAY_CHAR` itself, so a blanket filter empties the
    table it exists to fill. Excluding one folder costs nothing and cannot.

    `-search` is left alone. Jan settled it on chips 2026-08-20: a search is a
    hunt and may deliberately target work already shipped, so those terms narrow
    the listing and nothing else does.

    **A hand-picked commit set is not filtered at all (ADT #438).** `#417` and
    `#424` both wrote `-commit` down as the way back to a commit the filter
    hides (`outstanding_records`' own docstring says *"`-commit` is the stated
    way back to one"*) and neither wired it: `-commit` sets no search term, so
    the listing stayed the bare one and the filter still ran over it, which made
    the documented escape hatch select nothing. `tests/tools/
    patch_smoke.py` caught it, `-commit 2-3` returning commit 3 alone because an
    earlier check had built a patch carrying 2. This is the same invariant
    `test_explicit_commit_selection_survives_the_patch_code_filter` already pins
    one filter over: a commit the caller named is an instruction, and dropping
    it silently builds an empty patch that still reports success. `-ignore`
    rides the same flag, being the other half of one selection.

    Sits beside `_preview_header` because the two answer one question in two
    halves, what the listing is called and what it holds. It reads
    `PreviewListing.folder` rather than the header text for the reason that type
    exists: a title cannot say which patch its table is OF.
    """
    if listing.requested:
        return records
    if listing.header == RECENT_COMMITS_HEADER:
        return outstanding_records(records, workspace.discover())
    if listing.folder is None:
        return records
    others = [
        folder for folder in workspace.discover() if folder.folder != listing.folder
    ]
    return outstanding_records(records, others)


# `preview_folders` moved to `commands_patch_actions` with ADT #510, joining the
# renderer it feeds. The folder listing is one section, selection and rendering,
# and this module was 108 bytes under the 20 KB context guard with the section
# split across two files. Both of its callers already imported the renderer from
# there, so the move cost no new import edge.


def print_patch_preview(
    workspace: PatchWorkspace,
    config: dict[str, object],
    args: argparse.Namespace,
    records: list[CommitRecord],
    patch_ref: str | None,
    patch_folder_name: str | None,
    commit_table: bool = True,
    authors: list[str] | None = None,
    recent: int | float | None = None,
) -> int:
    """The whole LOOK screen: the commit table, then what a named patch holds.

    Moved out of `commands_patch.py` when ADT #417 pushed that file past the
    20 KB context guard (`tests/contracts/test_context_file_size.py`). The seam
    is the one the module names: everything here decides what the preview says
    and prints it, while `commands_patch.py` is left dispatching verbs. The
    header, the filter that follows from it, and the tables it heads had ended
    up in two files for no reason other than where they were first written.

    ``commit_table`` is off under `-hash` (ADT #447). That mode selects FILES,
    and `CHANGED FILES:` above is its listing; a commit table under it either
    re-renders the same selection in a second shape or, once the synthetic
    carrier record is filtered out of commit tables, stands empty over nothing.
    Both were on screen in the first live run against a real schema.
    """
    listing = _preview_header(args, patch_folder_name)
    if commit_table:
        print_adt_header(listing.header)
        # No trailing `print()`: `print_adt_table` already ends with its own
        # blank and commits it, and the shared footer normalizer owns everything
        # after that. A per-call blank here is the spacing override the console
        # contract forbids, and it is what made the run print four empty lines
        # before TIMER instead of two (ADT #269).
        print_adt_table(
            preview_rows(
                records_for_header(listing, records, workspace),
                patch_show_commits(config),
            )
        )
    if patch_ref:
        # A named patch is being INSPECTED, so what it holds is the answer, not
        # a mode to ask for (ADT #353).
        print_patch_plan(workspace, config, patch_ref)
        return 0
    # Bare `patch`: the commits above, then what is already built (ADT #352).
    # The folders go last on purpose, so the table you read to pick the next
    # `-deploy` is the one still on screen when the run ends. Newest row at the
    # TOP since `#510`, which reversed `#285`.
    #
    # The header is `RECENT_PATCH_FOLDERS_HEADER` because this table is narrowed
    # twice over, by `patch_show_patches` and by `preview_folders`' author/window
    # filters; the constant's own comment argues the split from the other side.
    print_patch_folders(
        preview_folders(workspace, records, authors, recent),
        RECENT_PATCH_FOLDERS_HEADER,
        patch_show_patches(config),
    )
    return 0


def _ignored_create_arguments(args: argparse.Namespace) -> list[str]:
    """Name the patch-building arguments `-deploy` drops, in command-line order.

    They only steer how a patch is *built*, so under `-deploy` they do nothing,
    and a flag that does nothing is worth one line of output, not silence.
    """
    ignored = []
    # `-create` is NOT listed since ADT #350. It stopped being refused work the
    # moment it started carrying the patch name: under `-create NAME -deploy` it
    # is how the run learns which patch to ship, and when no folder existed it is
    # what built the one being shipped. Announcing it as ignored would report a
    # flag that did its job as a flag that did nothing.
    if args.hash is not None:
        ignored.append("-hash")
    if args.baseline is not None:
        ignored.append("-baseline")
    # The content-source flags steer what a snapshot CONTAINS (ADT #280), so
    # under `-deploy` (which ships the folder exactly as it stands) they are
    # refused work like every other build argument, not silently dropped.
    ignored.extend(flag for flag in CONTENT_MODE_FLAGS if getattr(args, flag[1:], False))
    return ignored


# `_ignored_deploy_arguments` stood here until ADT #443. It named `-force` and
# `-continue` on a non-deploy run so `IGNORING WITHOUT -deploy:` could announce
# them (ADT #309, from #292 §2c). Jan deleted that section on 2026-08-21 -- *"I
# did not asked for it"* -- and the helper went with it rather than staying as a
# function nothing calls, which is the same "no accepted-but-unused" rule SOP
# §Command surface applies to flags. `_ignored_create_arguments` above is
# untouched: `IGNORING WITH -deploy:` announces BUILD flags a deploy will not act
# on, which is a real surprise rather than a non-event.


def _selected_content_modes(args: argparse.Namespace) -> list[str]:
    return [flag for flag in CONTENT_MODE_FLAGS if getattr(args, flag[1:], False)]


def _content_mode(args: argparse.Namespace) -> str:
    """The content mode this run selected, or the committed default (ADT #280).

    `-hash` forces `local` (ADT #447). Hash mode's whole question is whether the
    WORKING TREE still matches the baseline, so shipping any other version of a
    file it selected would deploy bytes the comparison never looked at, and the
    baseline advanced after that deploy would record a hash for content that
    never reached the database. `-head` and `-nosnap` are refused beside `-hash`
    rather than silently overridden, so the only mode that reaches here with
    hash mode on is the default or an explicit `-local` that agrees with it.
    """
    selected = _selected_content_modes(args)
    if selected:
        return selected[0][1:]
    return CONTENT_MODE_LOCAL if getattr(args, "hash", None) is not None else (
        CONTENT_MODE_COMMITTED
    )


def _hash_changed_rows(
    diff: HashDiff,
    commits: dict[str, int] | None = None,
) -> list[dict[str, object]]:
    """One row per file the working tree no longer agrees with the baseline on.

    `STATUS` replaced the old `PREVIOUS` commit column (ADT #447). A rollout
    could only ever report a changed hash, so the only thing worth printing was
    which commit each side came from; a baseline knows whether a file is
    MODIFIED, NEW or DELETED, and that is what decides whether the patch ships
    the file or a DROP helper for it.
    """
    commits = commits or {}
    return [
        {
            "FILE": file,
            "COMMIT": commits.get(file, ""),
            "STATUS": diff.status(file),
        }
        for file in diff.files
    ]


__all__ = [name for name in globals() if not name.startswith("__")]
