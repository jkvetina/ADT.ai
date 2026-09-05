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
    CONTENT_MODE_LOCAL,
    CONTENT_MODE_NOSNAP,
    file_text,
)
from adt_ai.patch.files import (
    _apex_page_id,
    _install_file_link,
    _is_apex_end_environment,
    _is_apex_page,
    _is_apex_set_environment,
    _patch_map,
    _snapshot_link,
)
from adt_ai.patch.full_app import (
    is_full_app as _is_full_app,
)
from adt_ai.patch.helpers import (  # noqa: F401  (re-exported for existing importers)
    _drop_helper_sql,
    _parse_table_columns,
    _path_is_deleted,
    _split_sql_columns,
    _table_alter_sql,
    _table_versions,
    _write_drop_helpers,
    _write_generated_patch_scripts,
    _write_table_diff_helpers,
)
from adt_ai.patch.layout import (
    apex_app_id as _apex_app_id,
)
from adt_ai.patch.layout import (
    apex_app_root as _apex_app_root,
)
from adt_ai.patch.layout import (
    database_object_type as _database_object_type,
)
from adt_ai.patch.layout import (
    is_apex_static_file as _is_apex_static_file,
)
from adt_ai.patch.layout import (
    is_apexlang_path as _is_apexlang_path,
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
    for group in sorted({_patch_group(path, config, owners) for path in files}):
        group_files = [path for path in files if _patch_group(path, config, owners) == group]
        sql_path = folder / _settings.group_script_name(group, config)
        payload = (
            _apex_patch_payload(
                root,
                folder,
                group_files,
                records,
                config,
                patch_code=patch_code,
                full_app_ids=full_app_ids,
                target_env=target_env,
                schema=group,
                content_mode=content_mode,
                present_files=present_files,
            )
            if all(_is_apex_application_path(path, config) for path in group_files)
            else _database_patch_payload(
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
                present_files=present_files,
            )
        )
        text_files.write_text(sql_path, payload)
        sql_files[group] = sql_path
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
    present_files: Mapping[str, bool],
) -> str:
    signatures = signatures or []
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
    guarded = [item for item in signatures if item.file in set(files)]
    payload.extend(_signatures.lock_payload(guarded, config, records=records))
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
        after = [
            *_script_payload(root, folder, config, after_slot, patch_code, target_env),
            *_template_payload(root, folder, config, after_slot, patch_code, target_env),
        ]
        # Old ADT's own condition (patch.py:1401): a group earns a section when it
        # has object files OR scripts. Keying the section off the object files
        # alone is what made a `tables_after/` script vanish from a patch that
        # changed no table, moved out of the source folder by ADT #309 and then
        # linked by nothing, which is the silent non-delivery that card exists to
        # stop. Found by running `-create`, not by a test: every fixture happened
        # to put a script in a slot whose group also had a file.
        if not group_files and not before and not after:
            continue
        payload.extend(["", "PROMPT --;", f"PROMPT -- {group.upper()}", "PROMPT --;"])
        payload.extend(before)
        for path in group_files:
            # Presence belongs to the selected source; deletion remains an
            # object-identity question so a group move never claims a DROP.
            if _path_is_deleted(
                root, path, config, deleted_cache,
                present=present_files[path],
            ):
                payload.append(f"PROMPT -- [DELETED] {path}")
            elif present_files[path]:
                link = _object_link(root, folder, path, config, mode=content_mode)
                payload.extend(_file_link_rows(path, link))
        payload.extend(after)
    # No `PROMPT -- COMMITS` block here: the commit list is written ONCE, in the
    # `--` comment header above (`_change_summary_comment`), exactly as old ADT
    # did it (patch.py:1753-1764). SQLcl echoes every PROMPT, so the invented
    # copy was the only one reaching the deploy log (ADT #263).
    payload.extend(_template_payload(root, folder, config, "db_end", patch_code, target_env))
    # The other half of the pair, after every object is in and before SUCCESS:
    # holding the objects for the rest of the lock's 20 minutes would block the
    # colleague the lock was taken to protect, for no remaining reason.
    payload.extend(_signatures.unlock_payload(guarded, config))
    payload.extend(["", "PROMPT --;", "PROMPT -- SUCCESS", "PROMPT --;"])
    if config.get("patch_spooling", True):
        payload.append(queries.SPOOL_OFF_DIRECTIVE)
    payload.append("")
    return "\n".join(payload)

