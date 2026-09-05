"""Everything a patch injects that is not an exported object file.

Two configured folders feed the generated install script: `patch_template_dir`
(reusable per-project templates, one folder per slot, `db_init`, `apex_init`,
`<group>_before`, `<group>_after`, `db_end`, `apex_end`) and `patch_scripts_dir`
(one-off scripts for a single patch code). Both are read from the *project* root,
never from ADT.ai's own checkout: `config/patch_template/` in this repo is the
reference copy a project takes, exactly as old ADT shipped it.

Split out of `create.py` when it crossed the 20 KB context guard (ADT #254).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.files import _install_file_link, _patch_scripts_folder
from adt_ai.shared.apex_store import ApexStore

# `name.[ENV].sql` restricts a template or script to one target environment (old
# ADT patch.py:1843-1851). A project's own `95_release.[PROD].sql` runs on PROD
# and nowhere else.
_ENV_TAGGED_NAME_RE = re.compile(r"\.\[([^\]]+)\]\.")


def _env_tag(name: str) -> str | None:
    match = _ENV_TAGGED_NAME_RE.search(name)
    return match.group(1) if match else None

def _template_payload(
    root: Path,
    patch_folder: Path,
    config: dict[str, Any],
    folder_name: str,
    patch_code: str,
    target_env: str | None,
) -> list[str]:
    if not config.get("patch_add_templates", True):
        return []
    return _configured_sql_payload(
        root,
        patch_folder,
        root / str(config.get("patch_template_dir") or "config/patch_template") / folder_name,
        config,
        patch_code,
        target_env,
        label = "TEMPLATE",
    )

def _script_payload(
    root: Path,
    patch_folder: Path,
    config: dict[str, Any],
    folder_name: str,
    patch_code: str,
    target_env: str | None,
) -> list[str]:
    """Link the per-patch scripts `scripts.collect_patch_scripts` already moved.

    They sit INSIDE the patch now (ADT #309, was #300), so unlike a template this
    reads the patch folder rather than the project tree. The provenance line still
    names the source path the script came from, because after the move that is all
    that is left of it, the same split an exported object file already uses,
    `PROMPT -- FILE: <repo path>` over `@"./snapshots/<repo path>"`.

    The reader still resolves `patch_scripts_dir` through `_patch_scripts_folder`
    to spell that source path, so writer and reader stay on ONE resolver: they
    disagreed once, and against the shipped `patch_scripts/{$PATCH_CODE}/` default
    the reader looked inside a directory literally named `{$PATCH_CODE}` and every
    generated helper went unlinked (ADT #18).
    """
    if not config.get("patch_add_scripts", True):
        return []
    return _configured_sql_payload(
        root,
        patch_folder,
        patch_folder / settings.scripts_snap_folder(config) / folder_name,
        config,
        patch_code,
        target_env,
        label  = "SCRIPT",
        origin = _patch_scripts_folder(root, config, patch_code) / folder_name,
    )

def _configured_sql_payload(
    root: Path,
    patch_folder: Path,
    folder: Path,
    config: dict[str, Any],
    patch_code: str,
    target_env: str | None,
    *,
    label: str,
    origin: Path | None = None,
) -> list[str]:
    """LINK each file where it already lives, never inline it, never copy it.

    Old ADT `attach_file` (patch.py:1857-1906) wrote `PROMPT -- TEMPLATE: <src>`
    plus the configured `patch_file_link` (`@"./#FILE#"`). Copying the body in
    instead (ADT #264, Jan 2026-08-10: "Use PROMPT + @") turned the init block into
    an anonymous wall of SQL and hid which template actually shipped, so the
    `PROMPT` + `@` pair below is that fix and stays.

    What goes is the snapshot old ADT took alongside it. These two folders hold
    per-project CONFIG, `patch_template_dir` and `patch_scripts_dir`, both under
    `config/`, so snapshotting them grew a `snapshots/config/` subtree that is
    byte-identical on every run of every patch (ADT #288). Jan, 2026-08-10: "you
    create snapshots of the modified files which are in the patch, but not the
    config/template files, which are the same every single run." The exported
    OBJECT files keep their snapshots: `_write_snapshots` transforms each one, so
    those are per-patch artifacts rather than duplicates of a static file.

    The link is relative to the patch folder, derived from where that folder
    actually sits rather than assuming a depth, `patch_root` is configurable.

    ``origin`` splits the two answers apart for a file that has been MOVED into
    the patch (ADT #309): the `PROMPT` names where it came from, the `@` names
    where it now is. A template passes no ``origin`` because for it the two are
    the same place.
    """
    if not folder.exists():
        return []
    rows: list[str] = []
    for path in sorted(folder.glob("*.sql")):
        tagged_env = _env_tag(path.name)
        if tagged_env is not None and tagged_env != (target_env or ""):
            continue
        source = (origin / path.name) if origin is not None else path
        relative = source.relative_to(root).as_posix()
        link = Path(os.path.relpath(path, patch_folder)).as_posix()
        # One empty line above each pair (ADT `#456`, Jan 2026-08-21: *"in patch
        # itself, create empty line above each PROMPT -- TEMPLATE:"*). The
        # `PROMPT` and its `@` are one unit, and a run linking several of them
        # ran them together into a wall where the group headers around them are
        # already spaced. It is a blank line between two complete SQLcl
        # commands, so nothing is buffered across it.
        rows.append("")
        rows.append(f"PROMPT -- {label}: {relative}")
        rows.append(_install_file_link(link, config))
    return rows

def _cached_apex_workspace(root: Path, app_id: int) -> str:
    """The workspace `export_apex` recorded for this app, or "" if it never did.

    `config/internal/apex.db` is the gitignored cache `export_apex` writes and
    `validate` / `dependencies -refresh` already read offline before connecting;
    it carries `workspace` per app id (`export_apex/metadata.py`). Reading it
    here keeps `patch -create` connectionless, the workspace costs no round
    trip. The store keys applications by integer id whatever spelling the caller
    holds, which is the whole reason the old YAML lookup had to try two.
    """
    with ApexStore.load(root) as store:
        application = store.application(app_id)
    if not isinstance(application, dict):
        return ""
    return str(application.get("workspace") or "")

def _apex_environment_payload(root: Path, app_id: int) -> list[str]:
    """Set the workspace ADT knows, or emit nothing at all.

    An app `export_apex` never recorded has no workspace to state, and a guessed
    or blank one fails the deploy at the first APEX call, worse than leaving
    the project's own `apex_init` template in charge, which is what happened for
    every patch before ADT #298 anyway.
    """
    workspace = _cached_apex_workspace(root, app_id)
    if not workspace:
        return []
    block = queries.APEX_ENVIRONMENT_BLOCK.format(workspace = workspace.replace("'", "''"))
    return ["", *block.splitlines()]

def _apex_build_status_payload(
    config: dict[str, Any],
    app_id: int,
    target_env: str | None,
) -> list[str]:
    """`patch_apex_build_status: {PROD: RUN_ONLY}`, per environment, like `.[ENV].`.

    Scoped to the target the way an env-tagged template filename is, so a UAT
    patch cannot lock an application the way a PROD one does. Unconfigured is
    the default and emits nothing: locking an app is never a tool default.
    """
    statuses = config.get("patch_apex_build_status") or {}
    if not isinstance(statuses, dict):
        return []
    build_status = statuses.get(target_env or "")
    if not build_status:
        return []
    block = queries.APEX_BUILD_STATUS_BLOCK.format(
        app_id = app_id,
        build_status = str(build_status).replace("'", "''"),
    )
    return ["", *block.splitlines()]
