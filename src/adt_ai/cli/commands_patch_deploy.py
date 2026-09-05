"""Connection-side helpers for `adtai patch -deploy`: gateways and the block.

Split out of ``commands_patch.py`` when that module reached the 20 KB context
guard (ADT #269), and split again when this one crossed it (ADT #273). The seam
is the same one twice over: everything that *renders* now lives in
``patch_deploy_render``, and what stays here opens databases. The re-exports
below keep the pre-split import path working for callers and tests that learned
it before the second split.

Split a third time by ADT #670, on a seam the command's own docstring already
drew: `patch -drop` is an ACTION beside `-archive`, not a modifier on the
deploy, and it now lives in ``commands_patch_drop``. It reads the gateway
factories back out of this module, so the dependency runs one way.
"""

from __future__ import annotations

# ruff: noqa: F401 - re-exports keep the pre-split import path working.
import argparse
import sys
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path

from adt_ai.cli.commands_patch_actions import print_patch_plan
from adt_ai.cli.constants import (
    GatewayFactory,
    PatchError,
    PatchWorkspace,
    QueryGateway,
    print_adt_header,
)
from adt_ai.cli.context import (
    _config_search_paths,
    _load_startup_context,
    _print_connection_block,
    _print_startup_debug,
    _project_relative,
    _repo_root,
)
from adt_ai.cli.gateways import build_gateway, cached_schema_gateway_factory
from adt_ai.cli.patch_deploy_render import (
    ConsoleDeployReporter,
    _print_apex_notes,
    _print_apex_scans,
    _print_deployment_errors,
    _print_still_invalid_objects,
    _print_view_mismatches,
    print_deploy_patch_contents,
    print_deployment_table,
)
from adt_ai.cli.patch_hash_mode import BASELINE_STAMP_FORMAT, print_baseline_stats
from adt_ai.cli.patch_preview_render import (
    _ignored_create_arguments,
    patch_scan_commits,
)
from adt_ai.patch.apex_import import resolve_target
from adt_ai.patch.create import install_script_name
from adt_ai.patch.hashes import (
    merge_into_baseline,
    read_baseline,
    read_patch_hashes,
    resolve_baseline_path,
)
from adt_ai.patch.models import DeploymentResult
from adt_ai.patch.selection import apex_owner_schemas
from adt_ai.shared.connections import Connection
from adt_ai.shared.diff_tables import drop_diff_tables
from adt_ai.shared.file_list import print_file_rows
from adt_ai.shared.identity import commit_account, load_identity


