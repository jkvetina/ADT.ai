"""The files one run wrote, recognised under any spelling the disk folds together.

A sweep that deletes what a run did not write compares directory entries with the
paths it wrote. macOS's default disk ignores case and Unicode normal form, so an
entry whose name differs from a written path only there IS the file the run wrote,
under the spelling it already had. Comparing names exactly took it for a stale one
and deleted it at exit 0: first in the APEXlang sweep (ADT #923), then found again
in `export_data`'s sidecar folder (ADT #1094). A command package never imports
another, so the answer lives here and both sweeps read it.
"""

from __future__ import annotations

import contextlib
import os
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path

#: Do two names reach one file on this disk?
SameFile = Callable[[Path, Path], bool]


def same_file(left: Path, right: Path) -> bool:
    """Do both names reach one file? A name that reaches nothing reaches no file."""
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _folded(path: Path) -> str:
    """`path` the way a disk that ignores case and Unicode normal form reads it."""
    return unicodedata.normalize("NFC", str(path)).casefold()


class WrittenFiles:
    """The files one run wrote, recognised under any spelling the disk folds together.

    macOS's default disk ignores case, so a component whose static ID or name
    changes only in case (`minicalendar` to `MiniCalendar`) is written onto the
    old entry, which keeps its old spelling. A sweep comparing names exactly took
    that entry for a stale one and deleted the file the run had just written, at
    exit 0 (ADT #923). An entry is this run's own when its name folds to a written
    one AND the disk says the two names reach one file, so a disk that keeps both
    spellings apart still sweeps the old one. The survivor takes the spelling the
    run wrote, so the repository records the rename.
    """

    def __init__(self, paths: Iterable[Path], same: SameFile | None = None) -> None:
        self.paths = set(paths)
        self._folded = {_folded(path): path for path in self.paths}
        self._same = same or same_file

    def claims(self, path: Path) -> bool:
        """Did this run write `path`? One under an old spelling takes the new one."""
        if path in self.paths:
            return True
        written = self._folded.get(_folded(path))
        if written is None or not self._same(path, written):
            return False
        _respell(path, written)
        return True


def _respell(path: Path, written: Path) -> None:
    """Rename each entry on `path` whose name differs from `written`'s, top down.

    A folder is renamed before the entries under it. A rename the disk refuses
    leaves the old spelling, which is where the entry stood before, rather than
    failing an export that wrote everything it was asked to.
    """
    for depth, (have, want) in enumerate(zip(path.parts, written.parts, strict=False)):
        if have != want:
            parent = Path(*written.parts[:depth])
            with contextlib.suppress(OSError):
                (parent / have).rename(parent / want)


__all__ = [
    "SameFile",
    "WrittenFiles",
    "same_file",
]
