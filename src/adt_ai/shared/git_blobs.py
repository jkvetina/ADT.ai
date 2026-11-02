"""The blobs one commit changed, read in one `git cat-file --batch` (ADT #1094).

Moved here from `shared.git_files` with the one case the batch cannot carry. The
batch reads one request per LINE, so a file name holding a newline was two
requests: the first answered `missing`, the second answered some other path, and
every record after it shifted by one. `changed_files` then hashed the wrong
content for every later file in the commit, with no error anywhere, and a
baseline built on that reported the wrong files as modified. `shared/git_batch`
guards the same seam for its own batch; this module asks for such a path by argv
instead, where a newline is just a byte, and the batch never sees it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from adt_ai.shared.subprocess_env import safe_subprocess_environment


def blob_contents(root: Path, commit_hash: str, paths: list[str]) -> dict[str, bytes]:
    """`{path: bytes}` for every path that resolves to a blob at `commit_hash`.

    A gitlink (a submodule pointer) or any other non-blob entry is absent from the
    answer rather than wrong in it.
    """
    if not paths:
        return {}
    batched = [path for path in paths if "\n" not in path]
    contents = _batched_blobs(root, commit_hash, batched) if batched else {}
    for path in paths:
        if "\n" in path:
            payload = _single_blob(root, commit_hash, path)
            if payload is not None:
                contents[path] = payload
    return contents


def _single_blob(root: Path, commit_hash: str, path: str) -> bytes | None:
    # One process for the one shape the batch protocol cannot express.
    completed = subprocess.run(
        ["git", "cat-file", "blob", f"{commit_hash}:{path}"],
        cwd=root,
        capture_output=True,
        check=False,
        env=safe_subprocess_environment(),
    )
    return completed.stdout if completed.returncode == 0 else None


def _batched_blobs(root: Path, commit_hash: str, paths: list[str]) -> dict[str, bytes]:
    # Resolve `<commit>:<path>` specs to raw blob bytes in one batched git call.
    # `git cat-file --batch` echoes one record per input line, in order: a present
    # object is `<oid> <type> <size>\n<size bytes>\n`; an unresolvable spec (e.g. a
    # gitlink whose commit lives in the submodule, not this repo) is `<spec> missing\n`.
    # Bodies are read by byte size, so binary / NUL-containing blobs are safe.
    specs = "".join(f"{commit_hash}:{path}\n" for path in paths)
    completed = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=specs.encode("utf-8", errors="surrogateescape"),
        capture_output=True,
        check=True,
        env=safe_subprocess_environment(),
    )
    output = completed.stdout

    contents: dict[str, bytes] = {}
    pos = 0
    for record_index, path in enumerate(paths):
        newline = output.find(b"\n", pos)
        if newline == -1:
            raise RuntimeError(
                f"git cat-file returned {record_index} complete record(s) "
                f"for {len(paths)} path(s)"
            )
        header = output[pos:newline].decode(errors="replace")
        pos = newline + 1
        if header.endswith(" missing"):
            continue
        try:
            _oid, obj_type, size_text = header.split(" ", 2)
            size = int(size_text)
        except (TypeError, ValueError) as error:
            raise RuntimeError(f"Malformed git cat-file response: {header!r}") from error
        if pos + size >= len(output):
            raise RuntimeError(f"Truncated git cat-file body for {path!r}")
        if output[pos + size : pos + size + 1] != b"\n":
            raise RuntimeError(f"Malformed git cat-file body terminator for {path!r}")
        if obj_type == "blob":
            contents[path] = output[pos : pos + size]
        pos += size + 1  # skip the body and its trailing newline
    return contents


__all__ = ["blob_contents"]
