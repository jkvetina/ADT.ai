"""`patch -create` asks the `-deploy` signature question early (ADT #957).

`patch -deploy -app` refuses an import onto an application somebody moved since
the tree was exported (`apex_signature.py`, ADT #592). That is the right place to
stop, and the wrong place to find out: by then the patch is built and the
developer is at the deploy. Jan, 2026-09-24: *"We should already have this check
on -deploy, but that is a bit late. We can give warning to the user sooner so he
can rebase before the deployment fail."*

**A warning, never a refusal.** The gate that decides still runs on `-deploy`;
this reads the same two values and says what that gate is going to say, while
the rebase is still cheap. A read that fails says nothing at all, for the same
reason `read_last_change` answers "unknown": a courtesy must not stop a build.

**The live read is the SOURCE application, on the environment the export was
taken on** (ADT #962), never `-target`: a checksum recorded on PLAYGROUND read
against WHATEVER's live application answers a different question than the one
`-deploy` is about to ask, and would warn about nothing or warn about
everything depending on which two environments happened to disagree. An export
that recorded no environment (every store written before this) falls back to
the connection `-create` already opens for its table ALTERs (`-target`, else
the connection file's default), exactly as before. The `-app <id>` landing id
is where the deploy writes; the question here is whether anybody moved the
application the tree came from, which on a retarget is the only one somebody
else edits.

APEXlang trees only. ADT #956 was to extend this to the other formats and the
database objects, and Jan moved it to the deploy instead: *"If someone deployed
something to target after we created our patch, thats bad"*, while a colleague
working on the source meanwhile is not. `locks/check_apps.sql` is that guard.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from adt_ai.patch.apex_signature import (
    UNKNOWN,
    ApexSignatures,
    change_rows,
    export_command,
    read_last_change,
    read_target_signature,
    recorded_export,
)
from adt_ai.patch.layout import apex_app_id, is_apexlang_path
from adt_ai.patch.selection import apex_owner_schemas
from adt_ai.validate.files import ValidateTarget


def changed_applications(
    root            : Path,
    config          : dict[str, Any],
    files           : list[str],
    gateway_factory : Callable[[str, str], Any] | None,
    *,
    already_read    : Mapping[int, ApexSignatures | None] | None = None,
) -> list[ApexSignatures]:
    """Every APEXlang application in ``files`` that `-deploy` would refuse.

    One live read per application, on the connection of the environment the
    export recorded (``gateway_factory(environment, schema)``, ADT #962; an
    empty environment is the caller's own fallback for an export that recorded
    none) and the schema the export recorded as its owner. An application with
    no recorded checksum is reported without connecting: `-deploy` refuses it
    whatever the database holds.

    ``already_read`` is `DriftReads.answers` (ADT #988): the applications
    whose read already ran inside their own validation row are answered from
    it, in the same order, rather than read a second time. Given at all, it is
    the whole list of reads: an application the validation rows did not read
    had no tree on disk to compile, which `-deploy` cannot import either (it
    only names it), so a live read for it would be a round trip after the rows
    had closed, for a warning about a refusal that cannot happen. It is still
    reported when that verdict needs no connection (no recorded checksum).
    """
    if gateway_factory is None:
        return []
    app_ids = sorted({
        app_id
        for path in files
        if is_apexlang_path(path, config) and (app_id := apex_app_id(path, config)) is not None
    })
    if not app_ids:
        return []
    answers = already_read or {}
    reader = gateway_factory if already_read is None else None
    owners = apex_owner_schemas(root)
    changed: list[ApexSignatures] = []
    for app_id in app_ids:
        drift = (
            answers[app_id] if app_id in answers
            else application_drift(root, app_id, owners.get(app_id, ""), reader)
        )
        if drift is not None:
            changed.append(drift)
    return changed


def application_drift(
    root            : Path,
    app_id          : int,
    owner           : str,
    gateway_factory : Callable[[str, str], Any] | None,
) -> ApexSignatures | None:
    """One application's answer for `changed_applications`: its signatures, or None.

    None when `-deploy` would not refuse it, when the live read failed, or when
    the answer needs a live read and ``gateway_factory`` is None.
    """
    recorded = recorded_export(root, app_id)
    signatures = ApexSignatures(
        app_id      = app_id,
        target_id   = app_id,
        on_target   = "",
        based_on    = recorded.checksum,
        deploying   = "",
        base_commit = recorded.base_commit,
        mirror_ref  = recorded.mirror_ref,
        based_at    = recorded.checksum_at,
    )
    if signatures.verdict == UNKNOWN:
        return signatures
    if gateway_factory is None:
        return None
    live = _live_signature(gateway_factory, recorded.checksum_env, owner, app_id)
    if live is None:
        return None
    gateway, on_target = live
    signatures = replace(signatures, on_target=on_target)
    if not signatures.refused:
        return None
    return replace(signatures, last_change=read_last_change(gateway, app_id))


class DriftReads:
    """`-create`'s drift reads, each run inside its app's validation row (ADT #988).

    Jan: each `VALIDATING APEXLANG APPS:` row stays open through both the SQLcl
    compile and the live checksum read, and its clock covers both -- same
    apps, no new line. `read` is the hook `ValidateRunner` calls between a
    tree's clean compile and its row's close (`ValidateRequest.after_compile`);
    `changed_applications(..., already_read=answers)` then reports from what
    it found. A failed compile is never read: the build refuses right after.
    """

    def __init__(self, root: Path, gateway_factory: Callable[[str, str], Any] | None) -> None:
        self.root = root
        self.gateway_factory = gateway_factory
        self.answers: dict[int, ApexSignatures | None] = {}

    def read(self, target: ValidateTarget) -> None:
        if self.gateway_factory is None or target.app_id is None:
            return
        owner = apex_owner_schemas(self.root).get(target.app_id, "")
        self.answers[target.app_id] = application_drift(
            self.root, target.app_id, owner, self.gateway_factory
        )


def _live_signature(
    gateway_factory : Callable[[str, str], Any],
    env             : str,
    owner           : str,
    app_id          : int,
) -> tuple[Any, str] | None:
    """The owner's gateway on ``env`` and the live checksum, or None on failure.

    None rather than an error: the deploy's gate still decides, and a read a
    grant hides must not stop a patch from being written.
    """
    try:
        gateway = gateway_factory(env, owner)
        return gateway, read_target_signature(gateway, app_id)
    except Exception:  # noqa: BLE001 - reported as nothing, see above
        return None


def drift_warning_rows(signatures: ApexSignatures) -> list[str]:
    """The rows under one application's warning header.

    The header carries the headline since ADT #961 (`print_changed_apps`), so
    what is left is the `-deploy` refusal's own rows (`change_rows`), minus its
    overwrite sentence, and the way out as numbered steps ending at creating
    the patch again, since that is the step the developer is on. Jan: *"should
    be more clear, we used "1)" and "2)" elsewhere."*

    An application with no recorded checksum has no rows to show, only the steps.
    """
    if signatures.verdict == UNKNOWN:
        return recovery_steps(signatures)
    return [*change_rows(signatures), "", *recovery_steps(signatures)]


def recovery_steps(signatures: ApexSignatures) -> list[str]:
    """`  1) run: <command>` and what follows it, through `create the patch again`.

    The command is the `-deploy` refusal's own (`recovery_command` up to its
    comma): the rebase when the export shared a base, else the re-export, which
    alone leaves a tree to reconcile. The indent and the lowercase are the
    option lists `patch -create` already prints (`cli/patch_no_commits.py`).
    """
    if signatures.verdict == UNKNOWN:
        steps = [f"run: {export_command(signatures)}", "commit the export"]
    elif signatures.rebase_command:
        steps = [f"run: {signatures.rebase_command}"]
    else:
        steps = [f"run: {export_command(signatures)}", "reconcile the tree, commit changes"]
    steps.append("create the patch again")
    return [f"  {number}) {step}" for number, step in enumerate(steps, start=1)]


__all__ = [
    "DriftReads",
    "application_drift",
    "changed_applications",
    "drift_warning_rows",
    "recovery_steps",
]
