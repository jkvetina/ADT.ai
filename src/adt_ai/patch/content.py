"""Which version of a file a patch ships (ADT #280).

Old ADT had three answers. The default read the git blob at the file's
*authoritative commit*, the newest commit in the patch window that touched it
(`get_file_commit`, patch.py:2095), `-local` read the working tree instead
(patch.py:1958), and `-head` lifted the window ceiling so the newest version won
(patch.py:2100). ADT.ai shipped only the middle one, unnamed: `_write_snapshots`
called `source.read_text()`, so every patch carried whatever happened to be in
the working tree, including edits no commit records.

Jan settled the default on 2026-08-11, *"the committed files (not local, not
head - but it might be same as head)"*, and asked for a fourth mode in the same
answer: no snapshot at all, just a reference to the file where it lives. That one
is `-nosnap`, because `-ref` already means *existing patch reference* on `patch`.

`committed` and `head` coincide whenever the window's newest commit is HEAD and
nothing newer touched the file, which is the ordinary case; they diverge exactly
when `#277`'s newer-commit warning has something to say.

`head` grew a second ref on 2026-08-30 (ADT #599). It reads the local `HEAD` and
the remote default branch and takes whichever touched the file last, per file,
because the run that asks for the newest version wants a colleague's pushed fix
to the same object even though nothing has pulled it yet. `#598` had already
given the mode the fetch; this is what the fetch is for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adt_ai.patch.layout import is_apex_static_file, is_apexlang_path
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_files import (
    git_blob_exists,
    git_ref_exists,
    git_show,
    last_commit_time,
)

CONTENT_MODE_COMMITTED = "committed"
CONTENT_MODE_LOCAL = "local"
CONTENT_MODE_HEAD = "head"
CONTENT_MODE_NOSNAP = "nosnap"

# `committed` first: it is the default, and the order is what the CLI error and
# the help text list.
CONTENT_MODES = (
    CONTENT_MODE_COMMITTED,
    CONTENT_MODE_LOCAL,
    CONTENT_MODE_HEAD,
    CONTENT_MODE_NOSNAP,
)

# The flags a user actually types; `committed` has none because it is the default.
CONTENT_MODE_FLAGS = ("-local", "-head", "-nosnap")

# What `-head` compares the local `HEAD` against, first one that resolves. Remote
# only, and deliberately not `shared/git_files.default_branch_ref`, whose probe
# falls through to a LOCAL `main`/`master`: on a repo whose HEAD already is
# `main`, that fallback would compare the branch against itself and answer
# nothing. A repo with no origin resolves neither and reads `HEAD` alone, which
# is what `-head` has always done.
REMOTE_HEAD_REFS = ("origin/main", "origin/master")


def authoritative_commit(
    path: str, records: list[CommitRecord], *, include_deleted: bool = False,
) -> CommitRecord | None:
    """The newest selected commit that touched ``path``, or ``None``.

    ``records`` arrive oldest-first (`git log --reverse`), so the first hit
    scanning backwards is the newest, the same walk old ADT did from the top of
    a reverse-sorted commit map.

    ``None`` is a real answer, not an error: a grant file pulled in from disk, or
    a helper this run generated, has no commit behind it at all. That is what the
    `UNCOMMITTED FILES` warning reports (ADT #276).

    Body resolution includes deletion records so an earlier selected edit
    cannot resurrect a path removed later in the same selection. Reporting
    callers keep the historical non-deleted commit lookup by default.
    """
    for record in reversed(records):
        if path in record.usable_files or (include_deleted and path in record.deleted_files):
            return record
    return None


def newer_commits(
    path: str,
    window: list[CommitRecord],
    current_number: int | None,
) -> list[tuple[int, str]]:
    """Commits newer than the one being shipped that also touched ``path``.

    Scanned over the whole commit WINDOW rather than the selected records, old
    ADT read `self.all_files[orig_file]`, everything it had cached (patch.py:1636).
    A commit dropped by the patch-code or author filter is precisely the one worth
    naming: nothing else in the run mentions it, and it is the reason the shipped
    version is stale.

    Newest first, matching old ADT's `reversed(found_newer)` at the print site.
    """
    if current_number is None:
        return []
    return [
        (record.number, record.summary)
        for record in sorted(window, key=lambda item: item.number, reverse=True)
        if record.number > current_number and path in record.usable_files
    ]


def file_text(
    root: Path,
    path: str,
    *,
    mode: str,
    records: list[CommitRecord],
) -> str | None:
    """The text this patch should snapshot for ``path``, or ``None`` if it is gone.

    Every mode falls back to the working tree when git has nothing to offer,
    because the alternative is dropping a file the patch needs. A file that
    reaches that fallback carries no commit, so the run reports it under
    `UNCOMMITTED FILES` rather than shipping it silently.
    A selected deletion, or a known deletion at HEAD, is a final source answer
    and never takes that fallback.
    """
    payload = file_bytes(root, path, mode=mode, records=records)
    return payload.decode("utf-8", errors="replace") if payload is not None else None


def file_bytes(
    root: Path,
    path: str,
    *,
    mode: str,
    records: list[CommitRecord],
) -> bytes | None:
    """``file_text`` for a payload that is not text, an APEX static file.

    Kept on the same resolution rules rather than always reading disk: a binary
    asset committed weeks ago and edited locally would otherwise be the one file
    in a `committed` patch that still shipped the working-tree version.
    """
    blob = _blob_ref(root, path, mode=mode, records=records)
    if blob is not None:
        content = git_show(root, blob, path)
        if content is not None:
            return content
        # A recorded deletion is a source answer, not a missing-source fallback.
        # HEAD can remove a selected file too; a local uncommitted restoration
        # must not silently replace that authoritative answer.
        record = authoritative_commit(path, records, include_deleted=True)
        if mode != CONTENT_MODE_HEAD and record and path in record.deleted_files:
            return None
        if mode == CONTENT_MODE_HEAD and last_commit_time(root, blob, path):
            return None
    source = root / path
    return source.read_bytes() if source.is_file() else None


def file_present(
    root: Path,
    path: str,
    config: dict[str, Any],
    *,
    mode: str,
    records: list[CommitRecord],
) -> bool:
    """Whether the selected source carries a file the patch can actually ship.

    Links, snapshots, reports and drift guards all ask this same question.
    APEXlang and ordinary no-snapshot links retain their live-tree contract;
    the static wrapper still uses the ordinary content resolver in every mode.
    """
    if is_apexlang_path(path, config) or (
        mode == CONTENT_MODE_NOSNAP and not is_apex_static_file(path, config)
    ):
        return (root / path).is_file()
    # The ordinary committed/local case already has a usable disk fallback.
    # Avoid opening the same git blob in each report, link and guard consumer;
    # only a locally absent file or HEAD's possibly newer deletion needs git.
    if mode != CONTENT_MODE_HEAD:
        record = authoritative_commit(path, records, include_deleted=True)
        if mode != CONTENT_MODE_LOCAL and record and path in record.deleted_files:
            return False
        if (root / path).is_file():
            return True
    blob = _blob_ref(root, path, mode=mode, records=records)
    if blob is not None:
        if git_blob_exists(root, blob, path):
            return True
        if mode == CONTENT_MODE_HEAD and last_commit_time(root, blob, path):
            return False
    return (root / path).is_file()


def _blob_ref(
    root: Path,
    path: str,
    *,
    mode: str,
    records: list[CommitRecord],
) -> str | None:
    """The git ref whose version of ``path`` this mode wants, or ``None`` for disk."""
    if mode == CONTENT_MODE_LOCAL:
        return None
    if mode == CONTENT_MODE_HEAD:
        return _newest_head_ref(root, path)
    record = authoritative_commit(path, records, include_deleted=True)
    return record.commit_hash if record else None


def _newest_head_ref(root: Path, path: str) -> str:
    """`HEAD`, or the remote default branch when IT holds the newer ``path``.

    Per FILE, never per run: the answer is whichever of the two refs a commit
    touched last, so a run can ship one file from the branch and the next from
    the remote. Jan, 2026-08-30 (ADT #599): *"Read the head from current branch
    AND from the remote main/master. Use the most recent version."*

    That is what makes `#598`'s fetch change anything. `git fetch --prune origin`
    moves `origin/<branch>` and never the local `HEAD`, so a colleague's PUSHED
    fix to an object this patch already ships reached nothing until the branch
    pulled it. Reading the remote ref is the half that puts his version in the
    snapshot, and the file LIST is untouched: it is still the commit selection's.

    Every fallback is silent and every one of them is today's behaviour. No
    origin, no `origin/main` and no `origin/master`, or a remote that does not
    carry this path, all answer `HEAD`; a path with no commit on `HEAD` at all
    scores `0` and loses to any commit the remote has.
    """
    remote = next((ref for ref in REMOTE_HEAD_REFS if git_ref_exists(root, ref)), None)
    if remote is None or not git_blob_exists(root, remote, path):
        return "HEAD"
    # Ties go to `HEAD`: the same commit reached from both refs is the ordinary
    # case (a branch level with its remote), and answering `HEAD` there keeps the
    # ref the rest of the run reports.
    if last_commit_time(root, remote, path) > last_commit_time(root, "HEAD", path):
        return remote
    return "HEAD"
