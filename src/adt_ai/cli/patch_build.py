"""The steps a `patch` run walks between its flags and the folder it writes.

Split out of ``commands_patch.py`` by ADT #670. `_run_patch_command` had reached
340 lines carrying eleven separate jobs -- argument validation, workspace
construction, the fetch, branch validation, the supporting actions, name and
folder resolution, hash mode, content mode, the graph refresh, the commit scan,
the baseline, the no-commits answer, the discovery gate and finally the build --
each with its own reasoning inline. So the ONE thing that function exists to
express, the ORDER those steps happen in, was the hardest thing in it to see,
and every reader had to hold thirty comment blocks to find out whether `-drop`
runs before or after the commit store is levelled.

The seam is the one this package already draws twice. `patch_inputs.py` holds
what a run resolves from its arguments and the repository; `patch_preview_render.py`
holds what it prints. This module is the middle: the steps that DECIDE. Each one
answers rather than exits -- a refusal comes back as a message, not as a
`print` and a return code -- so `_run_patch_command` stays a list of steps with
every exit visible in one screen.

It is a pure move. Nothing here changed behaviour except where ADT #670 says so
in the docstring that carries it (`build_flag_refusal` merges two refusals that
were already adjacent, and nothing else).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from adt_ai.cli.commands_patch_actions import run_archive_patches, run_install_script
from adt_ai.cli.commands_patch_drop import run_drop_applications
from adt_ai.cli.constants import (
    GatewayFactory,
    PatchError,
    PatchWorkspace,
)
from adt_ai.cli.patch_create_render import print_create_screen
from adt_ai.cli.patch_hash_mode import HashSelection, apply_hash_mode, hash_mode_error
from adt_ai.cli.patch_preview_render import _content_mode, _selected_content_modes
from adt_ai.patch import settings as patch_settings
from adt_ai.patch.apex_import import resolve_target
from adt_ai.patch.content import CONTENT_MODE_FLAGS
from adt_ai.shared.patch_folders import PatchFolder


def dispatch_supporting_actions(
    args: argparse.Namespace,
    *,
    root: Path,
    workspace: PatchWorkspace,
    config: dict[str, Any],
    gateway_factory: GatewayFactory | None,
) -> int | None:
    """Run whichever supporting action was asked for, or answer ``None``.

    The two that write nothing to a database return before any commit scan
    (`commands_patch_actions.py`). `-deldiff` and `-contents` were the other
    two: cleanup became automatic (#356) and listing a patch's contents became
    what naming one does (#353).

    `-drop` is the third (ADT #592). It connects, so it lives beside the deploy
    rather than beside the other two, and it names no patch at all.
    """
    if args.install:
        return run_install_script(root, workspace, config)
    if args.archive is not None:
        return run_archive_patches(args, workspace, config)
    if args.drop:
        return run_drop_applications(
            args,
            root            = root,
            config          = config,
            gateway_factory = gateway_factory,
        )
    return None


def missing_patch_name(args: argparse.Namespace, patch_ref: str | None) -> str | None:
    """The refusal a build or a deploy with no `-name` earns, or ``None``.

    One noun, two verbs (ADT #465, reasoning in `parser_patch.py`). `-name`
    carries the patch in every mode, so `patch_action()` and the whole borrow /
    precedence / mismatch apparatus it needed are gone, and the one requirement
    that survives is that a build or a deploy says which patch.
    """
    if patch_ref or not (args.create or args.deploy):
        return None
    verb = "-create" if args.create else "-deploy"
    return f"Missing required patch name: pass -name PATCH_NAME with {verb}"


@dataclass(frozen=True)
class PatchSelection:
    """Which patch this run is about, and whether it is going to build one."""

    #: `None` when the code names no folder yet, the normal pre-create case.
    selected_folder: PatchFolder | None
    #: The folder name for the preview header: an existing folder's own, or the
    #: one `-create` would mint.
    folder_name: str | None
    create_requested: bool

    def previewing(self) -> PatchSelection:
        """The same selection with the build stood down (ADT #570)."""
        return replace(self, create_requested=False)


def resolve_patch_name_and_folder(
    args: argparse.Namespace,
    workspace: PatchWorkspace,
    config: dict[str, Any],
    patch_ref: str | None,
) -> PatchSelection:
    """Resolve `-name` against what is on disk, before anything mints a name.

    Existence decides whether `-create -deploy` builds anything: a folder on
    disk is one Jan has already read, so rebuilding it would ship something
    other than what was reviewed. Only an unbuilt name is built then shipped.

    Jan, 2026-08-10: *"I should be able to refer to the patch also by the folder
    name, like -name 260810-1-BLOCK_B_TESTS"*. That failed twice over:
    `next_folder` runs the value through `_patch_code`, which rewrites every
    non-alphanumeric to `_` and so named a brand new
    `260810-<seq>-260810_1_TASK68_BLOCK_B_TESTS`; and the raw argument doubled
    as the commit search term, which no commit subject carries. Resolving first
    fixes both, and `resolve` accepts the ID, the patch code and the full folder
    name (`#268`), each matched whole (`#255`).

    `-create` goes through here too since `#289`. It was excluded, so the one
    path that MINTS a name was the one path that never resolved first, which is
    how a folder name still became a mangled second folder after `#285` had
    documented it as a valid value. Jan, settling `#267`: *"I already told you to
    support ID, FOLDER NAME and CARD NAME"*, all three, everywhere.
    """
    create_requested = args.create
    if args.create and args.deploy and workspace.resolve(patch_ref) is not None:
        create_requested = False
    if not (patch_ref and (create_requested or not args.deploy)):
        return PatchSelection(
            selected_folder  = None,
            folder_name      = None,
            create_requested = create_requested,
        )
    selected_folder = workspace.resolve(patch_ref)
    # The project's own `patch_folder` shape (#430), so a renamed folder layout
    # still gets the typo guard below.
    folder_re = patch_settings.patch_folder_re(config)
    if selected_folder is None and folder_re.match(patch_ref):
        # A well-formed folder name resolving to nothing is a typo, not a new
        # patch code. Minting `260811-<seq>-260101_1_NOPE` from it is the silent
        # mangling `#289` forbids whichever way the rewrite question landed.
        raise PatchError(
            f"{patch_ref!r} looks like a patch folder name but no such "
            "folder exists - check the name, or pass a patch code to create a "
            "new patch"
        )
    return PatchSelection(
        selected_folder = selected_folder,
        # Resolved for the preview header alone, and never printed on a line of
        # its own: `Next patch folder: <path>` was an ADT.ai invention that
        # appears nowhere in old ADT (ADT #274). The header is the only place
        # the resolved folder is named.
        folder_name     = (
            selected_folder.folder
            if selected_folder
            else workspace.next_folder(patch_ref).name
        ),
        create_requested = create_requested,
    )


def build_flag_refusal(args: argparse.Namespace) -> str | None:
    """The refusal a contradictory build earns, or ``None`` when it is coherent.

    Two questions with one answer each, checked in the order the run has always
    checked them: whether `-hash` was given a shape it can act on, and whether
    the run named more than one content source. Three answers to one question
    (ADT #280): a run naming two has no defined content source, and silently
    picking one would put a file version nobody asked for into a patch.
    """
    hash_failure = hash_mode_error(args)
    if hash_failure:
        return hash_failure
    conflicting = _selected_content_modes(args)
    if len(conflicting) > 1:
        return (
            f"Pass one of {', '.join(CONTENT_MODE_FLAGS)}, "
            f"not {' and '.join(conflicting)}"
        )
    return None


@dataclass(frozen=True)
class HashOutcome:
    """What hash mode did to the run: its records, its report, and whether to stop."""

    records: list[Any]
    selection: HashSelection | None
    #: `-hash` modes that ANSWER rather than build (a report, a refusal already
    #: printed) close the run here rather than falling through to a preview of
    #: commits they never selected.
    stop: bool


def select_content_and_hash(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
    records: list[Any],
    *,
    create_requested: bool,
) -> HashOutcome:
    """Narrow the scanned commits to what `-hash` selected, if anything.

    A run without `-hash` passes its records straight through and reports no
    selection, which is what the preview reads to decide whether a commit table
    belongs under `CHANGED FILES:` (ADT #447).
    """
    if args.hash is None:
        return HashOutcome(records=records, selection=None, stop=False)
    selection = apply_hash_mode(
        args,
        root,
        config,
        records,
        create_requested = create_requested,
    )
    return HashOutcome(
        records   = selection.records,
        selection = selection,
        stop      = not selection.keep_going,
    )


def build_database_patch(
    args: argparse.Namespace,
    workspace: PatchWorkspace,
    config: dict[str, Any],
    selection: PatchSelection,
    *,
    patch_ref: str,
    records: list[Any],
    window: list[Any],
    hash_selection: HashSelection | None,
    root: Path,
) -> None:
    """Write the patch folder and print the whole `-create` screen.

    The screen's section order is the render module's business, not this
    function's (ADT #443).
    """
    result = workspace.create_database_patch(
        config,
        patch_code = (
            selection.selected_folder.patch_code
            if selection.selected_folder
            else patch_ref
        ),
        records    = records,
        # `-app`'s SELECTION half (ADT #592). Its target half never reaches the
        # build: retargeting moves where the tree lands, not which files ship,
        # so the two questions stay separable and the build reads the same
        # `None`/`[]` it always did.
        full_app_ids = resolve_target(args.app).full_app_ids,
        target_env = args.target,
        # Resolved above, so a re-create rewrites THAT folder rather than
        # minting a second one from the string that selected it (ADT #289).
        folder     = selection.selected_folder.path if selection.selected_folder else None,
        # `local` under `-hash`, decided inside `_content_mode` (ADT #447): hash
        # mode compared the WORKING TREE against the baseline, so the working
        # tree is what has to ship, or the baseline advances to a hash of bytes
        # nobody deployed.
        content_mode = _content_mode(args),
        window     = window,
        # Only a hash-built patch records what it shipped, and recording it is
        # what marks the folder as hash-built for `-deploy` (ADT #447).
        hash_shipped = hash_selection.diff.changed if hash_selection else None,
        hash_commits = hash_selection.commits if hash_selection else None,
        # What the target is believed to hold, per changed file: the ALTER base
        # hash mode uses in place of a commit walk it cannot do.
        hash_previous = (
            hash_selection.diff.baseline.hashes if hash_selection else None
        ),
        # `-force` earns a meaning on this side of the command with ADT #366: it
        # is what lets a build rewrite a folder that has already been deployed,
        # as a refresh that keeps its logs. Since ADT #508 that refresh also
        # empties the folder's patch_scripts/, which is an input to the build
        # rather than a record of one.
        force      = args.force,
    )
    print_create_screen(workspace, config, result, records, root)


__all__ = [
    "HashOutcome",
    "PatchSelection",
    "build_database_patch",
    "build_flag_refusal",
    "dispatch_supporting_actions",
    "missing_patch_name",
    "resolve_patch_name_and_folder",
    "select_content_and_hash",
]
