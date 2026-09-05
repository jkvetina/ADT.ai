from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from adt_ai.cli.commands_patch_deploy import run_patch_deploy
from adt_ai.cli.constants import (
    ConfigLoader,
    GatewayFactory,
    PatchError,
    PatchRunner,
    PatchWorkspace,
    print_adt_header,
    print_module_banner,
)
from adt_ai.cli.context import (
    _config_search_paths,
    _repo_root,
)
from adt_ai.cli.patch_build import (
    build_database_patch,
    build_flag_refusal,
    dispatch_supporting_actions,
    missing_patch_name,
    resolve_patch_name_and_folder,
    select_content_and_hash,
)
from adt_ai.cli.patch_dependency_refresh import ensure_fresh_dependency_graph
from adt_ai.cli.patch_hash_mode import run_baseline
from adt_ai.cli.patch_inputs import (
    build_patch_request,
    ensure_commit_store,
    searching_without_narrowing,
)
from adt_ai.cli.patch_no_commits import answer_without_commits
from adt_ai.cli.patch_preview_render import print_patch_preview
from adt_ai.patch.apex_import import resolve_target
from adt_ai.patch.topup import ConsoleTopUpReporter
from adt_ai.shared import text_files
from adt_ai.shared.git_files import fetch_origin, git_ref_exists


