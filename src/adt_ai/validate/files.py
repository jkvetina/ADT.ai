"""Decide which APEXlang folders a ``validate`` run covers, entirely offline.

``config/internal/apex.db`` already records ``owner``/``app_alias`` per app id
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
from adt_ai.shared.apex_paths import APEXLANG_DIR, apexlang_folders
from adt_ai.shared.apex_store import ApexStore, apex_store_path
from adt_ai.shared.path_template import DEFAULT_PATH_APP

APPS_METADATA = "config/internal/apex.db"


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
    configured APEX root. Notes are the actionable half, an app with no export
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
            notes.append(
                f"No {APEXLANG_DIR}/ folder found under {discovery_label(root, config)} "
                f"- run `adtai export_apex -apexlang` first."
            )

    return targets, notes


def discovery_label(root: Path, config: Mapping[str, Any]) -> str:
    """Where a bare run looked, phrased so the reader can go and check it.

    A bare run matches one SHAPE rather than walking a folder, so the honest
    answer is that shape written the way the project's own config writes it:
    `path_apex` and `apex_path_app` with their tokens intact, which names the two
    keys a reader would go and check. A rendered path could not be honest here,
    because `<schema>` stands for every schema at once (ADT #765).
    """
    templates = [
        str(config.get("path_apex") or "apex/").strip("/"),
        str(config.get("apex_path_app") or DEFAULT_PATH_APP).strip("/"),
    ]
    return "/".join(part for part in templates if part) or "."


def _targets_for_apps(
    root    : Path,
    config  : Mapping[str, Any],
    app_ids : list[str],
) -> tuple[list[ValidateTarget], list[str]]:
    resolver = ApexFileResolver.from_config(root, dict(config))
    targets: list[ValidateTarget] = []
    notes: list[str] = []
    with ApexStore.load(root) as store:
        for raw_id in app_ids:
            entry = store.application(raw_id)
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
                ValidateTarget(
                    folder,
                    app_label(application.app_id, application.app_alias),
                    application.app_id,
                    stageable = True,
                )
            )
    return targets, notes


def app_label(app_id: int, alias: str) -> str:
    """An application named the way Jan reads it: `<id>/<alias>` (ADT #966, #973).

    Jan: *"show app number + app alias, we must have this somewhere in
    export_apex"*. The alias is the one `export_apex` recorded, so no connection
    is needed to print it; an application recorded without one prints its id.
    Joined by a slash since ADT #973, the spelling `EXPORTING APP <id>/<alias>:`
    already used. Jan: *"Find all places where you have "<app_id> <app_alias>"
    and replace it with "<app_id>/<app_alias>""*.
    """
    return f"{app_id}/{alias}" if alias else str(app_id)


def _application(entry: Mapping[str, Any], raw_id: str) -> ApexApplication:
    return ApexApplication(
        owner        = str(entry.get("owner") or ""),
        workspace    = str(entry.get("workspace") or ""),
        app_group    = str(entry.get("app_group") or ""),
        app_id       = int(entry.get("app_id") or raw_id),
        app_alias    = str(entry.get("app_alias") or ""),
        app_name     = str(entry.get("app_name") or ""),
        pages        = entry.get("pages"),
        updated_at   = str(entry.get("updated_at") or ""),
    )


def _discover(root: Path, config: Mapping[str, Any]) -> list[ValidateTarget]:
    recorded = _recorded_labels(root, config)
    return [
        ValidateTarget(
            folder,
            recorded.get(folder.resolve()) or _export_label(folder, root),
            stageable = True,
        )
        for folder in apexlang_folders(root, config)
    ]


def _recorded_labels(root: Path, config: Mapping[str, Any]) -> dict[Path, str]:
    """Each recorded application's tree, named `<id>/<alias>` (ADT #966, #973).

    A bare run finds trees by walking folders, so the rows name the same
    applications `-app` names; a tree no export recorded keeps its folder.
    The store is only read when it exists: a bare run never creates one.
    """
    if not apex_store_path(root).is_file():
        return {}
    resolver = ApexFileResolver.from_config(root, dict(config))
    with ApexStore.load(root) as store:
        entries = store.applications()
    labels: dict[Path, str] = {}
    for raw_id, entry in entries.items():
        application = _application(entry, str(raw_id))
        folder = resolver.for_schema(application.owner).apexlang_root(application)
        labels[folder.resolve()] = app_label(application.app_id, application.app_alias)
    return labels


def _export_label(folder: Path, root: Path) -> str:
    """An export named by its application folder, not by the tree inside it.

    Every folder this module resolves for itself ends in `apexlang/`, because
    that is the only thing it compiles, so printing the segment on every row
    spends width to say what the module is. Jan, 2026-09-10: "In the VALIDATING
    section dont print the `/apexlang`, it is just noise".

    An `-input` label is deliberately not trimmed: that mode validates exactly
    the path it was handed, may be a zip or a single `.apx`, and its refusal
    screen has to echo what the user typed for them to recognise the typo.
    """
    label = _label(folder, root)
    suffix = f"/{APEXLANG_DIR}"
    return label[:-len(suffix)] if label.endswith(suffix) else label


def _label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
