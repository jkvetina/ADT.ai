from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    content_hash: str | None = None


def changed_files(root: Path, commit_hash: str) -> list[ChangedFile]:
    diff_lines = run_git(
        root,
        ["diff-tree", "--root", "--no-commit-id", "--name-status", "-r", commit_hash],
    ).splitlines()
    entries: list[tuple[str, str]] = []
    for line in diff_lines:
        if "\t" not in line:
            continue
        status, path = line.split("\t", 1)
        entries.append((status[0], path))

    # Fetch every changed blob for this commit in a single `git cat-file --batch`
    # process instead of one `git show` per file: a commit touching N files used
    # to spawn N+1 git processes, which dominated `rebuild` on large histories.
    blob_paths = [path for file_status, path in entries if file_status != "D"]
    contents = _blob_contents(root, commit_hash, blob_paths)

    result: list[ChangedFile] = []
    for file_status, path in entries:
        if file_status == "D":
            result.append(ChangedFile(path=path, status=file_status))
            continue
        content = contents.get(path)
        if content is None:
            # gitlink (submodule pointer) or other non-blob entry - skip
            continue
        result.append(
            ChangedFile(
                path         = path,
                status       = file_status,
                content_hash = file_payload_hash(content),
            )
        )
    return result


def _blob_contents(root: Path, commit_hash: str, paths: list[str]) -> dict[str, bytes]:
    # Resolve `<commit>:<path>` specs to raw blob bytes in one batched git call.
    # `git cat-file --batch` echoes one record per input line, in order: a present
    # object is `<oid> <type> <size>\n<size bytes>\n`; an unresolvable spec (e.g. a
    # gitlink whose commit lives in the submodule, not this repo) is `<spec> missing\n`.
    # Bodies are read by byte size, so binary / NUL-containing blobs are safe.
    if not paths:
        return {}
    specs = "".join(f"{commit_hash}:{path}\n" for path in paths)
    output = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        input=specs.encode(),
        capture_output=True,
    ).stdout

    contents: dict[str, bytes] = {}
    pos = 0
    for path in paths:
        newline = output.find(b"\n", pos)
        if newline == -1:
            break
        header = output[pos:newline].decode(errors="replace")
        pos = newline + 1
        if header.endswith(" missing"):
            continue
        _oid, obj_type, size_text = header.split(" ", 2)
        size = int(size_text)
        if obj_type == "blob":
            contents[path] = output[pos : pos + size]
        pos += size + 1  # skip the body and its trailing newline
    return contents


def file_payload_hash(payload: bytes | str, encoding: str = "utf-8") -> str:
    if isinstance(payload, str):
        payload = payload.encode(encoding)
    value = hashlib.sha1(payload).hexdigest()
    return "" if value == "da39a3ee5e6b4b0d3255bfef95601890afd80709" else value


def run_git(root: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def run_git_bytes(root: Path, args: list[str]) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout


def git_config_value(key: str, root: Path | None = None) -> str:
    # `git config <key>` exits non-zero when the key is unset; treat that (and
    # any other failure) as "", never as an error. `root=None` reads from the
    # process working directory, which is what the export flows expect.
    result = subprocess.run(
        ["git", "config", key],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def git_user_email(root: Path) -> str:
    return git_config_value("user.email", root)


def fetch_origin(root: Path) -> None:
    # Best-effort refresh of remote-tracking refs; `--prune` drops branches
    # deleted on the server. Offline/no-remote failures are non-fatal — fall
    # back to whatever refs are already present locally.
    subprocess.run(
        ["git", "fetch", "--quiet", "--prune", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
    )


def default_branch_ref(root: Path) -> tuple[str, str]:
    """`(ref, short)` of the default branch, or `("", "")` if unresolved.

    Prefers the `origin/HEAD` symbolic ref set by clone; falls back to probing
    the conventional remote then local `main`/`master` names so local-only
    repos (and tests) still resolve a base for `default..feature` ranges.
    """
    result = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    ref = result.stdout.strip()
    if ref.startswith("refs/remotes/origin/"):
        full = ref[len("refs/remotes/"):]
        return full, full[len("origin/"):]
    for candidate in ("origin/main", "origin/master", "main", "master"):
        probe = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", candidate],
            cwd=root,
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            short = candidate[len("origin/"):] if candidate.startswith("origin/") else candidate
            return candidate, short
    return "", ""
