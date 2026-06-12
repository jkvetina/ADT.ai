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
    diff_lines = _git(
        root,
        ["diff-tree", "--root", "--no-commit-id", "--name-status", "-r", commit_hash],
    ).splitlines()
    result: list[ChangedFile] = []
    for line in diff_lines:
        if "\t" not in line:
            continue
        status, path = line.split("\t", 1)
        file_status = status[0]
        if file_status == "D":
            result.append(ChangedFile(path=path, status=file_status))
            continue
        try:
            content = _git_bytes(root, ["show", f"{commit_hash}:{path}"])
        except subprocess.CalledProcessError:
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


def file_payload_hash(payload: bytes | str, encoding: str = "utf-8") -> str:
    if isinstance(payload, str):
        payload = payload.encode(encoding)
    value = hashlib.sha1(payload).hexdigest()
    return "" if value == "da39a3ee5e6b4b0d3255bfef95601890afd80709" else value


def _git(root: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _git_bytes(root: Path, args: list[str]) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
