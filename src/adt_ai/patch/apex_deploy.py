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

**The tree is imported where it sits.** `export_apex -apexlang` omits the
static-file payloads by design, so an `apexlang/` folder on its own reports one
`REFERENCE_NOT_FOUND` per payload and `apex import` validates before it writes.
ADT #165 answered that with a staged copy under `config/temp/`; ADT #765 replaced
it with `shared/apex_payloads.link_payloads`, which keeps the payloads hardlinked
inside the export's own tree. So the bytes the import sees are the bytes the
compile gate passed and the bytes that are committed, with no second tree that
could disagree with any of them.

**Everything that can refuse, refuses before the first install script runs.** The
payload reconciliation, the signature read and all three gates happen in `prepare`
and the import itself in `run`, with the patch's own scripts in between. That
ordering is the card's own requirement, the target's signature read before anything
is written, and it has a second payoff: a deploy refused on drift has not deployed
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

from adt_ai.patch import settings
from adt_ai.patch.apex_backup import ApexBackup, backup_line
from adt_ai.patch.apex_import import (
    ApexTarget,
    build_import_script,
    derive_sandbox_alias,
    recover_task_number,
)
from adt_ai.patch.apex_lock import BuildStatusLock, build_status_line, lock_target
from adt_ai.patch.apex_signature import (
    ApexSignatures,
    collect_signatures,
    drift_message,
    signature_lines,
)
from adt_ai.patch.layout import is_apex_full_export
from adt_ai.patch.models import DeploymentPlanItem, DeploymentResult
from adt_ai.shared.apex_payloads import IGNORE_NAME, drop_legacy_staging, link_payloads
from adt_ai.shared.apex_store import ApexStore
from adt_ai.validate.files import resolve_targets
from adt_ai.validate.report import message_lines, parse_import_output

# The sibling export that owns the static-file payloads `-apexlang` skips, the
# same constant `cli/commands_validate.py` reads for the same reason.
FILES_DIR = "files"

EXPORT_COMMAND = "adtai export_apex -apexlang -app"


def _tree_files(apexlang_root: Path) -> int:
    """How many files the import is about to carry, payload links included.

    Counted off the tree rather than summed from a staging result, because the tree
    IS what the import reads since ADT #765. The ignore file the payload folder
    carries is ADT.ai's own bookkeeping and is not one of them.
    """
    return sum(
        1
        for path in apexlang_root.rglob("*")
        if path.is_file() and path.name != IGNORE_NAME
    )

