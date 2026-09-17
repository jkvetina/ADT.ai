from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch import queries
from adt_ai.patch import settings as _settings
from adt_ai.patch import signatures as _signatures
from adt_ai.patch.content import (
    CONTENT_MODE_COMMITTED,
    CONTENT_MODE_NOSNAP,
)
from adt_ai.patch.create_apex import (
    _apex_patch_payload,
    _apexlang_patch_payloads,
    _is_apexlang_application,
)
from adt_ai.patch.files import (
    _patch_map,
)
from adt_ai.patch.generated_helpers import is_alter_helper_filename
from adt_ai.patch.helpers import (  # noqa: F401  (re-exported for existing importers)
    _drop_helper_sql,
    _path_is_deleted,
    _table_versions,
    _write_drop_helpers,
    _write_generated_patch_scripts,
    _write_table_diff_helpers,
    table_alter_sql,
)
from adt_ai.patch.immutables import NEVER_RECREATED, disabled_link
from adt_ai.patch.install_links import _file_link_rows, _object_link
from adt_ai.patch.layout import (
    database_object_type as _database_object_type,
)
from adt_ai.patch.selection import (  # noqa: F401  (re-exported for existing importers)
    _apex_copy_files,
    _apex_patch_sort_key,
    _component_sort_key,
    _database_patch_sort_key,
    _is_apex_application_path,
    _patch_files,
    _patch_group,
    _patch_object_order,
    _patch_sort_key,
    _refresh_apex_components,
    _refresh_database_files,
    apex_owner_schemas,
    install_script_name,
)
from adt_ai.patch.summary import (
    change_summary_comment as _change_summary_comment,
)
from adt_ai.patch.summary import (
    spool_start as _spool_start,
)
from adt_ai.patch.templates import (  # noqa: F401  (re-exported for existing importers)
    _apex_build_status_payload,
    _apex_environment_payload,
    _configured_sql_payload,
    _env_tag,
    _script_payload,
    _template_payload,
)
from adt_ai.shared import text_files
from adt_ai.shared.apex_paths import APEXLANG_DIR
from adt_ai.shared.commit_discovery import CommitRecord


def _write_patch_files(
    root: Path,
    folder: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    patch_code: str,
    full_app_ids: list[int] | None,
    target_env: str | None,
    content_mode: str = CONTENT_MODE_COMMITTED,
    present_files: Mapping[str, bool],
    never_recreated: Mapping[str, str] | None = None,
) -> dict[str, Path]:
    sql_files: dict[str, Path] = {}
    # One store read for the whole write, the same reason `_patch_files` reads it
    # once: the group an APEX file lands in is the application's own schema.
    owners = apex_owner_schemas(root)
    # Collected once for the whole patch rather than per group: the identity of
    # every guarded object is one walk of the file list, and each schema's script
    # takes the slice that belongs to it.
    signatures = _signatures.collect_signatures(
        root, files, config, present_files=present_files,
    )
    # The schema-level half, on the same walk and for the same reason: an ORDS
    # module and a workspace static file belong to no application, so neither the
    # object guard nor `#592`'s application checksum covers them (ADT #724).
    workspace = _signatures.collect_workspace_signatures(
        root, files, config, present_files=present_files,
    )
    for group in sorted({_patch_group(path, config, owners) for path in files}):
        group_files = [path for path in files if _patch_group(path, config, owners) == group]
        if all(_is_apex_application_path(path, config) for path in group_files):
            # An APEXlang application is two scripts around the import that
            # `patch -deploy -app` issues (ADT #735), `init` before it and `end`
            # after it; any other application is one script that installs itself.
            if _is_apexlang_application(group_files, config):
                payloads = _apexlang_patch_payloads(
                    root, folder, group_files, records, config,
                    patch_code    = patch_code,
                    full_app_ids  = full_app_ids,
                    target_env    = target_env,
                    schema        = group,
                    content_mode  = content_mode,
                    present_files = present_files,
                )
            else:
                payloads = {
                    group: _apex_patch_payload(
                        root, folder, group_files, records, config,
                        patch_code    = patch_code,
                        full_app_ids  = full_app_ids,
                        target_env    = target_env,
                        schema        = group,
                        content_mode  = content_mode,
                        workspace     = workspace,
                        present_files = present_files,
                    )
                }
        else:
            payloads = {
                group: _database_patch_payload(
                    root,
                    folder,
                    group_files,
                    records,
                    config,
                    patch_code=patch_code,
                    target_env=target_env,
                    schema=group,
                    content_mode=content_mode,
                    signatures=signatures,
                    workspace=workspace,
                    present_files=present_files,
                    never_recreated=never_recreated,
                )
            }
        for script_group, payload in payloads.items():
            sql_path = folder / _settings.group_script_name(script_group, config)
            text_files.write_text(sql_path, payload)
            sql_files[script_group] = sql_path
    return sql_files

