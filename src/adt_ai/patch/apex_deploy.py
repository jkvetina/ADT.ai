"""Landing the APEXlang tree on the target, as a step of `patch -deploy`.

`patch/apex_import.py` answers which id a tree lands on and what SQLcl is asked
to do about it; this is what actually asks. It is the third side of the round
trip ADT already owned both read halves of, `export_apex -apexlang` writing the
tree and `validate` compiling it (ADT #592).

**It runs where the grant already is.** `#142` carved `patch -deploy` out of the
agent SQLcl block; a fresh top-level verb would have needed a carve-out of its
own for a mechanic that is identical either way, so the runner lives on `patch`
and `-app` is what turns it on. Absent `-app` nothing here runs at all, which is
what keeps the existing command exactly as it was.

**The tree is staged, never imported where it sits.** `export_apex -apexlang`
omits the static-file payloads by design, so a committed `apexlang/` folder
reports one `REFERENCE_NOT_FOUND` per payload and `apex import` validates before
it writes. `validate/staging.stage_apexlang` is the same hardlink tree
`validate` already builds, reused rather than re-implemented, so the bytes the
import sees are the bytes the compile gate passed.

**Everything that can refuse, refuses before the first install script runs.**
Staging, the signature read and all three gates happen in `prepare` and the
import itself in `run`, with the patch's own scripts in between. That ordering is
the card's own requirement, the target's signature read before anything is
written, and it has a second payoff: a deploy refused on drift has not deployed
the database half either, so there is nothing to undo.

**One import cannot serve two applications.** `resolve_target` already refuses a
second id on the flag; the same collision arrives from the other direction when
a patch ships two applications and the run names one target id, and it is
refused here for the same reason: one of them would land and the other would be
dropped behind a correct-looking screen.

**A retarget refuses a full export in the same patch.** `-app <id>` moves where
the TREE lands and can do nothing about an `f<source>.sql` install script, which
would install the source application in place while the tree went to the
sandbox, so a run meant to touch nothing but a throwaway id would write the real
one. That is a production write nobody asked for, so it is refused, and `-force`
overrides it in the sense `-force` already carries in `patch` (`#309`),
overriding a refusal rather than acquiring a third meaning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.patch.apex_import import (
    ApexTarget,
    build_import_script,
    derive_sandbox_alias,
    recover_task_number,
)
from adt_ai.patch.apex_signature import (
    ApexSignatures,
    collect_signatures,
    drift_message,
    signature_lines,
)
from adt_ai.patch.layout import is_apex_full_export
from adt_ai.patch.models import DeploymentPlanItem, DeploymentResult
from adt_ai.shared.apex_store import ApexStore
from adt_ai.validate.files import resolve_targets
from adt_ai.validate.report import message_lines, parse_import_output
from adt_ai.validate.staging import stage_apexlang, staging_root_for

# The sibling export that owns the static-file payloads `-apexlang` skips, the
# same constant `cli/commands_validate.py` reads for the same reason.
FILES_DIR = "files"

EXPORT_COMMAND = "adtai export_apex -apexlang -app"


@dataclass(frozen=True)
class ApexImportItem:
    """One application's tree, resolved, staged, and read."""

    app_id     : int
    target_id  : int
    alias      : str
    schema     : str
    source     : Path
    staged     : Path
    label      : str
    files      : int
    signatures : ApexSignatures

    @property
    def file(self) -> str:
        """What the deploy table and the log file name this step.

        Not a `.sql` name: nothing here is an install script, and a reader
        opening the patch folder looking for one would not find it.
        """
        return f"apex_import_{self.target_id}"

    @property
    def retargeted(self) -> bool:
        return self.target_id != self.app_id

    @property
    def alias_for_target(self) -> str:
        """The alias that travels with the id, derived in the same step.

        `derive_sandbox_alias` is the shipped rule and takes the TASK number,
        because that is what a derived id carries: app `1100` under task `123`
        is `1100123`. The id reaching this deploy is whatever the operator typed
        though, Jan having settled that `-app` accepts any number, so the task is
        recovered by taking off the application id it is prefixed with, and the
        whole value stands in when it carries no such half.

        Either way the suffix is unique in the workspace because the id is, which
        is the property that matters: an APEX alias is unique per workspace, so a
        sandbox keeping the source alias collides with the application the tree
        came from and APEX only says so at import time.
        """
        if not self.retargeted:
            return self.alias
        return derive_sandbox_alias(self.alias, _task_number(self.app_id, self.target_id))

    @property
    def target(self) -> ApexTarget:
        """The `ApexTarget` this one item lands under.

        Rebuilt per item rather than threaded through, because `-app`'s own
        target is one value for the whole run while the id an item lands on is a
        property of the item: under a bare `-app` every application gets its own
        id back, and `build_import_script` then emits no `-id` at all.
        """
        return ApexTarget(
            selected     = True,
            target_id    = self.target_id if self.retargeted else None,
            full_app_ids = [],
        )

    def plan_item(self, order: int, commits: int) -> DeploymentPlanItem:
        """The row the deploy table sizes itself on, beside the install scripts.

        The import is announced through the table `DEPLOYING PATCH:` already
        heads rather than through a section of its own. A wait that owns no row
        owns a section, and this one owns a row: the console grows no new string
        for it, which is the difference between flushing what exists and a
        console redesign (`#372`).
        """
        return DeploymentPlanItem(
            order   = order,
            file    = self.file,
            schema  = self.schema,
            app_id  = self.target_id,
            files   = self.files,
            commits = commits,
            path    = self.source,
        )