def run_patch_deploy(
    args: argparse.Namespace,
    *,
    root: Path,
    workspace: PatchWorkspace,
    config: dict[str, object],
    gateway_factory: GatewayFactory | None,
    ref: str | None,
) -> int:
    """Ship the patch exactly as it stands on disk.

    Moved out of `commands_patch.py` when the 2026-08-15 batch pushed that module
    past the 20 KB context guard (`tests/contracts/test_context_file_size.py`),
    the same call `#273` made for `patch_deploy_render.py` and `#285` for
    `patch_preview_render.py`: a module that crosses the guard is split, never
    registered as debt. The seam is the one this file already draws, everything
    that opens a database lives here.

    It never builds, rewrites or re-orders a patch, and never advances a hash
    rollout, so a build flag beside it is refused work, announced rather than
    silently dropped. Since ADT #350 `-create NAME` is the exception: that is how
    the name arrives, and where it also had to build the folder, the build has
    already happened by the time this is called.
    """
    if not args.target:
        print("Missing required target: use -target TARGET with -deploy", file=sys.stderr)
        print(file=sys.stderr)
        return 2
    ignored = _ignored_create_arguments(args)
    if ignored:
        print_adt_header("IGNORING WITH -deploy:", ", ".join(ignored))
        print("  -deploy deploys the existing patch unchanged")
        print("  drop -deploy to build the patch, then deploy it in a separate run")
        print()
    # Header, patch, connect, deploy, old ADT's order (`init()`:
    # show_matching_commits at patch.py:230, then create_patch's PATCH OVERVIEW
    # at :291, and only then deploy_patch opening connections at :296). Printed
    # before the gateways are built, so the listing is on screen while the
    # connections open rather than after them (ADT #273).
    print_deploy_patch_contents(
        workspace,
        config,
        args,
        root,
        ref        = ref,
        scan_limit = patch_scan_commits(config),
    )
    # What the run is about to ship, listed with no flag asked for it (ADT #353):
    # the same section `-patch` and `-create` print.
    print_patch_plan(workspace, config, ref)
    selected_gateway_factory, dev_gateway_factory, connection_provider = (
        _patch_deploy_gateway_factories(args, root, config, gateway_factory)
    )
    # The diff-table sweep travels with this call since ADT #670: its read has
    # to happen under an open connection header, so it is the block that runs
    # it. The receipt still prints between the last block and `DEPLOYING PATCH:`.
    deploy_gateway_factory = _patch_print_connection_block(
        workspace,
        config,
        ref                = ref,
        target_env         = args.target,
        gateway_factory    = selected_gateway_factory,
        dev_gateway_factory= dev_gateway_factory,
        connection_provider= connection_provider,
        debug              = args.debug,
    )
    if args.debug:
        _print_startup_debug(_load_startup_context(args))
    reporter = ConsoleDeployReporter(folder=_resolved_folder_name(workspace, config, ref))
    result = workspace.deploy_patch(
        config,
        ref                = ref,
        target_env         = args.target,
        gateway_factory    = deploy_gateway_factory,
        dev_gateway_factory= dev_gateway_factory,
        force              = args.force,
        continue_on_error  = args.continue_patch,
        reporter           = reporter,
        # `-app` (ADT #592). Resolved here rather than inside the runner so the
        # one-id refusal keeps firing where `_run_patch_command` already fires
        # it, at the top of the handler and ahead of everything a rejected flag
        # should not have to pay for.
        apex_target        = resolve_target(args.app),
        # Who a retargeted import is stamped as (ADT #682). Resolved here for
        # the reason `-app` is, and because `-config-dir` is only known at this
        # edge; `patch/apex_import.build_import_script` owns what it writes.
        apex_account       = commit_account(
            load_identity(_config_search_paths(args.config_dir, root, _repo_root())),
            root,
        ),
    )
    if not reporter.streamed:
        # A SKIPPED deploy never enters the script loop, so nothing streamed and
        # the finished table is still the report.
        print_adt_header("DEPLOYING PATCH:", reporter.folder)
        print_deployment_table(result.results, result.plan)
    _print_deployment_errors(result.results)
    _print_view_mismatches(result.view_mismatches)
    _print_still_invalid_objects(result.still_invalid)
    _print_apex_scans(result.apex_scans, root)
    _print_apex_notes(result.apex_notes)
    advance_baseline(root, config, args, workspace, ref=ref, results=result.results)
    print()
    return 1 if result.status == "ERROR" else 0


# `_print_apex_notes` lives in `patch_deploy_render.py` with every other
# deploy-result printer (`#676`). This module opens databases; formatting a
# finished result is the other one's job.