def _run_patch(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_module_banner("PATCH")
    try:
        return _run_patch_command(args, gateway_factory)
    except PatchError as error:
        if args.debug:
            raise
        print_adt_header("PATCH FAILED:")
        print(str(error))
        return 1


def _refuse(message: str) -> int:
    """An argument-shaped refusal: the message on stderr, then the exit code.

    One spelling for all three of them (ADT #670). Each used to print its own
    two lines at its own call site, which is how the trailing blank every
    refusal owes the console became a property three separate blocks had to
    remember individually.
    """
    print(message, file=sys.stderr)
    print(file=sys.stderr)
    return 2


def _level_history(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
) -> str | None:
    """Refresh refs, validate `-branch`, and level that branch's commit store.

    Returns the branch whose store was levelled, or ``None`` when the top-up
    could not run at all; the commit scan reads that to decide whether it still
    owes a walk of its own.

    Kept in this module rather than moved to `patch_build.py` with the rest of
    the steps (ADT #670): `tests/cli/test_patch_fetch_branch.py` reaches
    `commands_patch.fetch_origin` to prove the fetch happens under `-head` and
    not without it, and a refactor that silently relocates a monkeypatch target
    turns a real regression test into a passing no-op.

    The fetch refreshes remote-tracking refs BEFORE anything reads history;
    after it, it would change nothing the run can see, which is the dead flag it
    replaces (ADT #309, was #275). Old ADT called `fetch_changes()` at the same
    point (patch.py:182-183). Offline failures stay non-fatal: the shared helper
    swallows them and the run falls back to local refs. It moved ahead of the
    store top-up with `#367`, because a top-up that ran first would level the
    store against refs the fetch was about to move.

    `-head` asks for it since ADT #598, the separate flag having been withdrawn.
    Jan, 2026-08-30: *"When I ask for -head, you will do the -fetch first,
    obviously, then use HEAD commits!"* A run wanting the newest version of
    every file is the run that wants the newest refs.

    `-branch NAME` selects which history the run walks, so a name that resolves
    to nothing fails loudly rather than silently falling back to HEAD, which
    would hand back a patch built from the wrong branch.

    The store is levelled before ANY action, including the three that return
    without ever reaching the commit scan. `patch` reads commit NUMBERS out of
    the branch store and writes them into a patch folder, so a store short of
    `HEAD` hands out a window that disagrees with the repository the operator is
    reading, and `-commit 41` means one thing on screen and another on disk
    (ADT #367). Jan, 2026-08-15: *"before running anything it must check that
    commits .db for requested branch is up to date"*.
    """
    if args.head:
        fetch_origin(root)
    if args.branch and not git_ref_exists(root, args.branch):
        raise PatchError(
            f'BRANCH "{args.branch}" NOT FOUND - check the name, or fetch the '
            "remote first if the branch only exists there"
        )
    return ensure_commit_store(args, root, config)


def _run_patch_command(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None,
) -> int:
    """The ORDER a `patch` run happens in. Each step's reasoning is its own.

    Refactored to this shape by ADT #670: at 340 lines the function carried
    every step's justification inline, so the sequence it exists to express was
    the one thing in it a reader could not see. The steps live in
    `patch_build.py` and ANSWER rather than exit, which is what keeps every
    return in this function visible at once.
    """
    # `-app`'s one-id refusal fires HERE, ahead of the config load and the commit
    # scan, because a rejected flag must cost nothing. Landed inside
    # `build_patch_request` first, where the run had already paid for a full
    # commit rebuild (929 commits, about 90s on this repo) before the message
    # reached the screen, which is the argparse-shaped defect §Console output
    # contract means by validating inside the handler after the banner.
    try:
        resolve_target(args.app)
    except ValueError as error:
        raise PatchError(str(error)) from error
    root = Path(args.root).expanduser().resolve()
    cached_config: dict[str, Any] | None = None

    def patch_config() -> dict[str, Any]:
        nonlocal cached_config
        if cached_config is None:
            cached_config = ConfigLoader(
                _config_search_paths(args.config_dir, root, _repo_root())
            ).load().data
        return cached_config

    # Applied before ANY artifact is written. The line ending is process-wide
    # state, so a write landing before `file_crlf` is applied silently takes the
    # LF default (`tests/contracts/test_text_write_newline.py`, card #193).
    #
    # `text_files.apply_config` directly, not `_load_startup_context`, which is
    # the fuller helper that also resolves connections and wallets. `patch`
    # writes its artifacts on the paths that open no database at all (`-create`
    # is documented as connecting to nothing), so demanding a connection file
    # before a build would refuse the exact runs that need no connection.
    #
    # This module used to satisfy that contract through a `_load_startup_context`
    # call sitting inside the deploy block's `if args.debug:` branch, which is a
    # static-check pass and a real-behaviour miss: `-create` and `-install` wrote
    # before any config was applied, on every non-debug run. The #350 split moved
    # that call out and turned the check red, which is the guard finding the gap
    # it was written for.
    text_files.apply_config(patch_config())
    # After the config load, never before: `patch_root` is the project's own
    # answer since ADT #430, so a workspace minted early looks in the wrong folder.
    workspace = PatchWorkspace(root, patch_config())
    scanned_branch = _level_history(args, root, patch_config())
    dispatched = dispatch_supporting_actions(
        args,
        root            = root,
        workspace       = workspace,
        config          = patch_config(),
        gateway_factory = gateway_factory,
    )
    if dispatched is not None:
        return dispatched
    patch_ref: str | None = args.name or None
    unnamed = missing_patch_name(args, patch_ref)
    if unnamed is not None:
        return _refuse(unnamed)
    selection = resolve_patch_name_and_folder(args, workspace, patch_config(), patch_ref)

    def run_deploy() -> int:
        return run_patch_deploy(
            args,
            root            = root,
            workspace       = workspace,
            config          = patch_config(),
            gateway_factory = gateway_factory,
            ref             = patch_ref,
        )

    # Deploy-only: nothing to build, so the commit scan below is skipped
    # entirely. A run that also builds falls through and deploys at the end.
    if args.deploy and not selection.create_requested:
        return run_deploy()
    incoherent = build_flag_refusal(args)
    if incoherent is not None:
        return _refuse(incoherent)
    # ADT #570's gate, ASKED here rather than after the commit scan (ADT #670).
    # It reads nothing but `args`, so the answer is the same in both places, and
    # the refresh below opens a connection and rewrites the schema's dependency
    # mirror. A discovery run doing that is precisely the "yet" Jan was
    # declining: *"I am looking for correct commits, I dont want patch yet."*
    # The FLIP still happens where `#570` put it, because what it selects there
    # is the screen the run falls through to.
    discovery = searching_without_narrowing(args)
    if selection.create_requested and not discovery:
        # Same gate as -install, and for the same reason: a patch built from a
        # graph that predates the objects it orders fails in the target
        # database. Checked before the commit scan and the hash rollout write,
        # so a refusal leaves nothing behind.
        #
        # Since ADT #367 it ENSURES rather than only refuses: a stale scope is
        # refreshed for the schemas that are actually behind, and the refusal is
        # what a run that still cannot produce a usable graph lands on.
        ensure_fresh_dependency_graph(args, root, patch_config(), gateway_factory)
    # `IGNORING WITHOUT -deploy:` stood here until ADT #443 (added by #309, from
    # #292 §2c). Jan, 2026-08-21: "remove this block, I did not asked for it". An
    # ADT.ai invention with no old-ADT equivalent, firing on the ordinary case of
    # appending a deploy's flags to a build command. `IGNORING WITH -deploy:`
    # stays: build flags a deploy will NOT act on are a real surprise. `-force` is
    # not dropped here either way, since #366 it steers the rewrite below.
    #
    # A read-only run degrades instead of refusing (ADT #352). Bare `patch` now
    # also lists the patch folders, and that answer does not depend on git at
    # all, so a project that is not a checkout, or has no commits yet, must still
    # get its listing rather than a `PATCH FAILED` where the tables belong. A
    # build still fails loudly: `-create` has nothing to build from.
    request = build_patch_request(
        args,
        root,
        patch_config(),
        selected_folder = selection.selected_folder,
        patch_ref       = patch_ref,
    )
    try:
        records, window = PatchRunner().run_window(
            request,
            reporter = ConsoleTopUpReporter(root, request.branch),
            # Already levelled at the top of the run (ADT #367). Walking the
            # branch a second time would cost the same bounded scan and print a
            # second bar for a store that cannot have moved since.
            top_up   = scanned_branch is None,
        )
    except (subprocess.CalledProcessError, OSError):
        if selection.create_requested:
            raise
        records, window = [], []
    # `-baseline` reads the commit records this run already scanned to fill its
    # commit column, so it sits after the scan; it builds nothing, needs no
    # patch name and opens no database, so it returns before everything below.
    if args.baseline is not None:
        return run_baseline(args, root, patch_config(), records)
    hashed = select_content_and_hash(
        args,
        root,
        patch_config(),
        records,
        create_requested = selection.create_requested,
    )
    records = hashed.records
    if hashed.stop:
        return 0
    if patch_ref and not records:
        # Three answers to one question, in `patch_no_commits.py` since ADT #467.
        return answer_without_commits(
            workspace,
            patch_config(),
            request,
            records,
            patch_ref,
            selection.selected_folder,
            selection.create_requested,
        )
    if selection.create_requested and discovery:
        # `-search` is the flag for FINDING the commits, so a `-create` beside it
        # is a build nobody has chosen the contents of yet (ADT #570). Jan,
        # 2026-08-27, after `-create -search %` rewrote the folder he had built
        # the night before: *"When -search "%" is passed, I am looking for
        # correct commits, I dont want patch yet."*
        #
        # A preview rather than a refusal, because the commit list IS the answer
        # he asked for, and it writes nothing at all, so the previous build, its
        # `patch_scripts/` and its snapshots survive the run. Scoped to `-search`
        # on his call: a bare `-create` builds exactly as it always has.
        #
        # It says nothing on screen, also his call, 2026-08-27, asked because a
        # new console string is a redesign rather than a bug fix (SOP §Console
        # output contract): the screen this falls through to is the commit list
        # the run was asking for, so there is no gap for a label to fill.
        selection = selection.previewing()
    # `patch_ref is not None` is already true wherever `create_requested` is:
    # a `-create` without a name returned 2 at the top of the run. It is the
    # name the build is FOR, so it is the half spelled out here.
    if selection.create_requested and patch_ref is not None:
        build_database_patch(
            args,
            workspace,
            patch_config(),
            selection,
            patch_ref      = patch_ref,
            records        = records,
            window         = window,
            hash_selection = hashed.selection,
            root           = root,
        )
        # `-create -deploy` on a name with no folder behind it: the build just
        # happened, so the deploy ships what this run produced.
        if args.deploy:
            return run_deploy()
        return 0
    return print_patch_preview(
        workspace,
        patch_config(),
        args,
        records,
        patch_ref,
        selection.folder_name,
        # `CHANGED FILES:` is hash mode's own listing, so a commit table under it
        # says the same thing twice or nothing at all (ADT #447).
        commit_table = hashed.selection is None,
        # The same two filters the commit scan ran, so `PATCH FOLDERS:` narrows
        # with the screen it sits on rather than listing every patch on disk
        # under a filtered heading (ADT #467). Taken off the request, which
        # already resolved `-my` and the bare `-recent` sentinel.
        authors      = request.authors,
        recent       = request.recent,
    )


__all__ = [name for name in globals() if not name.startswith("__")]
