from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared import text_files


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
    """One content, one hash, on every platform (ADT #454).

    A SHA-1 over a CANONICAL form of the payload rather than over the bytes as
    they sit: line endings collapsed to LF, then the whole payload trimmed once.
    Both callers depend on that being the same rule. `changed_files` hashes a git
    blob and `patch.hashes.hash_working_tree` hashes a file on disk, and a
    baseline compares one against the other, so the moment the two sides can
    disagree about a line ending the baseline reports every file as MODIFIED.

    That is not hypothetical. `file_crlf` exists because Oracle hands back
    whatever was compiled (`shared/text_files.py`), so the same project exports
    CRLF on one machine and LF on another, and ADT.ai now runs on Windows.
    Jan, 2026-08-21: *"strip all leading and trailing spaces (from the file
    payload, not from each line) and normalize line endings to LF so the file
    hash will match on win/mac."*

    The trim is ONE strip of the whole payload, never a strip per line: a body
    that differs only in indentation is a different body, and per-line trimming
    would hash the two the same.

    This is a deliberate divergence from old ADT, whose `util.get_hash()` is a
    plain SHA-1 of the bytes and carries the same win/mac defect. The empty
    sentinel is unchanged, and now also answers for a whitespace-only file,
    because trimming empties it.
    """
    value = hashlib.sha1(_canonical_payload(payload, encoding)).hexdigest()
    return "" if value == "da39a3ee5e6b4b0d3255bfef95601890afd80709" else value


def _canonical_payload(payload: bytes | str, encoding: str) -> bytes:
    if isinstance(payload, bytes):
        try:
            text = payload.decode(encoding)
        except UnicodeDecodeError:
            # Not text at all. `_blob_contents` reads binary blobs deliberately
            # and says so, so a payload that cannot be decoded hashes the bytes
            # it has rather than raising at a hash site. Hashing raw keeps two
            # different binaries apart, which returning a blank would not.
            return payload
    else:
        text = payload
    return text_files.normalize(text).strip().encode(encoding)


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


# `git_user_email` stood here until ADT #469, and `shared/git_identity.py` held a
# `current_git_identity` beside it. Both were one line over `git_config_value`,
# and between them they gave the tool three names for one lookup: five call sites
# in four modules asked git who the user was, while `config/IDENTITY.yaml` sat
# there declaring `email` and `apex_account` with no reader anywhere in the tree.
# The question has one answer now, `shared/identity.resolve_commit_identity`, and
# this module supplies the git FALLBACK it reaches for when the file states
# nothing.


def fetch_origin(root: Path) -> None:
    # Best-effort refresh of remote-tracking refs; `--prune` drops branches
    # deleted on the server. Offline/no-remote failures are non-fatal, fall
    # back to whatever refs are already present locally.
    subprocess.run(
        ["git", "fetch", "--quiet", "--prune", "origin"],
        cwd=root,
        capture_output=True,
        text=True,
    )


def git_ref_exists(root: Path, ref: str) -> bool:
    """True when ``ref`` resolves to a commit in the repository at ``root``."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def git_is_ancestor(root: Path, commit: str, branch: str) -> bool:
    """True when ``commit`` is an ancestor of ``branch``."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, branch],
        cwd=root,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def git_show(root: Path, ref: str, path: str) -> bytes | None:
    """Raw bytes of ``path`` at ``ref``, or ``None`` when it does not resolve."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd            = root,
        check          = False,
        capture_output = True,
    )
    return result.stdout if result.returncode == 0 else None


def git_blob_exists(root: Path, ref: str, path: str) -> bool:
    """True when ``path`` resolves to a blob at ``ref``."""
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        cwd=root,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def git_status_porcelain(root: Path, path: str) -> str:
    """Raw ``git status --porcelain`` output scoped to ``path``."""
    return subprocess.run(
        ["git", "status", "--porcelain", "--", path],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()


def git_checkout(root: Path, name: str) -> None:
    """Check out ``name``, raising with git's own stderr when it refuses."""
    result = subprocess.run(
        ["git", "checkout", name],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(message or f"git checkout {name} failed")


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
