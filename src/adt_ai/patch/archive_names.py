"""The archive a patch folder is written to, when that name is already taken (ADT #1094).

`files.next_patch_folder` reuses `<yymmdd>-<seq>-<CODE>` for a same-day
same-code `-create` once `-archive` has removed the folder: nothing left on disk
pushes the sequence on. So a patch rebuilt and archived twice in one day wrote
`260601-1-CORE.zip` twice, and `shutil.make_archive` replaced the first without
a word. The second archive is `260601-1-CORE-2.zip`, the third `-3`, the suffix
ahead of the extension the format appends. Split out of `files.py`, which sits
at the 24 KB context guard.
"""

from __future__ import annotations

from pathlib import Path

#: What `shutil.make_archive` appends per format, so the collision check looks
#: at the file it is about to write rather than at the extension-less base.
_ARCHIVE_SUFFIXES = {
    "zip": ".zip", "tar": ".tar", "gztar": ".tar.gz", "bztar": ".tar.bz2", "xztar": ".tar.xz",
}


def unused_archive_base(destination: Path, name: str, archive_format: str) -> Path:
    """`destination / name`, suffixed `-2`, `-3`, ... while that archive exists."""
    suffix = _ARCHIVE_SUFFIXES.get(archive_format, f".{archive_format}")
    base, counter = destination / name, 2
    while base.with_name(f"{base.name}{suffix}").exists():
        base, counter = destination / f"{name}-{counter}", counter + 1
    return base


__all__ = ["unused_archive_base"]
