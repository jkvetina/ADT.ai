"""The install scripts of an APEX application, one per legacy export and two per APEXlang tree.

Split out of `create.py` by ADT #735, which is also where the two-script shape
comes from. A legacy `.sql` or full-export application is installed by the
script itself, so it gets one; an APEXlang application is installed by the
`apex import` that `patch -deploy -app` issues between its `init` and `end`
halves, so it gets two. `create._write_patch_files` picks the shape per group.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, stages
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
    _is_apex_end_environment,
    _is_apex_page,
    _is_apex_set_environment,
)
from adt_ai.patch.full_app import (
    is_full_app as _is_full_app,
)
from adt_ai.patch.install_links import _file_link_rows, _object_link
from adt_ai.patch.layout import (
    apex_app_id as _apex_app_id,
)
from adt_ai.patch.layout import (
    apex_app_root as _apex_app_root,
)
from adt_ai.patch.layout import (
    is_apexlang_path as _is_apexlang_path,
)
from adt_ai.patch.summary import (
    change_summary_comment as _change_summary_comment,
)
from adt_ai.patch.summary import (
    spool_start as _spool_start,
)
from adt_ai.patch.templates import (
    _apex_build_status_payload,
    _apex_environment_payload,
    _template_payload,
)
from adt_ai.shared.apex_paths import APEXLANG_DIR
from adt_ai.shared.commit_discovery import CommitRecord


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
    workspace: list[_signatures.WorkspaceArtifact] | None = None,
    present_files: Mapping[str, bool],
) -> str:
    """One install script for an application the script itself installs.

    A full export links its `f<id>.sql`, a legacy `.sql` application its
    components and pages; either way the application lands from inside this
    script, so its `apex_init` and `apex_end` templates sit around a payload
    that is really here. An APEXlang application is the other shape and gets
    `_apexlang_patch_payloads`.
    """
    app_id = _apex_app_id(files[0], config) or 0
    payload = _apex_script_opening(
        root, files, records, config,
        patch_code=patch_code, target_env=target_env, schema=schema, app_id=app_id,
        spool_as=schema, present_files=present_files,
    )
    payload.extend(_apex_environment_payload(root, app_id))
    payload.extend(_template_payload(root, folder, config, "apex_init", patch_code, target_env))
    # A workspace static file takes THIS route rather than the database one, so
    # its guard rides here (ADT #724). Below `apex_init` on purpose: measured on
    # SANDBOX 2026-09-07, `wwv_flow_files` answers no rows at all until a
    # workspace is set, and this is where the script sets it -- through
    # `APEX ENVIRONMENT` when `export_apex` recorded a workspace for the
    # application, and through the project's own template when it did not. Still
    # above every `@` line, which is what the guard is for.
    payload.extend(
        _signatures.workspace_lock_payload(
            root, folder,
            [item for item in (workspace or []) if item.file in set(files)],
            config,
            records=records,
        )
    )
    if _is_full_app(app_id, full_app_ids):
        for path in files:
            if present_files[path]:
                link = _object_link(root, folder, path, config, mode=content_mode)
                payload.extend(_file_link_rows(path, link))
    else:
        payload.extend(_apex_component_rows(
            root, folder, files, records, config,
            content_mode=content_mode, present_files=present_files,
        ))
    # Same as the database payload: one commit list, in the `--` header (ADT #263).
    payload.extend(_template_payload(root, folder, config, "apex_end", patch_code, target_env))
    payload.extend(_apex_build_status_payload(config, app_id, target_env))
    payload.extend(_apex_script_closing(config))
    return "\n".join(payload)


def _apexlang_patch_payloads(
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
) -> dict[str, str]:
    """The two scripts of an APEXlang application, keyed by the group each is named by.

    An APEXlang application is installed by the `apex import` that `patch
    -deploy -app` issues, a SQLcl command and never a file in the patch, so one
    script cannot carry it. Written as one anyway, `<SCHEMA>.1000.sql` set the
    workspace, linked `apex_init`, then `apex_end`, and printed `SUCCESS` with
    the import nowhere inside it; Jan, 2026-09-07: *"the whole [one-script] file
    is a lie, it is just empty shell file with no actual app being installed
    ... we need a file before and after"* (ADT #735).

    `<SCHEMA>.<APP>.init.sql` is everything that ran before the import, the
    workspace block, the `apex_init` slot, the `APEXLANG SOURCE:` rows naming
    the folder the import reads; `<SCHEMA>.<APP>.end.sql` is everything that ran
    after it, the `apex_end` slot and the build status. The deploy loop puts the
    import between them (`deploy_sequence.deployment_sequence`), and each half spools
    to its own log because the log is named after the script.

    ``full_app_ids`` is accepted for signature parity with `_apex_patch_payload`
    and never consulted: ADT #606 rules an APEXlang application out of the full
    set by construction.
    """
    del full_app_ids
    app_id = _apex_app_id(files[0], config) or 0
    init_group = stages.staged_group(schema, stages.APP_SCRIPT_INIT)
    end_group = stages.staged_group(schema, stages.APP_SCRIPT_END)
    init = _apex_script_opening(
        root, files, records, config,
        patch_code=patch_code, target_env=target_env, schema=schema, app_id=app_id,
        spool_as=init_group, present_files=present_files,
    )
    init.extend(_apex_environment_payload(root, app_id))
    init.extend(_template_payload(root, folder, config, "apex_init", patch_code, target_env))
    init.extend(_apex_component_rows(
        root, folder, files, records, config,
        content_mode=content_mode, present_files=present_files,
    ))
    init.extend(_apex_script_closing(config))
    end = _apex_script_opening(
        root, files, records, config,
        patch_code=patch_code, target_env=target_env, schema=schema, app_id=app_id,
        spool_as=end_group, present_files=present_files,
    )
    end.extend(_template_payload(root, folder, config, "apex_end", patch_code, target_env))
    end.extend(_apex_build_status_payload(config, app_id, target_env))
    end.extend(_apex_script_closing(config))
    return {init_group: "\n".join(init), end_group: "\n".join(end)}


def _is_apexlang_application(files: list[str], config: dict[str, Any]) -> bool:
    """Does this application group ship as an APEXlang tree?

    One reader for the question `_apex_copy_files` and `_apexlang_source_payload`
    each answer inline: a group holding any `.apx` path is imported from its
    folder by `patch -deploy -app`, whatever else the window changed beside it.
    """
    return any(_is_apexlang_path(path, config) for path in files)


def _apex_script_opening(
    root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    patch_code: str,
    target_env: str | None,
    schema: str,
    app_id: int,
    spool_as: str,
    present_files: Mapping[str, bool],
) -> list[str]:
    """The header, the change summary, the session directives and the SPOOL.

    ``schema`` is what the `PROMPT -- SCHEMA` row says and ``spool_as`` what the
    log is named after; they differ only for the two halves of an APEXlang
    script, which share one application and cannot share one spool.
    """
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
        payload.append(_spool_start(config, target_env, spool_as))
    return payload


def _apex_script_closing(config: dict[str, Any]) -> list[str]:
    payload = ["", "PROMPT --;", "PROMPT -- SUCCESS", "PROMPT --;"]
    if config.get("patch_spooling", True):
        payload.append(queries.SPOOL_OFF_DIRECTIVE)
    payload.append("")
    return payload


def _apex_component_rows(
    root: Path,
    folder: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    content_mode: str,
    present_files: Mapping[str, bool],
) -> list[str]:
    """The rows between the `apex_init` and `apex_end` slots of a non-full application.

    The environment scripts a legacy import runs, the `APEXLANG SOURCE:` rows
    naming where an APEXlang import reads from, the component and page links,
    and the pages the window deleted. For an APEXlang application these are the
    `init` half's business: whatever a script still installs by SQL runs before
    the tree lands on it.
    """
    payload: list[str] = []
    environment_mode = (
        CONTENT_MODE_LOCAL if content_mode == CONTENT_MODE_NOSNAP else content_mode
    )
    set_env = next((path for path in files if _is_apex_set_environment(path)), None)
    end_env = next((path for path in files if _is_apex_end_environment(path)), None)
    if set_env:
        text = file_text(
            root, set_env, mode=environment_mode, records=records, config=config
        ) or ""
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
        text = file_text(
            root, end_env, mode=environment_mode, records=records, config=config
        ) or ""
        payload.extend(text.splitlines())
    return payload

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

def _apex_deleted_pages_payload(page_ids: list[int]) -> list[str]:
    payload = ["PROMPT --;", "PROMPT -- APEX REMOVE PAGES", "PROMPT --;", "BEGIN"]
    for page_id in sorted(page_ids):
        payload.append(queries.APEX_REMOVE_PAGE_STATEMENT.format(page_id=page_id))
    payload.extend(["END;", "/", "--"])
    return payload
