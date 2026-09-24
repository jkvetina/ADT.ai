"""`auto_sync_git`: every export's pre-write sync of the root git meta files (ADT #938).

`doctor -init -sync` is the on-demand, reports-everything route a developer
runs by hand. This is the silent, automatic one: before `export_db`,
`export_apex` or `export_data` writes a single file, it rewrites the ADT-owned
block in an EXISTING `.gitattributes` and `.gitignore` (never scaffolds a
missing one, that is `-init`'s job) and converts already-tracked sources that
git's effective attributes pin to `eol=lf` but still hold CRLF, on disk only.
Nothing is staged, ever, and a downstream line overriding one of the block's
attributes is never reported here -- `-init -sync` is the one route that
reports overrides. `patch` never calls this at all.

The `.gitattributes` block is rendered from the project's `file_crlf`, the
same way `doctor -init` renders it, so an export never rewrites the pins a
CRLF project relies on. Refused (broken) markers leave that file alone, and a
refused `.gitattributes` also skips the CRLF conversion: a silent hook must
never act on a file whose shape it cannot trust.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.doctor.init import (
    GITATTRIBUTES_NAME,
    GITATTRIBUTES_TEMPLATE,
    GITIGNORE_NAME,
    GITIGNORE_TEMPLATE,
)
from adt_ai.doctor.runner import default_resource_root
from adt_ai.shared import git_meta_sync, text_files
from adt_ai.shared.config import is_enabled


def sync_git_metadata_before_export(
    root: Path,
    config: Mapping[str, Any] | None,
    *,
    gitattributes_block: str | None = None,
    gitignore_block: str | None = None,
) -> None:
    """Run the `auto_sync_git` pre-write hook for one export command.

    A no-op unless `auto_sync_git` (default `True`) is enabled and `root` is a
    git work tree. At most one short line is printed, and only when something
    actually changed.

    `gitattributes_block` / `gitignore_block` override the shipped templates,
    for a test that wants a short, deterministic block instead of ADT.ai's own.
    """
    settings = config or {}
    if not is_enabled(settings.get("auto_sync_git"), default=True):
        return
    if not git_meta_sync.is_git_work_tree(root):
        return

    resources = default_resource_root()
    if gitattributes_block is None:
        gitattributes_block = git_meta_sync.render_gitattributes_block(
            resources.joinpath(*GITATTRIBUTES_TEMPLATE.parts).read_text(encoding="utf-8"),
            file_crlf=is_enabled(settings.get("file_crlf")),
        )
    if gitignore_block is None:
        gitignore_block = resources.joinpath(*GITIGNORE_TEMPLATE.parts).read_text(
            encoding="utf-8"
        )

    synced: list[str] = []
    attributes_trusted = False
    for name, block in (
        (GITATTRIBUTES_NAME, gitattributes_block),
        (GITIGNORE_NAME, gitignore_block),
    ):
        state = _sync_existing(root / name, block)
        if state is None:
            continue
        if name == GITATTRIBUTES_NAME:
            attributes_trusted = True
        if state:
            synced.append(name.as_posix())

    converted: list[str] = []
    if attributes_trusted:
        converted = git_meta_sync.convert_tracked_crlf(
            root, git_meta_sync.block_patterns(gitattributes_block)
        )

    if synced or converted:
        # The row is the banner's body, so it opens on the blank every section
        # body opens on (ADT #942); `print_adt_header` then sizes the gap to the
        # next header, which owes the pair under a banner that carried rows
        # (#904). An export that syncs nothing prints neither line.
        print()
        print(_summary_line(synced, converted))


def _sync_existing(path: Path, block: str) -> bool | None:
    """Rewrite `path`'s block in place: `True` changed, `False` already current,
    `None` when the file is missing or its markers are broken (left alone)."""
    if not path.exists():
        return None
    # The export already applied `file_crlf` process-wide; the block follows
    # it, as `doctor -init -sync` does, so the two never rewrite each other's
    # line endings on every run (ADT #944).
    merge = git_meta_sync.merge_block(
        path.read_bytes().decode("utf-8"), block, text_files.configured_newline()
    )
    if merge.refused:
        return None
    if merge.changed:
        text_files.write_bytes(path, merge.new_text.encode("utf-8"))
    return merge.changed


def _summary_line(synced: list[str], converted: list[str]) -> str:
    parts = [f"{name} synced" for name in synced]
    if converted:
        plural = "" if len(converted) == 1 else "s"
        parts.append(f"{len(converted)} file{plural} converted from CRLF to LF")
    return f"GIT: {', '.join(parts)}"


__all__ = [
    "sync_git_metadata_before_export",
]
