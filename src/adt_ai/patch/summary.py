"""The install script's header block: what this patch contains, in comments.

Old ADT wrote it directly under the `PATCH CODE` / `SCHEMA` header
(`patch.py:1278` -> `get_differences`, `patch.py:1753-1782`) and read it back
later (`get_file_commits`, `patch.py:1041-1056`). ADT.ai dropped it and wrote
`commits.txt` / `files.txt` sidecars instead, two files old ADT never produced,
carrying data the deployable artifact should have carried itself (ADT #258/#259).

Split out of `create.py`, which crossed the 20 KB context guard again.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch import settings
from adt_ai.patch.helpers import _path_is_deleted
from adt_ai.patch.layout import deploy_log_folder
from adt_ai.shared.commit_discovery import CommitRecord


def spool_start(config: dict[str, Any], target_env: str | None, schema: str) -> str:
    """`SPOOL "./logs_<ENV>/<SCHEMA>.log" APPEND;`.

    The script is generated per target, so its log destination is known here,
    which is what lets a hand-run in SQLcl land beside an `adtai patch -deploy`
    run instead of dropping a stray `<SCHEMA>.log` in the folder root (ADT #260).

    The line itself is `patch_spool_line` since ADT #431; the folder it names
    stays `deploy_log_folder`'s answer, because `-deploy` writes its own captured
    copy into that same folder and the two must not be resolved twice.
    """
    return settings.spool_line(
        config,
        folder = deploy_log_folder(config, target_env),
        schema = schema,
    )


def change_summary_comment(
    root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    present_files: Mapping[str, bool],
) -> list[str]:
    """The commits-and-files block, as `--` comments.

    Two jobs, and the second is why the sidecars could go: it tells whoever
    opens the install script what is in it, and it is the machine-readable
    record `parse_patch_folder` reads back. Comments rather than `PROMPT`s
    because `_deployment_payload` strips them, so this costs nothing at deploy
    time, exactly as old ADT had it.
    """
    cache: dict[tuple[str, ...], set[tuple[str, str, str]]] = {}
    deleted = [
        path for path in files
        if _path_is_deleted(
            root, path, config, cache, present=present_files[path],
        )
    ]
    live = [path for path in files if path not in set(deleted)]
    rows = ["--", "-- COMMITS:"]
    rows.extend(f"--   {record.number}) {record.summary}" for record in records)
    for header, group in (
        ("NEW FILES", _files_with_status(live, records, "A")),
        ("DELETED FILES", deleted),
        ("MODIFIED FILES", _files_with_status(live, records, "M")),
    ):
        if not group:
            continue
        rows.append("--")
        rows.append(f"-- {header}:")
        rows.extend(f"--   {path}" for path in sorted(group))
    rows.append("--")
    return rows


def _files_with_status(
    files: list[str],
    records: list[CommitRecord],
    wanted: str,
) -> list[str]:
    """Files whose EARLIEST status in the patch window is ``wanted``.

    A file added and then edited inside one patch is NEW, not MODIFIED, old ADT
    read the same answer off a single `first..last` diff, so the first status in
    the window is the one that decides. A file with no recorded status (a grant
    script injected without a commit behind it) falls into neither group rather
    than being guessed into one; `parse_patch_folder` still sees it, because the
    `@` link line names it.
    """
    present = set(files)
    resolved: dict[str, str] = {}
    for record in records:
        for path, status in record.file_statuses.items():
            if path in present and path not in resolved:
                resolved[path] = status[:1].upper()
    return [path for path, status in resolved.items() if status == wanted]
