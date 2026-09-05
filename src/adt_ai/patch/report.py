"""The per-schema report `-create` prints while it builds (ADT #276 / #277).

ADT.ai's `-create` printed `PATCH FILES:`, the folder, and one path per schema.
Old ADT printed a section per schema naming every file it processed, which commit
each one came from, which of them changed a table, and which carried no commit at
all. Losing that made the command silent about the two things a reviewer checks
before deploying: what is in the patch, and whether any of it is stale.

This module answers those questions as DATA; `cli/patch_create_render.py` owns how
it looks. The split is what lets the shape be pinned by a console test and the
commit arithmetic by a unit test, instead of one test doing both badly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch.content import (
    CONTENT_MODE_HEAD,
    CONTENT_MODE_LOCAL,
    authoritative_commit,
    newer_commits,
)
from adt_ai.patch.create import _patch_group
from adt_ai.patch.helpers import _path_is_deleted
from adt_ai.patch.models import GeneratedScripts, ProcessedFile, SchemaReport
from adt_ai.patch.object_identity import _object_identity
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_files import git_status_paths

# `PROMPT -- TEMPLATE: <path>` / `PROMPT -- SCRIPT: <path>`, written by
# `templates._configured_sql_payload`. Read back off the generated script rather
# than re-derived from config: the writer already resolved `{$PATCH_CODE}` and
# applied the `[ENV]` filter, and a second derivation of that path is exactly the
# drift ADT #18 was.
_INJECTED_RE = re.compile(r"^PROMPT -- (?:TEMPLATE|SCRIPT): (?P<path>.+?)\s*$")

# `MARKER_DELETED` and `MARKER_NEW` stood here until ADT #465. The row markers
# they spelled are gone from the console: `[DELETED]` became the `DELETED
# OBJECTS:` section, `[ALT:n]` became `ALTER STATEMENTS:`, and `[NEW]` was
# dropped outright on Jan's *"no need to bother with new files"*.


def build_reports(
    root: Path,
    files: list[str],
    sql_files: dict[str, Path],
    records: list[CommitRecord],
    window: list[CommitRecord],
    config: dict[str, Any],
    *,
    mode: str,
    generated: GeneratedScripts,
    present_files: Mapping[str, bool],
) -> list[SchemaReport]:
    written = set(generated.paths)
    deleted_cache: dict[tuple[str, ...], set[tuple[str, str, str]]] = {}
    reports: list[SchemaReport] = []
    for group in sorted(sql_files):
        schema, app_id = _split_group(group)
        group_files = [path for path in files if _patch_group(path, config) == group]
        rows = [
            _object_row(
                root,
                path,
                records,
                window,
                config,
                deleted_cache,
                mode = mode,
                present = present_files[path],
            )
            for path in group_files
        ]
        rows.extend(_injected_rows(sql_files[group], records, window, mode=mode))
        reports.append(
            SchemaReport(
                schema      = schema,
                app_id      = app_id,
                files       = rows,
                alter_files = sorted(
                    helper.path for helper in generated.alters if helper.source in group_files
                ),
                uncommitted = _uncommitted(root, rows, written, mode=mode),
                # Established once here rather than re-walked at render time, the
                # same reason `alter_files` is a field (ADT #465). Split into
                # objects and the rest by ADT #506: `DELETED OBJECTS:` prints
                # `TYPE | NAME` now, and the identity is what `_path_is_deleted`
                # already resolved to decide the row in the first place.
                deleted_objects = _deleted_objects(rows, config),
                deleted_scripts = _deleted_scripts(rows, config),
                object_count = len(group_files),
            )
        )
    return reports


def _deleted_objects(
    rows: list[ProcessedFile],
    config: dict[str, Any],
) -> list[tuple[str, str]]:
    """The dropped objects as `(TYPE, NAME)`, for `DELETED OBJECTS:` (ADT #506).

    Through `_object_identity`, the one reader of what a repo path IS, so this
    listing and the DROP helpers `_write_drop_helpers` generates cannot disagree
    about which object a file holds. Deduplicated because a `PACKAGE` and its
    `PACKAGE BODY` are two objects while a group move is one object at two
    paths, and only the second can present twice here.
    """
    seen: dict[tuple[str, str], None] = {}
    for row in rows:
        if not row.deleted:
            continue
        identity = _object_identity(row.path, config)
        if identity is not None:
            seen.setdefault((identity[1], identity[2]), None)
    return list(seen)


def _deleted_scripts(rows: list[ProcessedFile], config: dict[str, Any]) -> list[str]:
    """Dropped paths that hold no database object, kept as plain path rows.

    A per-patch script or a template is genuinely gone and has no type or name
    to render, so dropping it from the section to make every row fit one shape
    would lose the only place the run says so.
    """
    return [
        row.path
        for row in rows
        if row.deleted and _object_identity(row.path, config) is None
    ]


def _uncommitted(
    root: Path,
    rows: list[ProcessedFile],
    written: set[str],
    *,
    mode: str,
) -> list[str]:
    """The files that genuinely have uncommitted changes, asked of git (ADT #444).

    This used to list every row whose commit was not among the SELECTED records,
    which is not what the word means and is not a question about the working
    tree at all. A template slot and a grant script are pulled in from disk by
    design and can never carry a selected commit, so every build warned about
    them: measured on a live project 2026-08-21, all five files under the header were
    tracked and unmodified. Jan: *"Dont show me template files as uncommitted
    files, unless they actually has some uncommitted changes and we are not in
    -local mode!"*

    So it asks git, through the same helper the `local` flag already uses
    (`patch/files.py`), and a clean path drops out whatever its commit story is.
    An untracked file still reports (`??`), which is the case the warning was
    written for.

    **Silent under `-local`**, per the second half of that sentence: that mode
    ships the working tree on purpose, so an uncommitted file there is the
    instruction rather than a surprise, and warning about it would fire on every
    file the mode exists to carry.

    A helper THIS run generated is still excluded (``written``). Old ADT listed
    every one of them (patch.py:1630-1632) and on any patch that drops an object
    or changes a table they outnumber and bury the actionable entry; they remain
    visible in the listing above, on the same `  - ` row as every other file
    since `#456` retired the `!` this sentence used to name.

    Asked of git in ONE batched call rather than one `git status` subprocess
    per row (`#670`): a patch of a few hundred files used to spawn a few
    hundred processes here for a question `git_status_paths` answers in one.
    """
    if mode == CONTENT_MODE_LOCAL:
        return []
    candidates = [row.path for row in rows if row.path not in written]
    statuses = git_status_paths(root, candidates)
    return [path for path in candidates if path in statuses]


def _split_group(group: str) -> tuple[str, int | None]:
    """`APP` -> (`APP`, None); `APEX.100` -> (`APEX`, 100).

    `_patch_group` folds the APEX application id into the group name so one patch
    file is written per app; the report unfolds it again so `PROCESSED FILES:` can
    append `APEX.100`. It fed `PROCESSING SCHEMA APEX APP 100:` until ADT #444
    removed that header.
    """
    schema, _, app = group.partition(".")
    return schema, int(app) if app.isdigit() else None


def _object_row(
    root: Path,
    path: str,
    records: list[CommitRecord],
    window: list[CommitRecord],
    config: dict[str, Any],
    deleted_cache: dict[tuple[str, ...], set[tuple[str, str, str]]],
    *,
    mode: str,
    present: bool,
) -> ProcessedFile:
    record = authoritative_commit(path, records)
    number = record.number if record else None
    return ProcessedFile(
        path          = path,
        # The whole marker derivation lived here until ADT #465. Two of its three
        # answers became sections and the third was dropped, so all that is left
        # is the one fact `DELETED OBJECTS:` needs: the patch ships a DROP helper
        # for this path rather than its content, because the file is gone. Asked
        # of the OBJECT rather than the path since ADT #499, so a group-moved
        # file reports as still present.
        deleted       = _path_is_deleted(
            root, path, config, deleted_cache, present=present,
        ),
        # The question `_write_snapshots` asks before it copies anything, and the
        # one `_database_patch_payload` asks before it links (ADT #511). Answered
        # once here so a row leaves the listing exactly when the install script
        # has nothing to run for it: a group move's old side is neither carried
        # nor deleted, and it used to be listed as though it were carried.
        carried       = present,
        commit_number = number,
        newer         = [] if mode == CONTENT_MODE_HEAD else newer_commits(path, window, number),
    )


def _injected_rows(
    sql_path: Path,
    records: list[CommitRecord],
    window: list[CommitRecord],
    *,
    mode: str,
) -> list[ProcessedFile]:
    """Templates and per-patch scripts, in the order the install script links them.

    Old ADT split these rows with ``>`` and ``!`` (patch.py:1615), a script the
    project committed against one this run generated. `#456` prints one dash for
    every row on Jan's instruction, so the split lives on only where it says
    something in words: a file with no commit behind it is what the
    `UNCOMMITTED FILES` warning reports.
    """
    rows: list[ProcessedFile] = []
    seen: set[str] = set()
    for line in sql_path.read_text(encoding="utf-8").splitlines():
        match = _INJECTED_RE.match(line)
        if not match:
            continue
        path = match.group("path")
        if path in seen:
            continue
        seen.add(path)
        record = authoritative_commit(path, records)
        number = record.number if record else None
        rows.append(
            ProcessedFile(
                path          = path,
                # A template or script the install header links is on disk by
                # construction, so it is never a drop.
                deleted       = False,
                commit_number = number,
                newer         = (
                    [] if mode == CONTENT_MODE_HEAD else newer_commits(path, window, number)
                ),
            )
        )
    return rows