def _database_patch_payload(
    root: Path,
    folder: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    patch_code: str,
    target_env: str | None,
    schema: str,
    content_mode: str = CONTENT_MODE_COMMITTED,
    signatures: list[_signatures.PatchObject] | None = None,
    workspace: list[_signatures.WorkspaceArtifact] | None = None,
    present_files: Mapping[str, bool],
    never_recreated: Mapping[str, str] | None = None,
) -> str:
    signatures = signatures or []
    workspace = workspace or []
    never_recreated = never_recreated or {}
    payload = [
        "PROMPT --;",
        f"PROMPT -- PATCH {patch_code}",
        f"PROMPT -- SCHEMA {schema}",
        "PROMPT --;",
    ]
    payload.extend(_change_summary_comment(
        root, files, records, config, present_files=present_files,
    ))
    payload.extend(_settings.session_directives(config))
    payload.extend(_settings.rollback_directives(config))
    if config.get("patch_spooling", True):
        payload.append(_spool_start(config, target_env, schema))
    payload.extend(_template_payload(root, folder, config, "db_init", patch_code, target_env))
    # The guard sits here, at the top of the driving file, and is never emitted
    # above an individual object. Jan, 2026-09-02: *"I like 2 clean blocks (lock
    # at the start, unlock at the end) way more."* It is also the only placement
    # that cannot half-apply a patch: DDL does not roll back, so a guard that
    # refuses on object 10 of 10 leaves nine already overwritten.
    carried = set(files)
    guarded = [item for item in signatures if item.file in carried]
    payload.extend(_signatures.lock_payload(root, folder, guarded, config, records=records))
    payload.extend(
        _signatures.workspace_lock_payload(
            root, folder,
            [item for item in workspace if item.file in carried], config, records=records,
        )
    )
    deleted_cache: dict[tuple[str, ...], set[tuple[str, str, str]]] = {}
    for group in _payload_groups(files, config):
        group_files = [path for path in files if _database_patch_group(path, config) == group]
        # `patch_postfix_*` through the resolver `_known_slots` also reads (#430).
        before_slot = _settings.slot_name(group, "before", config)
        after_slot = _settings.slot_name(group, "after", config)
        before = [
            *_script_payload(root, folder, config, before_slot, patch_code, target_env),
            *_template_payload(root, folder, config, before_slot, patch_code, target_env),
        ]
        # The generated ALTERs leave the `after` slot they are WRITTEN to and run
        # ahead of the object files (ADT #753). A table with a generated ALTER
        # already exists in the target, so its exported file contributes only a
        # no-op `CREATE TABLE IF NOT EXISTS` and its `COMMENT ON COLUMN` lines,
        # and those describe the shape the ALTER is about to produce. Run after
        # the file, an added column's comment is `ORA-00904: invalid identifier`
        # and the whole patch rolls back, which is every "add a column and deploy
        # it" on any project whose export carries comments (measured on the
        # `patch/table_change` story fixture, 2026-09-09).
        #
        # A hand-written script in the same slot keeps its place behind the
        # files: that one was put there by a person who meant "after".
        alters = _script_payload(
            root, folder, config, after_slot, patch_code, target_env,
            keep=is_alter_helper_filename,
        )
        after = [
            *_script_payload(
                root, folder, config, after_slot, patch_code, target_env,
                keep=lambda name: not is_alter_helper_filename(name),
            ),
            *_template_payload(root, folder, config, after_slot, patch_code, target_env),
        ]
        # Old ADT's own condition (patch.py:1401): a group earns a section when it
        # has object files OR scripts. Keying the section off the object files
        # alone is what made a `tables_after/` script vanish from a patch that
        # changed no table, moved out of the source folder by ADT #309 and then
        # linked by nothing, which is the silent non-delivery that card exists to
        # stop. Found by running `-create`, not by a test: every fixture happened
        # to put a script in a slot whose group also had a file.
        if not group_files and not before and not after and not alters:
            continue
        payload.extend(["", "PROMPT --;", f"PROMPT -- {group.upper()}", "PROMPT --;"])
        payload.extend(before)
        payload.extend(alters)
        for path in group_files:
            # Presence belongs to the selected source; deletion remains an
            # object-identity question so a group move never claims a DROP.
            if _path_is_deleted(
                root, path, config, deleted_cache,
                present=present_files[path],
            ):
                payload.append(f"PROMPT -- [DELETED] {path}")
            elif present_files[path] and not _is_data_companion(root, path):
                link = _object_link(root, folder, path, config, mode=content_mode)
                rows = _file_link_rows(path, link)
                # The target holds this object and the file would create it
                # again, which `immutables` forbids (ADT #830).
                if path in never_recreated:
                    rows = disabled_link(rows, never_recreated[path], NEVER_RECREATED)
                payload.extend(rows)
        payload.extend(after)
    # No `PROMPT -- COMMITS` block here: the commit list is written ONCE, in the
    # `--` comment header above (`_change_summary_comment`), exactly as old ADT
    # did it (patch.py:1753-1764). SQLcl echoes every PROMPT, so the invented
    # copy was the only one reaching the deploy log (ADT #263).
    payload.extend(_template_payload(root, folder, config, "db_end", patch_code, target_env))
    # The other half of the pair, after every object is in and before SUCCESS:
    # holding the objects for the rest of the lock's 20 minutes would block the
    # colleague the lock was taken to protect, for no remaining reason.
    payload.extend(_signatures.unlock_payload(root, folder, guarded, config))
    payload.extend(["", "PROMPT --;", "PROMPT -- SUCCESS", "PROMPT --;"])
    if config.get("patch_spooling", True):
        payload.append(queries.SPOOL_OFF_DIRECTIVE)
    payload.append("")
    return "\n".join(payload)