def _apex_patch_payload(
    root: Path,
    folder: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    patch_code: str,
    full_app_ids: list[int] | None,
    target_env: str | None,
    schema: str,
    content_mode: str = CONTENT_MODE_COMMITTED,
    present_files: Mapping[str, bool],
) -> str:
    app_id = _apex_app_id(files[0], config) or 0
    payload = [
        "PROMPT --;",
        f"PROMPT -- PATCH {patch_code}",
        f"PROMPT -- SCHEMA {schema}",
        f"PROMPT -- APP ID {app_id}",
        "PROMPT --;",
    ]
    payload.extend(_change_summary_comment(
        root, files, records, config, present_files=present_files,
    ))
    payload.extend(_settings.session_directives(config))
    payload.extend(_settings.rollback_directives(config))
    if config.get("patch_spooling", True):
        payload.append(_spool_start(config, target_env, schema))
    payload.extend(_apex_environment_payload(root, app_id))
    payload.extend(_template_payload(root, folder, config, "apex_init", patch_code, target_env))
    if _is_full_app(app_id, full_app_ids):
        for path in files:
            if present_files[path]:
                link = _object_link(root, folder, path, config, mode=content_mode)
                payload.extend(_file_link_rows(path, link))
    else:
        environment_mode = (
            CONTENT_MODE_LOCAL if content_mode == CONTENT_MODE_NOSNAP else content_mode
        )
        set_env = next((path for path in files if _is_apex_set_environment(path)), None)
        end_env = next((path for path in files if _is_apex_end_environment(path)), None)
        if set_env:
            text = file_text(root, set_env, mode=environment_mode, records=records) or ""
            payload.extend(text.splitlines())
            payload.extend(queries.APEX_MODE_REPLACE_BLOCK.splitlines())
        component_files = [
            path
            for path in files
            if not _is_apex_set_environment(path)
            and not _is_apex_end_environment(path)
            and not _is_apex_page(path)
            and not _is_apexlang_path(path, config)
            and present_files[path]
        ]
        page_files = [
            path for path in files
            if _is_apex_page(path)
            and present_files[path]
        ]
        payload.extend(_apexlang_source_payload(files, config))
        for path in component_files:
            payload.extend(
                _file_link_rows(path, _object_link(root, folder, path, config, mode=content_mode))
            )
        deleted_pages = [
            page_id
            for path in files
            if _is_apex_page(path) and path not in page_files
            and (page_id := _apex_page_id(path))
        ]
        if deleted_pages:
            payload.extend(_apex_deleted_pages_payload(deleted_pages))
        if page_files:
            payload.extend(["PROMPT --;", "PROMPT -- APEX PAGES", "PROMPT --;"])
            for path in page_files:
                link = _object_link(root, folder, path, config, mode=content_mode)
                payload.extend(_file_link_rows(path, link))
        if end_env:
            text = file_text(root, end_env, mode=environment_mode, records=records) or ""
            payload.extend(text.splitlines())
    # Same as the database payload: one commit list, in the `--` header (ADT #263).
    payload.extend(_template_payload(root, folder, config, "apex_end", patch_code, target_env))
    payload.extend(_apex_build_status_payload(config, app_id, target_env))
    payload.extend(["", "PROMPT --;", "PROMPT -- SUCCESS", "PROMPT --;"])
    if config.get("patch_spooling", True):
        payload.append(queries.SPOOL_OFF_DIRECTIVE)
    payload.append("")
    return "\n".join(payload)

def _apexlang_source_payload(files: list[str], config: dict[str, Any]) -> list[str]:
    """The rows naming the folder an APEXlang application is imported FROM.

    An `.apx` file has no SQL install route, so the patch links none of them and
    `patch -deploy -app` imports the tree out of the application's own folder in
    the repository. The deploy log has to say so, or a reader counting `@` lines
    against the patch's file list concludes the patch shipped nothing at all.
    Jan, 2026-08-30: *"We should print a note then in the log that app was
    deployed from THAT folder."*

    One row per application folder rather than per file, because the import is
    per application: a page and a shared-components file in one tree are one
    import, and a row each would read as two.
    """
    folders = sorted({
        "/".join((*app_root, APEXLANG_DIR))
        for path in files
        if _is_apexlang_path(path, config)
        and (app_root := _apex_app_root(path, config)) is not None
    })
    if not folders:
        return []
    return [
        "PROMPT --;",
        *(f"PROMPT -- APEXLANG SOURCE: {folder}" for folder in folders),
        "PROMPT -- imported from that folder by patch -deploy -app, not from this patch",
        "PROMPT --;",
    ]

def _file_link_rows(path: str, link: str) -> list[str]:
    """Label one file link in the install script, no invented counter.

    SQLcl echoes each `PROMPT`, so the last marker in a deploy's output is the file
    the run stopped on (ADT #254). A generated `n/m` count used to be baked into
    this same comment and re-parsed from the runtime transcript, but each install
    script counted only its own object-file loop and never the `patch_scripts`
    also linked into it, so the count drifted from reality (ADT #321, Jan: "you
    cant be counting the files based on this counter anyway"). This label is
    read-only prose: `deploy_progress.py` calculates the real total from the `@`
    link this row's second entry is, never from this text, because a label can
    survive a hand-edit that comments out only the `@` line beneath it (Jan:
    "I can have `-- FILE: ...` / `--@file` and the file is listed, counted, but
    not executed").
    """
    return [f"PROMPT -- FILE: {path}", link]

def _apex_deleted_pages_payload(page_ids: list[int]) -> list[str]:
    payload = ["PROMPT --;", "PROMPT -- APEX REMOVE PAGES", "PROMPT --;", "BEGIN"]
    for page_id in sorted(page_ids):
        payload.append(queries.APEX_REMOVE_PAGE_STATEMENT.format(page_id=page_id))
    payload.extend(["END;", "/", "--"])
    return payload

def _object_link(
    root: Path,
    folder: Path,
    path: str,
    config: dict[str, Any],
    *,
    mode: str,
) -> str:
    """The `@` line for one object file, pointing where its content actually is.

    Every mode but ``-nosnap`` links the copy under `snapshots/`. ``-nosnap`` links
    the repo file itself, using the same relative-path derivation `#288` gave
    templates and per-patch scripts, computed from where the patch folder sits
    rather than assuming a depth, because `patch_root` is configurable.

    An APEX static file is never linked in place: what deploys is the generated
    `wwv_flow_imp` wrapper, not the binary, so it keeps its snapshot in all modes.
    """
    if mode == CONTENT_MODE_NOSNAP and not _is_apex_static_file(path, config):
        return _install_file_link(Path(os.path.relpath(root / path, folder)).as_posix(), config)
    return _install_file_link(_snapshot_link(path, config), config)

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
    "_table_alter_sql",
    "_write_generated_patch_scripts",
    "_write_patch_files",
    "annotations",
    "apex_owner_schemas",
    "install_script_name",
    "os",
    "queries",
    "text_files",
]
