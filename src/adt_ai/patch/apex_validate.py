"""The APEXlang compiler, asked before a patch is built or deployed (ADT #964).

Jan's `patch -create -app <id> -target PLAYGROUND -deploy` ran `init.sql`, then
`> BUILDING APP` failed on an `INVALID_PROPERTY` in a page and a
`COMPONENT_NOT_FOUND` in `lists.apx`, both of which the compiler behind `adtai
validate` reports without a database: *"It should be validated during patch
creation and before you deploy. If the app is throwing the error after
deployment, it should have been prevented before!"*

So `-create` compiles the trees of the APEXlang applications it ships before it
writes the folder, and `-deploy -app` compiles the trees it imports before the
first install script and before the build-status lock writes a status. Both
refuse on a failing tree, Jan's call. Both go through `validate`'s own runner,
parser and connectionless SQLcl, so the three commands cannot disagree about one
tree, and `-create` still opens no connection for it.

**The tree is made ready first, the way the import reads it** (`precheck_trees`):
payloads linked, CRLF converted, a missing static file refused. `-create` and
`-deploy` run the same steps, so the bytes the build compiled are the bytes the
deploy imports.

**Once per run.** `-create -deploy` compiles each tree once: `ApexlangValidation`
remembers what passed, and a JVM start costs ~4.5s per tree.

**Optional below the CLI.** A caller that passes no gate compiles nothing, the
convention `locks` and `signature_gateway_factory` already follow: every test of
the other refusals builds a tree SQLcl never sees. The CLI always passes one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.patch.layout import apex_app_id, is_apexlang_path
from adt_ai.patch.models import PatchError
from adt_ai.shared.apex_payloads import link_payloads
from adt_ai.shared.apexlang_line_endings import PrecheckIssue, convert_crlf, crlf_issue
from adt_ai.shared.apexlang_static_refs import missing_refusal, missing_static_files
from adt_ai.validate.files import ValidateTarget, resolve_targets
from adt_ai.validate.runner import (
    FolderOutcome,
    SqlclRequest,
    ValidateReporter,
    ValidateRequest,
    ValidateRunner,
)

# What the refusal tells the developer to do again once the tree compiles.
RETRY_CREATE = "create the patch"
RETRY_DEPLOY = "deploy"

NO_APPLICATION_ID = "APEX IMPORT TARGET HAS NO APPLICATION ID"


@dataclass
class ApexlangValidation:
    """One run's compile gate: how SQLcl is reached, who watches, what passed.

    ``sqlcl_request`` is the CLI's own `run_sqlcl_script` global, handed down so
    the facade that swaps SQLcl for `validate`'s tests swaps it here too; `None`
    is `validate`'s own default. ``reporter`` streams the rows. ``passed`` holds
    the applications this run already compiled clean.
    """

    sqlcl_request : SqlclRequest | None = None
    reporter      : ValidateReporter | None = None
    passed        : set[int] = field(default_factory=set)
    #: What `check_deploy_trees` converted ahead of the connection block (ADT
    #: #966), handed to the deploy that prints its notes, since the trees it
    #: prechecks again are already converted and would report nothing.
    issues        : list[PrecheckIssue] = field(default_factory=list)


class ApexlangValidationError(PatchError):
    """The compiler refused a tree. Carries its reports, for the console to render.

    A `PatchError`, so a caller that stops on one stops on this; the CLI catches
    it first and prints the compiler's own lines instead of this message.
    """

    def __init__(self, failed: Sequence[FolderOutcome], retry: str) -> None:
        self.failed = tuple(failed)
        self.retry = retry
        ids = [str(outcome.target.app_id) for outcome in self.failed]
        # No `Run: adtai validate ...` way out since ADT #966, Jan: *"MAKE NO
        # SENSE HERE, remove it"*. The screen already lists what to fix.
        super().__init__(f"APP {', '.join(ids)} FAILED VALIDATION")


def check_patch_trees(
    root       : Path,
    config     : dict[str, Any],
    files      : Sequence[str],
    validation : ApexlangValidation | None,
) -> list[PrecheckIssue]:
    """`-create`'s gate: every APEXlang application ``files`` ships, compiled.

    Resolved as `-deploy` resolves them, through the recorded export, so both
    halves compile the same folder. An application with no recorded export has
    nothing to compile here; the deploy names it when it would have imported it.
    """
    if validation is None:
        return []
    app_ids = sorted({
        app_id
        for path in files
        if is_apexlang_path(path, config) and (app_id := apex_app_id(path, config)) is not None
    })
    if not app_ids:
        return []
    targets, _missing = resolve_targets(root, config, app_ids=[str(app_id) for app_id in app_ids])
    issues = precheck_trees(root, config, targets)
    validate_trees(root, targets, validation, retry=RETRY_CREATE)
    return issues


def check_deploy_trees(
    root       : Path,
    config     : dict[str, Any],
    app_ids    : Sequence[int],
    validation : ApexlangValidation | None,
) -> None:
    """`-deploy -app`'s gate, run before the connection block (ADT #966).

    Jan: *"VALIDATING should be done BEFORE connecting"*. The compiler opens no
    connection (SQLcl `/nolog`), so a refused tree stops the run before any
    schema is connected. The trees are completed first, the way the import
    reads them; the conversions are kept on ``validation`` for the deploy to
    report, and the deploy's own gate finds every tree already passed.
    """
    if validation is None or not app_ids:
        return
    targets, _missing = resolve_targets(
        root, config, app_ids=[str(app_id) for app_id in sorted(set(app_ids))]
    )
    validation.issues.extend(precheck_trees(root, config, targets))
    validate_trees(root, targets, validation, retry=RETRY_DEPLOY)


def precheck_trees(
    root    : Path,
    config  : dict[str, Any],
    targets : Sequence[ValidateTarget],
) -> list[PrecheckIssue]:
    """Complete, convert and check each tree in place; the conversions made.

    Raises ``PatchError`` for a static file a tree names and lacks, before the
    compiler is asked about it and before anything is written anywhere else.
    """
    resolver = ApexFileResolver.from_config(root, config)
    issues: list[PrecheckIssue] = []
    for target in targets:
        if target.app_id is None:
            raise PatchError(NO_APPLICATION_ID)
        label = relative_label(target.path, root)
        # ADT #765: the payloads are linked into the export's own tree and kept
        # there, so the compiler reads the folder that is committed rather than
        # a copy. From the configured `apex_path_files`, as `validate` reads it
        # (#923).
        link_payloads(target.path, resolver.files_root(target.path.parent))
        # ADT #928: SQLcl's compiler cannot read CRLF, so a tree committed that
        # way is converted in place and noted, before the tree is hashed, so the
        # log names the bytes the import read. Never refused: a re-export would
        # throw away the hand-edited `.apx` this patch exists to ship.
        if converted := convert_crlf(target.path):
            issues.append(crlf_issue(label, converted))
        # ADT #930: a static file the tree names and lacks fails the compile, so
        # it refuses here, before the lock writes a status and before `init` runs.
        if missing := missing_static_files(target.path):
            raise PatchError(missing_refusal(target.app_id, label, missing))
    return issues


def validate_trees(
    root       : Path,
    targets    : Sequence[ValidateTarget],
    validation : ApexlangValidation,
    *,
    retry      : str,
) -> None:
    """Compile each tree this run has not passed yet; refuse on any that fails.

    Every tree is compiled before the refusal, so one screen names every
    application that needs fixing rather than the first of them.
    """
    pending = tuple(target for target in targets if target.app_id not in validation.passed)
    if not pending:
        return
    result = ValidateRunner(validation.sqlcl_request).run(
        ValidateRequest(
            targets      = pending,
            root         = root,
            project_root = root,
            reporter     = validation.reporter,
        )
    )
    validation.passed.update(
        folder.target.app_id
        for folder in result.folders
        if folder.target.app_id is not None and not folder.report.failed
    )
    if failed := [folder for folder in result.folders if folder.report.failed]:
        raise ApexlangValidationError(failed, retry)


def relative_label(path: Path, root: Path) -> str:
    """``path`` from the project root, or absolute when it sits outside it."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


__all__ = [
    "NO_APPLICATION_ID",
    "RETRY_CREATE",
    "RETRY_DEPLOY",
    "ApexlangValidation",
    "ApexlangValidationError",
    "check_deploy_trees",
    "check_patch_trees",
    "precheck_trees",
    "relative_label",
    "validate_trees",
]