def advance_baseline(
    root: Path,
    config: dict[str, object],
    args: argparse.Namespace,
    workspace: PatchWorkspace,
    *,
    ref: str | None,
    results: list[DeploymentResult],
) -> None:
    """Record what this deploy actually landed, for a hash-built patch (ADT #447).

    Three rules, and each one is a way the baseline could otherwise start lying:

    * **Only a hash-built patch advances anything.** The marker is the folder's
      own `hashes.log`, which only `-create -hash` writes, so a commit-built
      patch is untouched. Jan, 2026-08-21: *"For normal patches you dont touch
      it. User should not be mixing these modes."*
    * **Only the files that patch shipped move**, so work done between `-create`
      and `-deploy` stays pending instead of being recorded as deployed.
    * **Only the files whose own install script SUCCEEDED.** Under `-continue` a
      run can land one schema and fail another; advancing the whole patch there
      would mark the failed schema's objects as live, and the next hash patch
      would leave them out. `install_script_name` re-derives the grouping the
      build used rather than trusting a second recorded copy of it.
    """
    try:
        folder, _plan = workspace.deployment_plan(config, ref=ref)
    except PatchError:
        return
    shipped, commits = read_patch_hashes(folder.path)
    if not shipped:
        return
    landed = {
        getattr(item, "file", "")
        for item in results
        if getattr(item, "status", "") == "SUCCESS"
    }
    # Hoisted out of the comprehension: the lookup is a sqlite read, and this
    # runs once per file the patch shipped.
    owners = apex_owner_schemas(root)
    advancing = {
        file: value
        for file, value in shipped.items()
        if install_script_name(file, config, owners) in landed
    }
    if not advancing:
        return
    path = resolve_baseline_path(root, config, args.target or "-", None)
    _written, advanced = merge_into_baseline(
        path,
        advancing,
        {file: number for file, number in commits.items() if file in advancing},
        target_env = args.target or "-",
        stamp      = datetime.now().strftime(BASELINE_STAMP_FORMAT),
    )
    total = len(read_baseline(path))
    print_adt_header("UPDATING BASELINE:")
    # One row, and flat: a baseline file is named rather than listed, so there is
    # nothing for a folder line to group (ADT #504). Through the shared renderer
    # all the same, so the dash and the indent have one home.
    print_file_rows([_project_relative(path, root)], nested=False)
    # `ADVANCED` is the files whose hash actually moved; everything else in the
    # baseline is untouched by design, and saying so is what makes the number
    # readable rather than a riddle beside the header (`#453`).
    print_baseline_stats(
        {"ADVANCED": advanced, "UNCHANGED": total - advanced, "TOTAL": total},
        total = total,
    )


def _resolved_folder_name(
    workspace: PatchWorkspace,
    config: dict[str, object],
    ref: str | None,
) -> str:
    """The patch folder ``ref`` resolved to, for the `DEPLOYING PATCH:` header.

    Empty when the lookup fails, which is the same lookup `deploy_patch` is about
    to raise on with the message written for it (ADT #443). A header append is
    never worth pre-empting that error.
    """
    try:
        folder, _plan = workspace.deployment_plan(config, ref=ref)
    except PatchError:
        return ""
    return folder.folder


def _sweep_diff_tables(
    gateway_factory: GatewayFactory,
    schema: str,
    dropped: list[str],
) -> None:
    """Drop SQLcl DIFF leftovers in one schema, collecting what went (ADT #356).

    Old ADT dropped these on DEV and never on the target
    (`ADT--OLD/patch.py:1684`), so the source gateway is used where the
    environments differ and the target's own only where they are the same schema
    anyway. `-create` opens no connection at all, so "the next patch run" is in
    practice this one.

    **The read happens, the printing does not** (ADT #670). This ran as a whole
    section after the connection blocks, which put a `SELECT` on `user_tables`
    behind a section that had already closed, and `AnnouncedGateway.guard`
    records exactly that. It is now called from inside the connection block,
    between the header and the version rows, and its caller prints the receipt
    afterwards -- `export_db`'s own split for the identical sweep, one module
    over. Anything printed from here would land between a header and its body.

    Every dropped table is still named by that caller: this is a `DROP TABLE`
    inside a deploy, and a project that legitimately owns a `%$1` table must not
    lose it in silence.

    ``dropped`` is appended to rather than returned so the whole call is a
    ``partial`` the connection block can run as its ``before_versions`` hook,
    with no closure over a loop variable to get wrong.
    """
    dropped.extend(drop_diff_tables(gateway_factory(schema)))


def _print_dropped_diff_tables(dropped: list[str]) -> None:
    """The sweep's receipt, once for the run rather than once per schema.

    One section for every schema swept (ADT #670). The header used to print
    inside the per-schema loop, so a two-schema patch with leftovers in both
    drew `DROPPING DIFF TABLES:` twice; nothing separates the two lists but the
    order they were read in, which the rows do not carry anyway.
    """
    if not dropped:
        return
    print_adt_header("DROPPING DIFF TABLES:")
    # Flat, and not a file list: these are TABLE names (ADT #504).
    print_file_rows(dropped, nested=False)
    print()


