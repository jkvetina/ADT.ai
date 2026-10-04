"""One `git cat-file --batch` process for every blob a `patch -create` reads (ADT #988).

`git_files.git_show` spawned one `git show <ref>:<path>` per file, and a patch
carrying two APEXlang trees reads every changed `.apx` file that way into its
snapshot: measured on a real repo, 3.9s of `_write_snapshots` with nothing on
screen, and on a two-tree fixture of 800 pages, 893 processes and 9.4s. A git
process costs about 10ms to start and next to nothing to answer, so the answer
is one long-lived `git cat-file --batch` that every read in the run talks to.

**Byte for byte what `git show` returns, or it is not answered here.** For a
blob `git show <ref>:<path>` prints the raw object -- no textconv, no eol or
smudge filter (measured with both configured) -- and `cat-file --batch` hands
back the same bytes. `missing` is `git show` failing, which `git_show` already
answers `None`. Anything else -- a tree, an ambiguous name, a spec holding a
newline the line protocol cannot carry, a process that died -- is answered
`(False, None)` and the caller runs its own `git show` exactly as before.

`batched_git_reads(root)` scopes the process to one build; outside it nothing
changes and no process is started.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import IO, cast

from adt_ai.shared.subprocess_env import safe_subprocess_environment

_lock = threading.Lock()
_readers: dict[Path, _BatchReader] = {}


class _BatchReader:
    """A `git cat-file --batch` process, asked one spec at a time."""

    def __init__(self, root: Path) -> None:
        self._process = subprocess.Popen(
            ["git", "cat-file", "--batch"],
            cwd    = root,
            stdin  = subprocess.PIPE,
            stdout = subprocess.PIPE,
            stderr = subprocess.DEVNULL,
            env    = safe_subprocess_environment(),
        )
        # Both pipes exist: `PIPE` was asked for each.
        self._stdin = cast(IO[bytes], self._process.stdin)
        self._stdout = cast(IO[bytes], self._process.stdout)

    def blob(self, spec: str) -> tuple[bool, bytes | None]:
        name = spec.encode("utf-8", errors="surrogateescape")
        self._stdin.write(name + b"\n")
        self._stdin.flush()
        header = self._stdout.readline()
        if not header.endswith(b"\n"):
            raise OSError("git cat-file --batch closed its output")
        header = header[:-1]
        if header == name + b" missing":
            return True, None
        parts = header.split(b" ")
        if len(parts) != 3 or not parts[2].isdigit():
            # `ambiguous`, or a shape this reader does not know: `git show` decides.
            return False, None
        size = int(parts[2])
        body = self._stdout.read(size)
        if len(body) != size or self._stdout.read(1) != b"\n":
            raise OSError("git cat-file --batch cut an object short")
        return (True, body) if parts[1] == b"blob" else (False, None)

    def close(self, *, kill: bool = False) -> None:
        if kill:
            self._process.kill()
        with suppress(OSError):
            self._stdin.close()
        self._process.wait()
        self._stdout.close()


@contextmanager
def batched_git_reads(root: Path) -> Iterator[None]:
    """Serve every `git_show` under ``root`` from one `cat-file` process."""
    key = Path(root).resolve()
    with _lock:
        if key in _readers:
            owned = None
        else:
            try:
                owned = _readers[key] = _BatchReader(key)
            except OSError:
                owned = None
    try:
        yield
    finally:
        if owned is not None:
            with _lock:
                retired = _readers.get(key) is not owned
                if not retired:
                    del _readers[key]
            if not retired:
                owned.close()


def batched_blob(root: Path, ref: str, path: str) -> tuple[bool, bytes | None]:
    """``(True, bytes or None)`` when the batch answered, ``(False, None)`` when not."""
    if not _readers:
        return False, None
    spec = f"{ref}:{path}"
    if "\n" in spec:
        return False, None
    key = Path(root).resolve()
    with _lock:
        reader = _readers.get(key)
        if reader is None:
            return False, None
        try:
            return reader.blob(spec)
        except OSError:
            # A reader that broke mid-run is retired; every read after it runs
            # its own `git show`, which is what the run did before ADT #988.
            del _readers[key]
            reader.close(kill=True)
            return False, None


__all__ = ["batched_blob", "batched_git_reads"]
