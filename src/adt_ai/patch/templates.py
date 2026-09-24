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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.deploy_paths import deploy_log_folder
from adt_ai.patch.files import _install_file_link, _patch_scripts_folder
from adt_ai.patch.generated_helpers import drop_helper_slot
from adt_ai.patch.immutables import NEVER_DROPPED, disabled_link, immutable_drop_helper_type
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared.apex_store import ApexStore

# `name.[ENV].sql` restricts a template or script to one target environment (old
# ADT patch.py:1843-1851). A project's own `95_release.[PROD].sql` runs on PROD
# and nowhere else.
_ENV_TAGGED_NAME_RE = re.compile(r"\.\[([^\]]+)\]\.")

# `--[PROD] ` in front of an install-script line: the line belongs to that one
# target environment (#924 F33). `-create` used to render the script for its own
# `-target`, so a patch built for DEV and promoted to PROD ran DEV's templates,
# DEV's build status and spooled into DEV's log folder. Jan, 2026-09-23:
# *"SHOULDNT THE INSTALL SCRIPT BE TARGET INDEPENDENT?"* Now every environment's
# lines are written, each behind this comment, and `-deploy -target` switches
# on its own in the payload it hands SQLcl (`for_target`). A hand-run in SQLcl
# knows no target, so it reads them as comments and runs only what every
# environment runs.
_ENV_SCOPED_LINE_RE = re.compile(r"^--\[(?P<env>[^\]\s]+)\] ?(?P<line>.*)$")


def _env_tag(name: str) -> str | None:
    match = _ENV_TAGGED_NAME_RE.search(name)
    return match.group(1) if match else None

def env_scoped(rows: list[str], env: str) -> list[str]:
    """``rows`` written for ``env`` only; a blank separator stays blank."""
    return [f"--[{env}] {row}" if row else row for row in rows]

def unscoped(line: str) -> str:
    """``line`` without its environment comment, whichever environment it names.

    For the readers that must see every environment's links at once: the deploy
    fingerprint, which has to change when a PROD-only template does, and the
    `-create` report, which lists what the patch carries for any target.
    """
    match = _ENV_SCOPED_LINE_RE.match(line.strip())
    return match.group("line") if match else line

def for_target(text: str, config: dict[str, Any], target_env: str | None) -> str:
    """The install script as ``target_env`` runs it (#924 F33).

    Two rewrites, both of lines `-create` wrote target-free. A `--[ENV] ` line
    for this target loses its comment and runs; one for any other target stays a
    comment. The SPOOL line naming the environment-free log folder is pointed at
    this target's, the folder `-deploy` reads its spool back from. Every other
    line passes through untouched, so a folder built before this change deploys
    exactly as it did.
    """
    target = (target_env or "").upper()
    spool = _neutral_spool_re(config, target_env)
    rows: list[str] = []
    for line in text.splitlines():
        scoped = _ENV_SCOPED_LINE_RE.match(line.strip())
        if scoped is not None and target and scoped.group("env").upper() == target:
            line = scoped.group("line")
        elif spool is not None and (found := spool.fullmatch(line.strip())):
            line = settings.spool_line(
                config,
                folder = deploy_log_folder(config, target_env),
                schema = found.groupdict().get("schema") or "",
            )
        rows.append(line)
    return "\n".join(rows) + ("\n" if text.endswith("\n") else "")

def _neutral_spool_re(config: dict[str, Any], target_env: str | None) -> re.Pattern[str] | None:
    """The environment-free SPOOL line `-create` writes, with its schema captured.

    Built from `patch_spool_line` itself, the way `deploy_progress._link_pattern`
    reads `patch_file_link`, so a project's own spelling is matched. ``None``
    when there is nothing to move: no target, or a `patch_deploy_logs` naming
    one folder for every environment.
    """
    neutral = deploy_log_folder(config, None)
    if not target_env or neutral == deploy_log_folder(config, target_env):
        return None
    folder, schema = "\x00folder\x00", "\x00schema\x00"
    body = re.escape(settings.spool_line(config, folder=folder, schema=schema))
    if re.escape(folder) not in body:
        return None
    body = body.replace(re.escape(folder), re.escape(neutral), 1)
    return re.compile(body.replace(re.escape(schema), r"(?P<schema>.+?)", 1))

def _template_payload(
    root: Path,
    patch_folder: Path,
    config: dict[str, Any],
    folder_name: str,
    patch_code: str,
) -> list[str]:
    if not config.get("patch_add_templates", True):
        return []
    return _configured_sql_payload(
        root,
        patch_folder,
        root / str(config.get("patch_template_dir") or "config/patch_template") / folder_name,
        config,
        patch_code,
        label = "TEMPLATE",
    )

