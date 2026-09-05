"""Copying a patch's files into its `snapshots/` folder.

Split out of `files.py` when ADT #280 pushed it past the 20 KB context guard
(`tests/contracts/test_context_file_size.py`). The seam is the question each half
answers: `files.py` decides WHICH files a patch involves and where its scripts
and archives live; this decides what lands in `snapshots/`, in which version, and
with which transforms applied.

`content.py` is the other half of that second question, it resolves the bytes,
this writes them.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from adt_ai.patch import queries
from adt_ai.patch import settings as _settings
from adt_ai.patch.content import (
    CONTENT_MODE_COMMITTED,
    CONTENT_MODE_NOSNAP,
    file_bytes,
    file_text,
)
from adt_ai.patch.files import _is_apex_page
from adt_ai.patch.layout import (
    database_object_type as _database_object_type,
)
from adt_ai.patch.layout import (
    is_apex_static_file as _is_apex_static_file,
)
from adt_ai.patch.layout import (
    is_apexlang_path,
)
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.mime import guess_mime_type


def _write_snapshots(
    root: Path,
    folder: Path,
    files: list[str],
    config: dict[str, Any],
    *,
    patch_code: str,
    content_mode: str = CONTENT_MODE_COMMITTED,
    records: list[CommitRecord] | None = None,
) -> None:
    """Copy each file into the patch, in the version ``content_mode`` selects.

    ``-nosnap`` writes nothing at all: the install script links the repo file
    where it lives instead (ADT #280, Jan's third mode). The two transforms
    `_snapshot_content` applies, the forced-view rewrite and the APEX page audit
    columns, therefore do not happen under that mode, because there is no copy
    to apply them to. That is the cost of shipping no snapshot, and it is why
    `-nosnap` is opt-in rather than the default.

    An APEX static file is the one exception: it has no runnable form in the repo
    (what deploys is the `wwv_flow_imp` wrapper generated below), so it is written
    in every mode.

    An APEXlang file is the mirror image and skipped in every mode (ADT #602):
    the application is imported from its own folder, so a copy here would be one
    nothing opens.
    """
    if records is None:
        records = []
    for path in files:
        # An APEXlang file deploys from where it LIVES, in every mode (ADT #602).
        # `apex import` reads the application's own folder, so a copy under
        # `snapshots/` is one nothing ever opens, and on a 10 000 file
        # application that copy is the entire cost of changing one page. Jan,
        # 2026-08-30: *"I dont want to copy all 10000 files into snapshot
        # folder ... deploy the app from its true location (like we would run
        # -nosnap mode, but just for this app)."*
        if is_apexlang_path(path, config):
            continue
        static = _is_apex_static_file(path, config)
        if content_mode == CONTENT_MODE_NOSNAP and not static:
            continue
        target = folder / _settings.snapshots_folder(config) / path
        if static:
            payload = file_bytes(root, path, mode=content_mode, records=records)
            if payload is None:
                continue
            target = target.with_suffix(target.suffix + ".sql")
            target.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(target, _apex_static_file_sql(path, payload))
            continue
        text = file_text(root, path, mode=content_mode, records=records)
        if text is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_text(target, _snapshot_content(text, path, config, patch_code))


def _snapshot_content(text: str, path: str, config: dict[str, Any], patch_code: str) -> str:
    if config.get("patch_force_views") and _database_object_type(path, config) == "VIEW":
        return re.sub(
            r"\bcreate\s+or\s+replace\s+view\b",
            "create or replace force view",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    if _is_apex_page(path):
        safe_patch_code = patch_code.replace("'", "''")
        text = re.sub(
            r",p_last_updated_by=>'[^']+'",
            f",p_last_updated_by=>'{safe_patch_code}'",
            text,
        )
        stamp = str(config.get("today_full_raw") or f"{date.today():%Y%m%d}000000")
        text = re.sub(
            r",p_last_upd_yyyymmddhh24miss=>'\d+'",
            f",p_last_upd_yyyymmddhh24miss=>'{stamp}'",
            text,
        )
    return text


def _apex_static_file_sql(path: str, content: bytes) -> str:
    file_name = Path(path).name
    # `application/octet-stream` for an unrecognised extension is the one
    # fallback that keeps an unknown static file downloadable; a browser
    # already refuses to run/apply it, so guessing wrong here costs nothing
    # (#670 -- CSS/JS/SVG/font/PDF static files used to get this fallback too,
    # which a browser refuses to apply/execute at all).
    mime_type = guess_mime_type(file_name, default="application/octet-stream")
    hex_payload = content.hex().upper()
    hex_rows = [hex_payload[index:index + 200] for index in range(0, len(hex_payload), 200)]
    footer = queries.APEX_STATIC_FILE_FOOTER.format(file_name=file_name, mime_type=mime_type)
    payload = [
        queries.APEX_STATIC_FILE_HEADER,
        *[
            queries.APEX_STATIC_FILE_ROW.format(index=index, row=row)
            for index, row in enumerate(hex_rows, start=1)
        ],
        *footer.splitlines(),
        "",
    ]
    return "\n".join(payload)


__all__ = [name for name in globals() if not name.startswith("__")]
