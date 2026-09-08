"""The `@` line an install script links one file by, and the label above it.

Shared by the database payload in `create.py` and the APEX payloads in
`create_apex.py`; moved here by ADT #735 so the two can import it without
importing each other.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from adt_ai.patch.content import CONTENT_MODE_NOSNAP
from adt_ai.patch.files import _install_file_link, _snapshot_link
from adt_ai.patch.layout import (
    is_apex_static_file as _is_apex_static_file,
)


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