def prepare_apex_imports(
    root            : Path,
    config          : dict[str, Any],
    app_ids         : list[int],
    patch_files     : list[str],
    apex_target     : ApexTarget | None,
    gateway_factory : Any,
    *,
    force           : bool = False,
) -> tuple[list[ApexImportItem], list[str]]:
    """Resolve, stage and read every application this deploy imports.

    Returns the prepared items plus notes about applications the patch ships and
    no tree was exported for. A note rather than a refusal there: the patch may
    legitimately carry an application whose APEXlang export nobody has taken, and
    refusing would stop a deploy that was never asking for an import.

    Raises ``PatchError`` for each of the three refusals, all of them before a
    single byte is written.
    """
    from adt_ai.patch.runner import PatchError

    if apex_target is None or not apex_target.selected or not app_ids:
        return [], []
    target_id = apex_target.target_id
    if target_id is not None and len(app_ids) > 1:
        named = ", ".join(str(app_id) for app_id in sorted(app_ids))
        raise PatchError(
            f"-app {target_id} names one target and this patch ships {len(app_ids)} "
            f"applications ({named}). Several applications cannot land on one id."
        )
    refusal = _full_export_refusal(patch_files, config, target_id)
    if refusal and not force:
        raise PatchError(refusal)

    items: list[ApexImportItem] = []
    aliases, owners = _application_facts(root, app_ids)
    targets, notes = resolve_targets(
        root, config, app_ids=[str(app_id) for app_id in sorted(app_ids)]
    )
    for resolved in targets:
        app_id = resolved.app_id
        if app_id is None:
            raise PatchError("resolved APEX import target has no application id")
        landing = target_id if target_id is not None else app_id
        staged = stage_apexlang(
            resolved.path,
            resolved.path.parent / FILES_DIR,
            staging_root_for(root, resolved.path),
        )
        signatures = collect_signatures(
            gateway_factory(owners.get(app_id, "")),
            root,
            app_id    = app_id,
            target_id = landing,
            tree_root = staged.path,
        )
        if signatures.refused and not force:
            raise PatchError(drift_message(signatures))
        items.append(
            ApexImportItem(
                app_id     = app_id,
                target_id  = landing,
                alias      = aliases.get(app_id, ""),
                schema     = owners.get(app_id, ""),
                source     = resolved.path,
                staged     = staged.path,
                label      = resolved.label,
                files      = staged.metadata_files + staged.payload_files,
                signatures = signatures,
            )
        )
    return items, notes


