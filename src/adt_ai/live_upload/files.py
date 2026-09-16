"""Which files the watch is looking at, and what each one is called in APEX."""

from __future__ import annotations

from pathlib import Path

from adt_ai.shared.mime import guess_mime_type

# The two kinds of static file APEX serves in a minified form beside the
# original, and the marker that says a file already IS one.
MINIFIABLE_SUFFIXES = (".css", ".js")
MINIFIED_MARKER = ".min."

# What an unrecognised suffix is uploaded as. A static file APEX cannot type is
# still a static file, so it goes up rather than being skipped.
DEFAULT_MIME_TYPE = "text/plain"


def scan(folder: Path) -> dict[Path, float]:
    """Every file under `folder`, mapped to its modification time."""
    return {
        path: path.stat().st_mtime
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def changed(folder: Path, known: dict[Path, float]) -> dict[Path, float]:
    """What has appeared or moved forward since `known` was taken.

    A stamp that moved BACKWARD is not a change: an editor restoring a backup
    keeps the older mtime, and uploading on that would push the stale copy over
    the newer one already in APEX.
    """
    return {
        path: stamp
        for path, stamp in scan(folder).items()
        if stamp > known.get(path, 0.0)
    }


def upload_name(folder: Path, path: Path) -> str:
    """The name APEX stores the file under: its path below the watched folder.

    POSIX-spelled whatever the machine, because the name is a value in an APEX
    table rather than a path on disk, and the same repository is watched from
    Windows and macOS.
    """
    return path.relative_to(folder).as_posix()


def mime_type(path: Path) -> str:
    return guess_mime_type(path.name, default=DEFAULT_MIME_TYPE)


def minified_target(path: Path) -> Path | None:
    """Where this file's minified sibling belongs, or None when it needs none.

    A name already carrying `.min.` returns None, which is what stops the next
    pass minifying the file this one just wrote.
    """
    if path.suffix not in MINIFIABLE_SUFFIXES or MINIFIED_MARKER in path.name:
        return None
    return path.with_suffix(f".min{path.suffix}")