def _patch_deploy_gateway_factories(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, object],
    gateway_factory: GatewayFactory | None,
) -> tuple[GatewayFactory, GatewayFactory | None, Callable[[str], object | None]]:
    debug = getattr(args, "debug", False)
    if gateway_factory:
        # `cli/gateways.py` owns the wrap (ADT #670). This built
        # `DebugQueryGateway(gateway) if debug else gateway` by hand over a
        # gateway `cli.runtime.main` had already guarded, which puts the logger
        # OUTSIDE `AnnouncedGateway`: the logger calls `mark_announced()` before
        # every statement, so a `-debug` deploy could never report a console
        # violation whatever it was showing.
        return (
            cached_schema_gateway_factory(gateway_factory, debug=debug),
            None,
            lambda _schema: None,
        )

    startup = _load_startup_context(args)
    connections = startup.connections
    target_env = args.target or connections.default_environment
    source_env = str(config.get("patch_source_env") or config.get("source_env") or "DEV")

    def target_connection(schema: str) -> Connection:
        return connections.resolve(environment=target_env, schema=schema)

    def target_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, target_connection(schema), debug=debug, project_root=root)

    if source_env.upper() == target_env.upper():
        return target_gateway_factory, None, target_connection

    def source_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(
            startup,
            connections.resolve(environment=source_env, schema=schema),
            debug        = debug,
            project_root = root,
        )

    return target_gateway_factory, source_gateway_factory, target_connection


def _patch_print_connection_block(
    workspace: PatchWorkspace,
    config: dict[str, object],
    *,
    ref: str | None,
    target_env: str,
    gateway_factory: GatewayFactory,
    dev_gateway_factory: GatewayFactory | None = None,
    connection_provider: Callable[[str], object | None],
    debug: bool,
) -> GatewayFactory:
    """Print the standard CONNECTING TO block per deploy-plan schema.

    Returns a caching wrapper around ``gateway_factory`` so the version probe and
    the subsequent ``deploy_patch`` reuse the same gateway per schema (one
    connection). The cache comes from `cli/gateways.py` rather than a local
    `dict` (ADT #670), which is the same move that took the `-debug` wrap out of
    this module: a schema's gateway is built, wrapped and reused as one decision.

    **The diff-table sweep runs inside this block, and reports after it** (ADT
    #670). It stood alone after the connection blocks, where the section it
    followed had already closed, so its `user_tables` read was a blocking call
    behind a finished screen. The read now happens between the header and the
    version rows -- the one place in the section where a header is on screen with
    no body under it -- and the receipt prints once the last block has closed, so
    the console reads exactly as it did: connection blocks, then `DROPPING DIFF
    TABLES:` if anything went, then `DEPLOYING PATCH:`.

    ``dev_gateway_factory`` is the source-environment factory when it differs
    from the target, which is the gateway old ADT swept on; without one the
    target's own cached gateway is swept, the two being the same schema anyway.
    """
    cached_factory = cached_schema_gateway_factory(gateway_factory)
    sweep_factory = dev_gateway_factory or cached_factory
    try:
        _folder, plan = workspace.deployment_plan(config, ref=ref)
    except PatchError:
        # Let deploy_patch surface the same error; just skip the connection
        # block. The sweep goes with it rather than raising a cleanup error over
        # the message `deploy_patch` was written to give.
        return cached_factory

    dropped: list[str] = []
    for schema in sorted({item.schema for item in plan}):
        _print_connection_block(
            cached_factory(schema),
            connection_provider(schema),
            schema          = schema,
            environment     = target_env,
            debug           = debug,
            before_versions = partial(_sweep_diff_tables, sweep_factory, schema, dropped),
        )
    _print_dropped_diff_tables(dropped)
    return cached_factory


__all__ = [name for name in globals() if not name.startswith("__")]
