from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared import text_files
from adt_ai.shared.subprocess_env import safe_subprocess_environment


@dataclass(frozen=True)
class ChangedFile:
    path: str
    status: str
    content_hash: str | None = None


def run_git_paths(root: Path, args: list[str]) -> list[str]:
    r"""Path output from a git command, split on NUL rather than on newlines.

    **Every git command that prints paths goes through this.** Git C-quotes any
    path outside plain ASCII in its default output, so `příklad.sql` comes back
    as `"database/tables/p\305\231\303\255klad.sql"`, quotes and octal escapes
    and all. That quoted spec resolves to nothing, so every Czech-named object
    silently vanished from `rebuild`, `search_repo`, `patch -create` and the hash
    index with no message (ADT #664). `-z` turns the quoting off and terminates
    each path with NUL, which is also the only separator a filename cannot
    contain, so a path holding a newline survives too.

    The caller passes the command WITHOUT `-z`; it is appended here so no call
    site can forget it.
    """
    output = run_git_bytes(root, [*args, "-z"]).decode("utf-8", errors="surrogateescape")
    return [record for record in output.split("\0") if record]


def changed_files(root: Path, commit_hash: str) -> list[ChangedFile]:
    # `--name-status -z` emits the status and the path as SEPARATE NUL-terminated
    # records rather than one tab-joined line, so they are read in pairs, except
    # a rename or copy, which carries its source and its destination.
    records = run_git_paths(
        root,
        ["diff-tree", "--root", "--no-commit-id", "--name-status", "-r", commit_hash],
    )
    entries: list[tuple[str, str]] = []
    index = 0
    while index + 1 < len(records):
        status = records[index]
        width = 3 if status[:1] in {"R", "C"} else 2
        # The destination is the path the commit now carries; for R/C it is the
        # third record, and a truncated stream falls back to the second.
        path = records[min(index + width - 1, len(records) - 1)]
        entries.append((status[0], path))
        index += width

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


BOM = "﻿"


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
    # A BOM is an encoding marker, not content, and `str.strip()` does not remove
    # it, so a BOM-prefixed file never hash-matched its stripped twin and every
    # baseline read it as MODIFIED for good (ADT #664). It goes before the trim,
    # for the same reason the line endings do: two spellings of one payload have
    # to reach one hash.
    return text_files.normalize(text).lstrip(BOM).strip().encode(encoding)


def run_git(root: Path, args: list[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        env=safe_subprocess_environment(),
    ).stdout


def run_git_bytes(root: Path, args: list[str]) -> bytes:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        env=safe_subprocess_environment(),
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
        env=safe_subprocess_environment(),
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
        env=safe_subprocess_environment(),
    )


def git_ref_exists(root: Path, ref: str) -> bool:
    """True when ``ref`` resolves to a commit in the repository at ``root``."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=root,
        capture_output=True,
        text=True,
        env=safe_subprocess_environment(),
    )
    return result.returncode == 0


def last_commit_time(root: Path, ref: str, path: str) -> int:
    """Committer timestamp of the newest commit at ``ref`` that touched ``path``.

    ``0`` when ``ref`` has no commit for it at all, which is a real answer rather
    than an error: a path added on one branch and nowhere else is exactly the
    case `patch -head` compares two refs to settle, and zero loses that
    comparison to any commit there is.
    """
    output = subprocess.run(
        ["git", "log", "-1", "--format=%ct", ref, "--", path],
        cwd            = root,
        capture_output = True,
        text           = True,
        check          = False,
        env            = safe_subprocess_environment(),
    ).stdout.strip()
    return int(output) if output else 0


def git_is_ancestor(root: Path, commit: str, branch: str) -> bool:
    """True when ``commit`` is an ancestor of ``branch``."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, branch],
        cwd=root,
        capture_output=True,
        text=True,
        env=safe_subprocess_environment(),
    )
    return result.returncode == 0


def git_show(root: Path, ref: str, path: str) -> bytes | None:
    """Raw bytes of ``path`` at ``ref``, or ``None`` when it does not resolve."""
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        cwd            = root,
        check          = False,
        capture_output = True,
        env            = safe_subprocess_environment(),
    )
    return result.stdout if result.returncode == 0 else None


def git_blob_exists(root: Path, ref: str, path: str) -> bool:
    """True when ``path`` resolves to a blob at ``ref``."""
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        cwd=root,
        capture_output=True,
        check=False,
        env=safe_subprocess_environment(),
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
        env=safe_subprocess_environment(),
    ).stdout.strip()


def git_status_paths(root: Path, paths: list[str]) -> dict[str, str]:
    """One ``git status --porcelain -z`` call answering every path in ``paths``.

    `patch/report.py::_uncommitted` and `patch/files.py::file_source_modes`
    used to call :func:`git_status_porcelain` once per file, spawning one `git
    status` subprocess per patch file (ADT #670); this batches every path the
    caller has into a single call, through the same `-z` NUL-terminated reader
    the rest of this module already uses so a non-ASCII path is not silently
    dropped by C-quoting the way `#664` found plain `ls-files` doing it.

    A path missing from the returned mapping is clean (git printed nothing for
    it). Both call sites only ever ask about ordinary working-tree edits on a
    known path, never a rename, so a rename/copy's second (pre-rename) NUL
    field is read as-is rather than paired off the way `changed_files` pairs
    `diff-tree -z`; it lands as one extra, unmatched key that no real caller
    looks up, never as a wrong answer for a path that IS in ``paths``.

    Empty ``paths`` answers ``{}`` without spawning a process at all: nothing
    to ask git about is not the same question as everything being clean.
    """
    if not paths:
        return {}
    output = run_git_bytes(
        root, ["status", "--porcelain", "-z", "--", *paths]
    ).decode("utf-8", errors="surrogateescape")
    statuses: dict[str, str] = {}
    for record in output.split("\0"):
        if record:
            statuses[record[3:]] = record
    return statuses


def git_checkout(root: Path, name: str) -> None:
    """Check out ``name``, raising with git's own stderr when it refuses."""
    result = subprocess.run(
        ["git", "checkout", name],
        cwd=root,
        capture_output=True,
        text=True,
        env=safe_subprocess_environment(),
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
        env=safe_subprocess_environment(),
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
            env=safe_subprocess_environment(),
        )
        if probe.returncode == 0:
            short = candidate[len("origin/"):] if candidate.startswith("origin/") else candidate
            return candidate, short
    return "", ""