def _payload_groups(files: list[str], config: dict[str, Any]) -> list[str]:
    """Every group the install script may open a section for, in `patch_map` order.

    Every configured group is a candidate, not just the ones the patch has files
    for: a group can earn its section on scripts alone (old ADT patch.py:1392-1401).
    `objects` is appended when the map does not declare it, because that is the
    fallback `_database_patch_group` hands back for an object type no group claims.

    The order is `patch_map`'s, which is also the order `_patch_sort_key` already
    sorted the files into, so the sections stay in the sequence a reader of the
    config expects, whether a group arrived by file or by script.
    """
    groups = list(_patch_map(config))
    if "objects" not in groups:
        groups.append("objects")
    for path in files:
        group = _database_patch_group(path, config)
        # defensive: `_database_patch_group` only returns a `patch_map` key (already in `groups`)
        # or the "objects" fallback (already ensured above), so this can never be new
        if group not in groups:  # pragma: no cover
            groups.append(group)
    return groups


def _is_data_companion(root: Path, path: str) -> bool:
    """A file `export_data` wrote beside a table's MERGE, which the MERGE runs itself.

    The CSV is the MERGE's input, not a script, and a `data/<table>/` folder
    (recognised by the `<table>.csv` beside it) holds one file per LOB value plus
    the per-row script that loads it, which the MERGE calls with `@@`. Linking any
    of them made SQLcl run a PNG as SQL and every LOB script twice (`#811`). They
    stay in the selection, so the snapshot still carries the scripts `@@` names.
    """
    relative = Path(path)
    if relative.suffix.lower() == ".csv":
        return True
    return (root / relative.parent).with_suffix(".csv").is_file()


def _database_patch_group(path: str, config: dict[str, Any]) -> str:
    object_type = _database_object_type(path, config)
    for group, object_types in _patch_map(config).items():
        if object_type in {item.upper() for item in object_types}:
            return group
    return "objects"


__all__ = [
    "APEXLANG_DIR",
    "Any",
    "CONTENT_MODE_COMMITTED",
    "CONTENT_MODE_NOSNAP",
    "CommitRecord",
    "Path",
    "_drop_helper_sql",
    "_patch_files",
    "_patch_group",
    "_refresh_apex_components",
    "_refresh_database_files",
    "_write_generated_patch_scripts",
    "_write_patch_files",
    "annotations",
    "table_alter_sql",
    "apex_owner_schemas",
    "install_script_name",
    "os",
    "queries",
    "text_files",
]
