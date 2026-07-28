"""Decide which APEXlang folders a ``validate`` run covers — entirely offline.

``config/apex_apps.yaml`` already records ``owner``/``app_alias`` per app id
(written by ``export_apex``), and ``ApexFileResolver.apexlang_root()`` already
knows where an APEXlang tree lives, so ``-app 800`` resolves to a path with no
database round-trip. That is what keeps ``validate`` connectionless.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.shared.yaml_io import load_yaml_mapping

APEXLANG_DIR = "apexlang"
APPS_METADATA = "config/apex_apps.yaml"


@dataclass(frozen=True)
class ValidateTarget:
    path   : Path
    label  : str
    app_id : int | None = None
    # True when this is a project `apexlang/` folder, so its static-file payloads
    # can be staged in from the sibling `files/` export (card `#165`). An
    # `-input` path is never stageable: that mode reads no project config by
    # contract, and may point at a zip or a single `.apx` rather than a tree.
    stageable : bool = False


def resolve_targets(
    root     : Path,
    config   : Mapping[str, Any],
    inputs   : list[str] | None = None,
    app_ids  : list[str] | None = None,
) -> tuple[list[ValidateTarget], list[str]]:
    """Return the folders to validate plus human-readable notes about misses.

    Precedence follows the command surface: explicit ``-input`` paths first, then
    ``-app`` ids, and a bare run discovers every ``apexlang/`` folder under the
    configured APEX root. Notes are the actionable half — an app with no export
    on disk names the path where one was expected instead of raising.
    """
    targets: list[ValidateTarget] = []
    notes: list[str] = []

    for value in inputs or []:
        # Resolved, because ``root`` is: ~/Dropbox is a symlink to
        # ~/Library/CloudStorage/Dropbox, so comparing an unresolved -input
        # against a resolved root made ``_label`` fall back to the absolute path
        # for a folder sitting right under the root (card `#164`).
        path = Path(value).expanduser().resolve()
        targets.append(ValidateTarget(path, _label(path, root)))

    if app_ids:
        resolved, app_notes = _targets_for_apps(root, config, app_ids)
        targets.extend(resolved)
        notes.extend(app_notes)

    if not inputs and not app_ids:
        discovered = _discover(root, config)
        if discovered:
            targets.extend(discovered)
        else:
            where = _label(_discovery_root(root, config), root)
            notes.append(
                f"No {APEXLANG_DIR}/ folder found under {where} "
                f"- run `adtai export_apex -apexlang` first."
            )

    return targets, notes


def _targets_for_apps(
    root    : Path,
    config  : Mapping[str, Any],
    app_ids : list[str],
) -> tuple[list[ValidateTarget], list[str]]:
    metadata = load_yaml_mapping(root / "config" / "apex_apps.yaml")
    resolver = ApexFileResolver.from_config(root, dict(config))
    targets: list[ValidateTarget] = []
    notes: list[str] = []
    for raw_id in app_ids:
        entry = metadata.get(_app_key(raw_id))
        if not isinstance(entry, Mapping):
            notes.append(
                f"app {raw_id}: not recorded in {APPS_METADATA} "
                f"- run `adtai export_apex -app {raw_id}` first."
            )
            continue
        application = _application(entry, raw_id)
        folder = resolver.for_schema(application.owner).apexlang_root(application)
        if not folder.is_dir():
            notes.append(
                f"app {raw_id}: nothing to validate, no export at {_label(folder, root)} "
                f"- run `adtai export_apex -app {raw_id} -apexlang` first."
            )
            continue
        targets.append(
            ValidateTarget(folder, _label(folder, root), application.app_id, stageable=True)
        )
    return targets, notes


def _application(entry: Mapping[str, Any], raw_id: str) -> ApexApplication:
    return ApexApplication(
        owner        = str(entry.get("owner") or ""),
        workspace    = str(entry.get("workspace") or ""),
        workspace_id = entry.get("workspace_id"),
        app_group    = str(entry.get("app_group") or ""),
        app_id       = int(entry.get("app_id") or raw_id),
        app_alias    = str(entry.get("app_alias") or ""),
        app_name     = str(entry.get("app_name") or ""),
        pages        = entry.get("pages"),
        updated_at   = str(entry.get("updated_at") or ""),
    )


def _app_key(raw_id: str) -> str | int:
    text = str(raw_id).strip()
    return int(text) if text.isdigit() else text


def _discovery_root(root: Path, config: Mapping[str, Any]) -> Path:
    """The folder a bare run walks.

    ``path_apex`` may carry a ``<schema>`` token, which only resolves once a
    schema is bound — and a bare run has none. Walking from the static prefix
    before that token covers every schema at once without guessing which ones
    exist on disk.
    """
    configured = str(config.get("path_apex") or "apex/")
    prefix = configured.split("<")[0].strip("/")
    candidate = root / prefix if prefix else root
    return candidate if candidate.is_dir() else root


def _discover(root: Path, config: Mapping[str, Any]) -> list[ValidateTarget]:
    base = _discovery_root(root, config)
    folders = sorted(
        path
        for path in base.rglob(APEXLANG_DIR)
        if path.is_dir() and not _under_dot_folder(path, base)
    )
    return [
        ValidateTarget(folder, _label(folder, root), stageable=True) for folder in folders
    ]


def _under_dot_folder(path: Path, base: Path) -> bool:
    """Skip hidden folders *below the base* only.

    The base itself routinely sits under a dot folder — every ADT.ai task
    worktree lives in ``.worktrees/`` — so judging the absolute path's parts
    would discover nothing there.
    """
    try:
        relative = path.relative_to(base)
    except ValueError:
        return False
    return any(part.startswith(".") for part in relative.parts)


def _label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