def run_apex_imports(
    items           : list[ApexImportItem],
    root            : Path,
    gateway_factory : Any,
    log_writer      : Any,
    *,
    order_from      : int,
    commits         : int,
    force           : bool = False,
    reporter        : Any = None,
    account         : str = "",
) -> list[DeploymentResult]:
    """Import each staged tree, one row per application.

    Nothing here raises: a deploy that got half way is a result to read, not an
    exception that swallows the table, which is the rule the install-script loop
    beside it already follows.

    ``account`` is the developer a retargeted import is stamped as, resolved at
    the CLI edge and handed down the way ``apex_version`` already is rather than
    read again here (ADT #682). `apex_import.build_import_script` owns what that
    stamp does and which imports get one.
    """
    results: list[DeploymentResult] = []
    for offset, item in enumerate(items, start=1):
        if reporter is not None:
            reporter.begin_script(item.plan_item(order_from + offset, commits))
        started_at = time.monotonic()
        script = build_import_script(
            item.staged, item.target, item.alias_for_target, account=account
        )
        execution_failed = False
        try:
            output = gateway_factory(item.schema).sqlcl_request(script, root)
        except Exception as error:  # noqa: BLE001 - reported as a row, like a script
            execution_failed = True
            output = str(error)
        seconds = time.monotonic() - started_at
        report = parse_import_output(output)
        status = "SUCCESS" if not execution_failed and not report.failed else "ERROR"
        log_path = log_writer(
            item.file,
            status,
            "\n".join([
                *signature_lines(item.signatures, forced=force),
                _source_line(item, root),
                "",
                output,
            ]),
        )
        result = DeploymentResult(
            order         = order_from + offset,
            file          = item.file,
            schema        = item.schema,
            app_id        = item.target_id,
            files         = item.files,
            commits       = commits,
            status        = status,
            log_path      = log_path,
            error_excerpt = _excerpt(report) if status == "ERROR" else (),
            seconds       = seconds,
        )
        results.append(result)
        if reporter is not None:
            reporter.end_script(result)
    return results


def _source_line(item: ApexImportItem, root: Path) -> str:
    """The row naming the folder this import read the application out of.

    The patch carries no copy of an APEXlang tree (ADT #602), so the log is the
    only place a reader can find out where the bytes came from. Jan, 2026-08-30:
    *"We should print a note then in the log that app was deployed from THAT
    folder."* Repo-relative when it sits under the project root, which is every
    ordinary run, and absolute otherwise rather than guessed at with `..`.

    Same column width as the three signature rows above it, so the block reads
    as one table rather than as a row bolted onto it.
    """
    try:
        source = item.source.relative_to(root).as_posix()
    except ValueError:
        source = str(item.source)
    return f"--   DEPLOYED FROM    | {source}"


def _full_export_refusal(
    files     : list[str],
    config    : dict[str, Any],
    target_id : int | None,
) -> str:
    """The message for a retarget whose patch also installs a full export.

    Empty when there is nothing to refuse. See the module docstring for why this
    combination is a production write rather than a redundancy.
    """
    if target_id is None:
        return ""
    exports = sorted(path for path in files if is_apex_full_export(path, config))
    if not exports:
        return ""
    lines = [
        f"-app {target_id} lands the tree on a different application, and this "
        "patch also installs a full export, which cannot be retargeted and would "
        "install the source application in place."
    ]
    lines.extend(f"  {path}" for path in exports)
    lines.append(
        "Run: drop -app's value to deploy in place, or rebuild the patch without "
        "the full export (or -force to deploy both)"
    )
    return "\n".join(lines)


def _application_facts(root: Path, app_ids: list[int]) -> tuple[dict[int, str], dict[int, str]]:
    """Alias and owner per application, in one store session rather than two."""
    aliases: dict[int, str] = {}
    owners: dict[int, str] = {}
    with ApexStore.load(root) as store:
        for app_id in app_ids:
            entry = store.application(app_id) or {}
            aliases[app_id] = str(entry.get("app_alias") or "")
            owners[app_id] = str(entry.get("owner") or "")
    return aliases, owners


def _task_number(app_id: int, target_id: int) -> int:
    """The task half of a derived id, or the whole id when it carries no app half.

    `apex_import.recover_task_number` is the shared prefix strip (`#670`), used
    the same way by `apex_drop._prefix_sources`; the fallback to the whole
    value is this call site's own, for a remainder that is not a positive
    number (an id equal to the application's own, a remainder of nothing but
    zeros), which `derive_sandbox_alias` then judges on its own terms rather
    than on a guess made here.
    """
    task = recover_task_number(app_id, target_id)
    return target_id if task is None else task


def _excerpt(report: Any) -> tuple[str, ...]:
    """The compiler's own rows, in the shape the deploy table's error block wants.

    A report with no parsed rows still owes the reader something, an
    UNRECOGNISED outcome being the case where the transcript is all there is, so
    its tail is carried instead of an empty tuple that would render as a failure
    with no reason attached.
    """
    if report.errors:
        return tuple(message_lines(report.errors))
    return tuple(line for line in report.raw.splitlines()[-10:] if line.strip())


__all__ = [name for name in globals() if not name.startswith("_")]
