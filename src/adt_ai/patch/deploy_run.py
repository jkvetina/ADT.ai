"""The loop that actually runs a patch folder's install scripts.

Split out of ``runner.py`` when ADT #434 pushed that module past the 20 KB
context guard. The seam is one the ``patch`` package already draws around this
work: ``deploy.py`` owns the payload, the log and the transcript parsing,
``deploy_progress.py`` owns the counting, and the loop that drives both was the
only piece still living on ``PatchWorkspace``. A module that crosses the guard is
split, never registered as debt.

``PatchWorkspace.deploy_patch`` stays as the entry point and delegates here, so
no caller and no test learns a new name.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adt_ai.patch import settings
from adt_ai.patch.apex_deploy import ApexImportItem, prepare_apex_imports, run_apex_imports
from adt_ai.patch.apex_import import ApexTarget
from adt_ai.patch.apex_scan import scanned_app_ids, verify_applications
from adt_ai.patch.deploy import (
    _deployment_error_excerpt,
    _deployment_payload,
    _deployment_succeeded,
    _invalid_objects,
    _not_deployed_result,
    _recompile_invalid_objects,
    _skipped_deployment_result,
    _verify_view_columns,
    _write_deployment_log,
    reset_deployment_spool,
)
from adt_ai.patch.deploy_progress import (
    DeploymentProgressReader,
    _countable_references,
    _deployment_progress,
)
from adt_ai.patch.deploy_receipt import (
    _include_closure,
    deployment_complete,
    deployment_fingerprint,
    write_deploy_receipt,
)
from adt_ai.patch.layout import deploy_log_folder, ensure_deploy_log_folder
from adt_ai.patch.models import DeploymentPlanItem, DeploymentResult, DeploymentRunResult
from adt_ai.patch.templates import _apex_environment_payload
from adt_ai.shared.sqlcl_errors import SqlclNotConnectedError, SqlclScriptError, SqlclTimeoutError


def _deploy_progress_reader(
    allowed: frozenset[str],
    total: int,
    reporter: Any,
) -> DeploymentProgressReader | None:
    """A line reader for this script, or ``None`` when nobody is watching.

    ``None`` is the load-bearing half (ADT #434). ``sqlcl_request`` only moves the
    child onto a pty when it is given a reader, so a reporter with no ``advance``
    hook, a caller that passed no reporter at all, and a script with nothing
    countable in it each keep the plain ``subprocess.run`` transport the deploy
    has always used. The pty is bought only where its output is actually
    rendered.
    """
    advance = getattr(reporter, "advance", None)
    if advance is None or not allowed:
        return None
    return DeploymentProgressReader(allowed, total, advance)


def run_deployment(
    workspace: Any,
    config: dict[str, Any],
    *,
    ref: str | None,
    target_env: str,
    gateway_factory: Callable[[str], Any],
    dev_gateway_factory: Callable[[str], Any] | None = None,
    force: bool = False,
    continue_on_error: bool = False,
    reporter: Any = None,
    apex_target: ApexTarget | None = None,
    apex_version: str | None = None,
    apex_account: str = "",
) -> DeploymentRunResult:
    """Deploy every script in the patch folder, reporting progress as it goes.

    ``reporter`` is the console's hook into the loop (ADT #273). Without it the
    whole run was silent until it finished and the caller printed the finished
    table, so a 42-file deploy looked stuck on the connection block for its
    entire duration. It is optional and duck-typed, ``begin_deploy(plan)`` before
    the first script, ``begin_script(item)`` before each blocking
    ``sqlcl_request``, ``end_script(result)`` after it, ``end_deploy(results)`` at
    the close, so every existing caller and test keeps working unchanged.
    ADT #434 adds one more in the same shape, ``advance(deployed, total)`` while a
    script is still running, and looks for it the same optional way.

    ``apex_target`` is `-app` (ADT #592), and ``None`` is every run that did not
    pass it: no APEXlang tree is staged, no signature is read, and the loop below
    is byte for byte the deploy it has always been. When it IS passed, the
    application trees are staged and gated BEFORE the first install script and
    imported after the last, so a refusal costs no database write at all.

    ``apex_version`` is the release the connection block already probed, handed
    down rather than probed again, and it gates the post-deploy scan (ADT #676).

    ``apex_account`` travels the same way and stamps a retargeted import as the
    developer who deployed it (ADT #682); it reaches the run from the CLI edge
    because that is where `-config-dir` is known.
    """
    folder, plan = workspace.deployment_plan(config, ref=ref)
    target = target_env.upper()
    log_folder = folder.path / deploy_log_folder(config, target)
    fingerprint = deployment_fingerprint(
        workspace.root, folder.path, plan, config, apex_target, apex_version, apex_account
    )
    if deployment_complete(log_folder, target, fingerprint) and not force:
        return DeploymentRunResult(
            folder          = folder,
            plan            = plan,
            results         = [_skipped_deployment_result(item) for item in plan],
            status          = "SKIPPED",
            view_mismatches = [],
            recompiled      = [],
        )

    # Before the first script runs, never after: `_write_deployment_log`
    # also creates this folder, but only once SQLcl has already failed to
    # open its spool. Git does not track an empty directory, so a patch
    # folder that arrives by clone has none even when `-create` seeded one
    # (ADT #270).
    ensure_deploy_log_folder(folder.path, config, target)
    write_deploy_receipt(log_folder, target, fingerprint, "INCOMPLETE")
    gateways = {
        schema: gateway_factory(schema)
        for schema in sorted({item.schema for item in plan})
    }
    # Before `begin_deploy`, so the streamed table sizes itself on every row it
    # is going to show, and before the first script, so a refused signature check
    # leaves the database untouched (ADT #592).
    apex_items, apex_notes = prepare_apex_imports(
        workspace.root,
        config,
        sorted({item.app_id for item in plan if item.app_id}),
        list(folder.files),
        apex_target,
        gateway_factory,
        force = force,
    )
    for imported in apex_items:
        if imported.schema not in gateways:
            gateways[imported.schema] = gateway_factory(imported.schema)
    results: list[DeploymentResult] = []
    if reporter is not None:
        reporter.begin_deploy(
            [
                *plan,
                *(
                    item.plan_item(len(plan) + offset, len(folder.commits))
                    for offset, item in enumerate(apex_items, start=1)
                ),
            ]
        )
    for index, item in enumerate(plan):
        if reporter is not None:
            reporter.begin_script(item)
        started_at = time.monotonic()
        reset_deployment_spool(config, folder.path, target, item.file)
        # Read BEFORE the call, not after (ADT #434). These are the same two
        # numbers the result below records; a live reader needs them while the
        # script is running, and reading the install script twice to get them at
        # two different times is how the two answers start to differ.
        deployed_total, allowed = _countable_references(
            item.path.read_text(encoding="utf-8", errors="replace"),
            workspace.root,
            folder.path,
            config,
        )
        # Passed only when there is a reader, the convention `OracleGateway._run`
        # already applies to `oci` for the same reason: a gateway that never
        # serves a live console is called with the exact arguments it always was.
        reader = _deploy_progress_reader(allowed, deployed_total, reporter)
        execution_failed = False
        try:
            output = gateways[item.schema].sqlcl_request(
                _deployment_payload(item.path, continue_on_error=continue_on_error),
                folder.path,
                **({"on_line": reader} if reader is not None else {}),
            )
        except (SqlclScriptError, SqlclNotConnectedError, SqlclTimeoutError) as error:
            execution_failed = True
            output = str(error)
        # Measured around the SQLcl call alone, the log write and the
        # transcript parsing below are ADT.ai's own bookkeeping, and folding
        # them in would report the tool's overhead as the script's cost.
        seconds = time.monotonic() - started_at
        status = "SUCCESS" if not execution_failed and _deployment_succeeded(output) else "ERROR"
        log_path = _write_deployment_log(
            config, folder.path, target, item.file, status, output,
            retain_output=execution_failed,
        )
        result = DeploymentResult(
            order   = item.order,
            file    = item.file,
            schema  = item.schema,
            app_id  = _installed_app_id(item, apex_items, workspace.root, config),
            files   = item.files,
            commits = item.commits,
            status  = status,
            log_path= log_path,
            # Still read off the transcript the run kept, never off the live
            # counter beside it: what a deploy REPORTS is a property of its own
            # log, and a display counter that drifted would take the record with
            # it. The two agree by construction, both filter on `allowed`.
            deployed= _deployment_progress(output, allowed),
            deployed_total = deployed_total,
            error_excerpt  = (
                tuple(_deployment_error_excerpt(output)) if status == "ERROR" else ()
            ),
            seconds = seconds,
        )
        results.append(result)
        if reporter is not None:
            reporter.end_script(result)
        if status == "ERROR" and not continue_on_error:
            # Report the scripts the run never reached instead of dropping them
            # from the table entirely (ADT #254).
            unrun = [_not_deployed_result(other) for other in plan[index + 1 :]]
            results.extend(unrun)
            if reporter is not None:
                # They still belong in the streamed table: a run that died on
                # script 1 of 3 has to show the two it never reached, or the
                # table reads as a one-script patch that failed.
                for skipped in unrun:
                    reporter.end_script(skipped)
            break
    # After the scripts, because a tree imports onto the objects it queries, and
    # only when they all landed: importing an application over a half-deployed
    # schema builds pages against columns that are not there yet. A run stopped
    # by an error reports the import as NOT DEPLOYED through the plan row rather
    # than silently dropping it (`#254`'s rule, one step out).
    apex_plan = [
        item.plan_item(len(plan) + offset, len(folder.commits))
        for offset, item in enumerate(apex_items, start=1)
    ]
    if apex_items and not any(result.status == "ERROR" for result in results):
        results.extend(
            run_apex_imports(
                apex_items,
                workspace.root,
                gateway_factory,
                lambda file, status, output: _write_deployment_log(
                    config, folder.path, target, file, status, output
                ),
                order_from = len(plan),
                commits    = len(folder.commits),
                force      = force,
                reporter   = reporter,
                account    = apex_account,
            )
        )
    elif apex_items:
        unrun = [_not_deployed_result(item) for item in apex_plan]
        results.extend(unrun)
        if reporter is not None:
            for skipped in unrun:
                reporter.end_script(skipped)
    # **The deploy section closes after the post-deploy reads, not before
    # them** (`#372`). Recompiling whatever the scripts invalidated and
    # checking the view columns print no row of their own, so closing the
    # table first left both running under a blank line; leaving it open one
    # moment longer puts them under `DEPLOYING PATCH:`, which is the header
    # they belong to anyway, and costs no new string. `#360` answered this
    # with `FINISHING THE DEPLOY:`, `RECOMPILING INVALID OBJECTS` and
    # `VERIFYING VIEW COLUMNS`; Jan deleted all three.
    recompiled = _recompile_invalid_objects(gateways)
    # **What the pass FIXED is a second read, never the worklist** (`#658`). A
    # COMPILE that fails leaves the object invalid and raises nothing, so the
    # only honest answer comes from asking `USER_OBJECTS` again. Skipped when
    # nothing was invalid, which is the ordinary case and costs a round trip.
    still_invalid = _invalid_objects(gateways) if recompiled else []
    view_mismatches = (
        _verify_view_columns(folder.files, config, gateways, dev_gateway_factory)
        if dev_gateway_factory
        else []
    )
    # **The application is asked whether its own SQL still parses** (`#676`). An
    # import that reported SUCCESS proves the tree landed, never that a region
    # query still resolves against the schema it landed on, and the gap between
    # those two facts is where a home page full of `ORA-00904` lived. One pass
    # over the applications this run actually deployed, both flavours together:
    # the per-app install script and the APEXlang import each carry `app_id` on
    # their result row, so an application touched by both is scanned once.
    apex_scans = (
        verify_applications(
            scanned_app_ids(results),
            _gateway_for_app(results, gateways),
            apex_version = apex_version,
            log_folder   = folder.path / deploy_log_folder(config, target),
            config       = config,
        )
        if settings.verify_deploy_scan(config)
        else []
    )
    if reporter is not None:
        reporter.end_deploy(results)
    # A findings row fails the run (Jan, 2026-09-02). Unlike `still_invalid`
    # beside it, this IS patch-scoped -- the scan reads one application, the one
    # this patch just deployed -- so it cannot fail a deploy over somebody
    # else's month-old debt, which is the reason that one deliberately does not.
    status = (
        "ERROR"
        if any(result.status == "ERROR" for result in results)
        or any(report.failed for report in apex_scans)
        else "SUCCESS"
    )
    write_deploy_receipt(log_folder, target, fingerprint, status)
    return DeploymentRunResult(
        folder          = folder,
        # The APEX rows belong to the plan, not beside it: the finished table is
        # rendered from `plan` when nothing streamed, so a plan short of them
        # would print an import that ran and a table that does not mention it.
        plan            = [*plan, *apex_plan],
        results         = results,
        status          = status,
        view_mismatches = view_mismatches,
        recompiled      = recompiled,
        still_invalid   = still_invalid,
        apex_notes      = apex_notes,
        apex_scans      = apex_scans,
    )


def _gateway_for_app(
    results: list[DeploymentResult],
    gateways: dict[str, Any],
) -> Callable[[int], Any]:
    """The already-open connection to scan each application on.

    The schema that deployed it, resolved out of the gateways the run opened at
    the top rather than through the factory: `test_runner_deploy` pins that a
    deploy connects once per schema and then stops connecting, and a
    verification step is not a reason to open a second one. The first result row
    wins if two ever name the same application, and an application whose schema
    somehow has no gateway falls back to the only one the run used.
    """
    owners = {
        app_id: result.schema
        for result in reversed(results)
        if (app_id := result.app_id) is not None
    }
    fallback = next(iter(gateways.values()), None)
    return lambda app_id: gateways.get(owners.get(app_id, ""), fallback)


def _installed_app_id(
    item: DeploymentPlanItem, imports: list[ApexImportItem], root: Path, config: dict[str, Any],
) -> int | None:
    """A tree-only carrier does not install its source application.

    Counts cannot prove this: templates and inline SQL can write an application
    without contributing a countable file. Only an inert SQLcl carrier, after
    removing the known workspace-selection block, can omit the source scan.
    Unknown SQL remains subject to verification of both source and target.
    """
    if item.app_id is None or not any(
        imported.app_id == item.app_id and imported.retargeted for imported in imports
    ):
        return item.app_id
    paths = {item.path.resolve()}
    if not _include_closure(paths, item.path.parent, config):
        return item.app_id
    environment = "\n".join(_apex_environment_payload(root, item.app_id)).strip()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        if environment:
            text = text.replace(environment, "")
        for line in text.splitlines():
            value = line.strip().upper()
            if value and not value.startswith(
                ("--", "PROMPT ", "SET ", "WHENEVER ", "SPOOL ", "@", "START ")
            ):
                return item.app_id
    return None


__all__ = ["run_deployment"]
