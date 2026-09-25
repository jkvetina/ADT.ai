"""What an install script reaches through its `@`/`@@` includes, and what it runs.

This module also wrote `logs_<TARGET>/deployment.json`, a completed-run receipt
keyed on a hash of the payload, until ADT #965 removed it (Jan, 2026-09-25:
*"REMOVE IT, YOU HAVE EVERYTHING IN REAL LOGS"*). The include walk it hashed
over is still what `installed_app_id` reads a carrier with, so it stays.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from adt_ai.patch.apex_deploy import ApexImportItem
from adt_ai.patch.deploy_progress import _link_pattern
from adt_ai.patch.models import DeploymentPlanItem
from adt_ai.patch.templates import _apex_environment_payload, for_target, unscoped

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
