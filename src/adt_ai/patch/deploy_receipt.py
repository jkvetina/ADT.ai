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
from adt_ai.patch.apex_deploy import _application_facts
from adt_ai.patch.apex_import import ApexTarget
from adt_ai.patch.deploy_progress import _link_pattern
from adt_ai.patch.models import DeploymentPlanItem
from adt_ai.shared import text_files
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
            line = line.strip()
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
) -> str:
    """Hash executable inputs, never deployment logs or mutable hash baselines.

    Snapshot and moved-script trees include nested SQL and binary payloads.
    Direct installer links also cover live sources and shared templates. An
    APEXlang import reads its current local tree and static payloads by design.
    """
    paths = {item.path.resolve() for item in plan}
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
    digest = hashlib.sha256()
    policy = {
        "plan": [(item.file, item.schema, item.app_id) for item in plan],
        "scan": settings.verify_deploy_scan(config),
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