def _script_payload(
    root: Path,
    patch_folder: Path,
    config: dict[str, Any],
    folder_name: str,
    patch_code: str,
    keep: Callable[[str], bool] | None = None,
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

    ``keep`` selects a subset of the slot by filename. One slot is linked in two
    places since ADT #753: a generated ALTER runs BEFORE the table files and the
    hand-written scripts in the same slot still run after them. See
    `create._database_patch_payload` for why.
    """
    if not config.get("patch_add_scripts", True):
        return []
    return _configured_sql_payload(
        root,
        patch_folder,
        patch_folder / settings.scripts_snap_folder(config) / folder_name,
        config,
        patch_code,
        label  = "SCRIPT",
        origin = _patch_scripts_folder(root, config, patch_code) / folder_name,
        keep   = keep,
    )

def _configured_sql_payload(
    root: Path,
    patch_folder: Path,
    folder: Path,
    config: dict[str, Any],
    patch_code: str,
    *,
    label: str,
    origin: Path | None = None,
    keep: Callable[[str], bool] | None = None,
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
        if keep is not None and not keep(path.name):
            continue
        linked = linked_file_rows(
            root, patch_folder, path, config,
            label  = label,
            source = (origin / path.name) if origin is not None else None,
        )
        # A generated DROP for an immutable object is linked commented out, never
        # run (ADT #830). Name AND slot, as every helper test pairs them.
        immutable = (
            immutable_drop_helper_type(path.name, config)
            if origin is not None and folder.name == drop_helper_slot(config)
            else None
        )
        if immutable is not None:
            linked = disabled_link(linked, immutable, NEVER_DROPPED)
        # Linked for every environment, switched on by `-deploy -target` (#924 F33).
        rows.extend(env_scoped(linked, tagged_env) if tagged_env is not None else linked)
    return rows

def linked_file_rows(
    root: Path,
    patch_folder: Path,
    path: Path,
    config: dict[str, Any],
    *,
    label: str = "TEMPLATE",
    source: Path | None = None,
) -> list[str]:
    """One `PROMPT -- <LABEL>: <project path>` + `@` pair, linking ``path`` in place.

    ``source`` is where the `PROMPT` says the file came from when that differs
    from where the `@` finds it (a moved per-patch script); a template and a
    shared lock script (ADT #850) pass none. The link is relative to the patch
    folder, derived from where that folder sits rather than assuming a depth.

    One empty line above each pair (ADT `#456`, Jan 2026-08-21: *"in patch
    itself, create empty line above each PROMPT -- TEMPLATE:"*). The `PROMPT`
    and its `@` are one unit, and a run linking several ran them together into a
    wall. It is a blank line between two complete SQLcl commands, so nothing is
    buffered across it.
    """
    relative = (source or path).relative_to(root).as_posix()
    link = Path(os.path.relpath(path, patch_folder)).as_posix()
    return ["", f"PROMPT -- {label}: {relative}", _install_file_link(link, config)]

def _cached_apex_workspace(root: Path, app_id: int) -> str:
    """The workspace `export_apex` recorded for this app, or "" if it never did.

    `config/internal/apex.db` is the gitignored cache `export_apex` writes and
    `validate` / `rebuild -app` already read offline before connecting;
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

    **App id `0` is the exception, and it resolves on the target** (ADT #720).
    It is the WORKSPACE group rather than an application, so the store holds no
    row for it and a project whose only APEX artifact is a workspace static file
    may never have exported an application to record one either. Emitting
    nothing there is not neutral: `wwv_flow_files` returns NO ROWS AT ALL until
    a workspace is set, so `#724`'s workspace-file guard queried an empty view
    and passed whatever the target held, and the file's own
    `create_workspace_static_file` failed with `ORA-20001: Package variable
    g_security_group_id must be set`. Both measured 2026-09-09. The target knows
    the answer from the connected schema, so the block asks it there.
    """
    if app_id == 0:
        return ["", *queries.APEX_WORKSPACE_ENVIRONMENT_BLOCK.splitlines()]
    workspace = _cached_apex_workspace(root, app_id)
    if not workspace:
        return []
    block = queries.APEX_ENVIRONMENT_BLOCK.format(workspace = escape_literal(workspace))
    return ["", *block.splitlines()]

def _apex_build_status_payload(
    config: dict[str, Any],
    app_id: int,
) -> list[str]:
    """`patch_apex_build_status: {PROD: RUN_ONLY}`, per environment, like `.[ENV].`.

    Scoped to the target the way an env-tagged template filename is, so a UAT
    deploy cannot lock an application the way a PROD one does. One block per
    configured environment, each behind its `--[ENV] ` comment, and the deploy
    switches on its own target's (#924 F33): baked at `-create`, the status was
    whichever target the patch happened to be built for. Unconfigured is the
    default and emits nothing: locking an app is never a tool default.
    """
    statuses = config.get("patch_apex_build_status") or {}
    if not isinstance(statuses, dict):
        return []
    rows: list[str] = []
    for env, build_status in statuses.items():
        if not env or not build_status:
            continue
        block = queries.APEX_BUILD_STATUS_BLOCK.format(
            app_id = app_id,
            build_status = escape_literal(str(build_status)),
        )
        rows.extend(["", *env_scoped(block.splitlines(), str(env))])
    return rows