# What the deploy table calls the import (ADT #735). It was `apex_import_<id>`,
# spelled like the install scripts around it, and nothing in the patch folder
# answers to that name: the import is a SQLcl command the run issues, not a file
# it reads. Jan, 2026-09-07: *"clearly mark in console that apex_import_1000 is
# not the actual file, but a sqlcl command. Lets try '> BUILDING APP'"*. The
# leading `>` is the mark. The log keeps the old stem (`log_stem` below), a
# filename is where a `>` does not belong.
BUILDING_APP_ROW = "> BUILDING APP"


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
        """What the deploy table calls this step: `> BUILDING APP`.

        Not a `.sql` name and not a name at all: nothing here is an install
        script, and a reader opening the patch folder looking for one would not
        find it, which is what the row said before `#735` and what it now says
        out loud.
        """
        return BUILDING_APP_ROW

    @property
    def log_stem(self) -> str:
        """The stem of this step's deploy log, `apex_import_<target id>`.

        The one place the import still carries a filename, and it carries the id
        the tree landed on because a folder of logs is read by name.
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
    locks           : dict[int, BuildStatusLock] | None = None,
) -> tuple[list[ApexImportItem], list[str]]:
    """Resolve, stage and read every application this deploy imports.

    Returns the prepared items plus notes about applications the patch ships and
    no tree was exported for. A note rather than a refusal there: the patch may
    legitimately carry an application whose APEXlang export nobody has taken, and
    refusing would stop a deploy that was never asking for an import.

    Raises ``PatchError`` for each of the three refusals, all of them before a
    single byte is written.

    ``locks`` is where the build-status lock records what it took (ADT #726),
    filled as each application is locked rather than returned, because this
    function RAISES on drift and a lock taken before that refusal still has to
    be released. A caller that passes nothing takes no locks at all, which is
    what every test of the three refusals wants.
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
    aliases, owners, workspaces = _application_facts(root, app_ids)
    drop_legacy_staging(root)
    targets, notes = resolve_targets(
        root, config, app_ids=[str(app_id) for app_id in sorted(app_ids)]
    )
    for resolved in targets:
        app_id = resolved.app_id
        if app_id is None:
            raise PatchError("resolved APEX import target has no application id")
        landing = target_id if target_id is not None else app_id
        # **Before the signature is read, which is the whole of the point**
        # (ADT #726). The window this closes runs from that read to the import,
        # so a lock taken after it would leave the race exactly where it was.
        # Only an import landing on the application's own id: a retargeted task
        # sandbox is a throwaway nobody is editing, and locking it would strand
        # a prototype on RUN_ONLY (Jan, 2026-09-07).
        # `off` records nothing at all rather than an entry saying it did
        # nothing: it has to leave the deploy exactly as it was before ADT #726,
        # and an import log growing a row is not exactly as it was.
        mode = settings.deploy_build_status(config)
        if locks is not None and landing == app_id and mode != settings.BUILD_STATUS_OFF:
            locks[app_id] = lock_target(
                gateway_factory(owners.get(app_id, "")),
                app_id,
                workspace = workspaces.get(app_id, ""),
                mode      = mode,
            )
        # ADT #765: the payloads are linked into the export's own tree and kept
        # there, so the import reads the folder that is committed rather than a
        # copy assembled under `config/temp/`. `apex import` compiles before it
        # writes, so it needs exactly the completeness `validate` needs, and it now
        # gets it from the same reconciliation rather than from a second tree that
        # could disagree with this one.
        link_payloads(resolved.path, resolved.path.parent / FILES_DIR)
        # ADT #745: a held lock has already read the target, one statement
        # before it wrote the status that moves that reading. `None` where no
        # lock went on, so an unlocked deploy reads the target here exactly as
        # it did before ADT #726.
        held = locks.get(app_id) if locks is not None else None
        signatures = collect_signatures(
            gateway_factory(owners.get(app_id, "")),
            root,
            app_id    = app_id,
            target_id = landing,
            tree_root = resolved.path,
            on_target = held.signature if held is not None and held.locked else None,
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
                staged     = resolved.path,
                label      = resolved.label,
                files      = _tree_files(resolved.path),
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
    backups         : dict[int, ApexBackup] | None = None,
    locks           : dict[int, BuildStatusLock] | None = None,
) -> list[DeploymentResult]:
    """Import each staged tree, one row per application.

    Nothing here raises: a deploy that got half way is a result to read, not an
    exception that swallows the table, which is the rule the install-script loop
    beside it already follows. That loop calls this once per application since
    ADT #735, between the application's `init` and `end` scripts, so ``items``
    is ordinarily one long; the list stays because the tail call for an
    application no script opened still passes several.

    ``account`` is the developer a retargeted import is stamped as, resolved at
    the CLI edge and handed down the way ``apex_version`` already is rather than
    read again here (ADT #682). `apex_import.build_import_script` owns what that
    stamp does and which imports get one.

    ``backups`` is what `apex_backup.back_up_targets` kept of each target before
    this loop was reached (ADT #727), read for one line: the log's `BACKUP` row,
    which is where a reader goes when the scan afterwards says the import broke
    the application. ``None`` is a run with `deploy_revert_on_scan_failure` off,
    and the row is then absent rather than written empty.

    ``locks`` is read the same way and for the same kind of line (ADT #726): the
    `BUILD STATUS` row, saying whether the application was held shut over the
    window this import closes. The lock's own timeline is a separate report,
    because its last two moments happen after this log is written.
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
            item.log_stem,
            status,
            "\n".join([
                *signature_lines(item.signatures, forced=force),
                _source_line(item, root),
                *(
                    [backup_line(backups.get(item.target_id), root)]
                    if backups is not None
                    else []
                ),
                *(
                    [build_status_line(locks[item.app_id])]
                    if locks is not None and item.app_id in locks
                    else []
                ),
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


def _application_facts(
    root: Path, app_ids: list[int]
) -> tuple[dict[int, str], dict[int, str], dict[int, str]]:
    """Alias, owner and workspace per application, in one store session.

    The workspace joined the pair when ADT #726 needed it: the build-status
    setter refuses without a workspace context, and this store read is already
    open, so asking for a third field costs nothing where a second `ApexStore`
    session would have cost a file open per deploy.
    """
    aliases: dict[int, str] = {}
    owners: dict[int, str] = {}
    workspaces: dict[int, str] = {}
    with ApexStore.load(root) as store:
        for app_id in app_ids:
            entry = store.application(app_id) or {}
            aliases[app_id] = str(entry.get("app_alias") or "")
            owners[app_id] = str(entry.get("owner") or "")
            workspaces[app_id] = str(entry.get("workspace") or "")
    return aliases, owners, workspaces


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
