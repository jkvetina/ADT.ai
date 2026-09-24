"""Durable completion of the exact patch payload and verification policy."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from adt_ai.patch import settings
from adt_ai.patch.apex_deploy import ApexImportItem, _application_facts
from adt_ai.patch.apex_import import ApexTarget
from adt_ai.patch.deploy_progress import _link_pattern
from adt_ai.patch.models import DeploymentPlanItem
from adt_ai.patch.templates import _apex_environment_payload, for_target, unscoped
from adt_ai.shared import text_files
from adt_ai.shared.apexlang_line_endings import import_bytes, is_source
from adt_ai.shared.deploy_status import DEPLOY_RECEIPT, read_deploy_receipt
from adt_ai.validate.files import resolve_targets

_INCLUDE_RE = re.compile(r'''^(@@?|START\s+)(?:"([^"]+)"|'([^']+)'|([^\s;]+))''', re.I)


def _include_closure(paths: set[Path], folder: Path, config: dict[str, Any]) -> bool:
    """Follow SQLcl's cwd-relative @ and caller-relative @@, stopping cycles.

    Missing or dynamic includes cannot establish a reusable completion proof.
    They still execute normally; only the optimization that skips a run is lost.
    """
    # The initial inventory also contains binary snapshot payloads. Only SQL
    # roots are parsed initially; every explicit include is executable whatever
    # its extension, including .pks/.pkb/.inc and extensionless scripts.
    pending = [path for path in paths if path.suffix.lower() == ".sql"]
    seen: set[Path] = set()
    configured = _link_pattern(config)
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            return False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            # Every environment's `--[ENV] ` link is followed, not only this
            # target's (#924 F33): a superset proof re-runs a deploy after any
            # scoped template changes, and never skips one that should run.
            line = unscoped(line).strip()
            if re.match(r"^(?:(?:CD|HOST)(?:\s|$)|!)", line, re.I):
                return False
            match = _INCLUDE_RE.match(line)
            custom = configured.fullmatch(line)
            if match:
                name = next(value for value in match.groups()[1:] if value is not None)
                base = path.parent if match.group(1) == "@@" else folder
            elif custom:
                name, base = custom.group("path"), folder
            else:
                if re.match(r"^(?:@|STA(?:RT)?(?:\s|$))", line, re.I):
                    return False
                continue
            if "&" in name:
                return False
            child = (base / name).resolve()
            if not child.suffix and not child.is_file():
                child = child.with_suffix(".sql")
            paths.add(child)
            pending.append(child)
    return True


def deployment_fingerprint(
    root: Path,
    folder: Path,
    plan: list[DeploymentPlanItem],
    config: dict[str, Any],
    apex_target: ApexTarget | None,
    apex_version: str | None,
    apex_account: str,
    continue_on_error: bool = False,
) -> str:
    """Hash executable inputs, never deployment logs or mutable hash baselines.

    Snapshot and moved-script trees include nested SQL and binary payloads.
    Direct installer links also cover live sources and shared templates. An
    APEXlang import reads its current local tree and static payloads by design.

    ``continue_on_error`` is in the policy for the same reason ``scan`` is
    (`#749`): it decides what a verdict COSTS, so two runs that would reach
    different conclusions from identical scan output are not the same run. A
    `-continue` deploy that waived a failing scan writes `SUCCESS`, and without
    this the next plain `-deploy` of the same payload would match that receipt,
    skip before its first script, and never issue the scan the operator did not
    waive.
    """
    paths = {item.path.resolve() for item in plan}
    sources: set[Path] = set()
    for name in (settings.snapshots_folder(config), settings.scripts_snap_folder(config)):
        paths.update(path.resolve() for path in (folder / name).rglob("*") if path.is_file())
    if not _include_closure(paths, folder, config):
        return ""
    if apex_target is not None and apex_target.selected:
        targets, _notes = resolve_targets(
            root, config,
            app_ids=[str(value) for value in sorted({item.app_id for item in plan if item.app_id})],
        )
        for target in targets:
            for tree in (target.path, target.path.parent / "files"):
                paths.update(path.resolve() for path in tree.rglob("*") if path.is_file())
            sources.update(
                path.resolve() for path in target.path.rglob("*")
                if path.is_file() and is_source(path.relative_to(target.path))
            )
    digest = hashlib.sha256()
    policy = {
        "plan": [(item.file, item.schema, item.app_id) for item in plan],
        "scan": settings.verify_deploy_scan(config),
        "continue_on_error": continue_on_error,
        "apex_target": asdict(apex_target) if apex_target is not None else None,
        "apex_version": apex_version,
        "apex_account": apex_account,
        "application_facts": (
            _application_facts(root, sorted({item.app_id for item in plan if item.app_id}))
            if apex_target is not None and apex_target.selected else None
        ),
    }
    digest.update(json.dumps(policy, sort_keys=True).encode())
    for path in sorted(paths):
        # Root-relative identities survive moving/cloning the patch checkout.
        label = Path(os.path.relpath(path, root)).as_posix()
        content = path.read_bytes() if path.is_file() else b"<MISSING>"
        if path in sources:
            # The bytes the import reads, after the tree's CRLF-to-LF conversion
            # (ADT #928), so a run that converted its tree matches its own
            # receipt next time rather than importing the same tree again (#936).
            content = import_bytes(content)
        digest.update(json.dumps([label, len(content)]).encode())
        digest.update(content)
    return digest.hexdigest()


def deployment_complete(log_folder: Path, target: str, fingerprint: str) -> bool:
    receipt = read_deploy_receipt(log_folder / DEPLOY_RECEIPT)
    return (
        bool(fingerprint)
        and receipt.get("status") == "SUCCESS"
        and receipt.get("target") == target
        and receipt.get("fingerprint") == fingerprint
    )


def write_deploy_receipt(log_folder: Path, target: str, fingerprint: str, status: str) -> None:
    """Atomically replace the receipt before execution and after verification."""
    text_files.write_text(
        log_folder / DEPLOY_RECEIPT,
        json.dumps({
            "version": 1, "target": target, "fingerprint": fingerprint, "status": status,
        }) + "\n",
    )


# a `/* */` block, never a `/*+` optimizer hint
BLOCK_COMMENT = re.compile(r"/\*(?!\+).*?\*/", re.DOTALL)


def _runs_sql(text: str) -> bool:
    """Whether a carrier holds anything beyond comments and SQLcl directives."""
    for line in BLOCK_COMMENT.sub("", text).splitlines():
        value = line.strip().upper()
        if value and not value.startswith(
            ("--", "PROMPT ", "SET ", "WHENEVER ", "SPOOL ", "@", "START ")
        ):
            return True
    return False


def installed_app_id(
    item: DeploymentPlanItem, imports: list[ApexImportItem], root: Path, config: dict[str, Any],
    target: str | None = None,
) -> int | None:
    """A tree-only carrier does not install its source application.

    Counts cannot prove this: templates and inline SQL can write an application
    without contributing a countable file. Only an inert SQLcl carrier, after
    removing the known workspace-selection block, can omit the source scan.
    Unknown SQL remains subject to verification of both source and target.
    The script is read as ``target`` runs it (#924 F33).

    It lives here rather than in the deploy loop it is called from (`#929`)
    because both readers it leans on do: `_include_closure` walks the same
    `@`/`@@` closure the fingerprint above hashes, and `_runs_sql` answers the
    same question about the same carrier. The loop kept a third copy of that
    neighbourhood and crossed the 24 KB context guard holding it.
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
        if path == item.path.resolve():
            text = for_target(text, config, target)
        if environment:
            text = text.replace(environment, "")
        if _runs_sql(text):
            return item.app_id
    return None
