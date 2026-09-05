"""`patch` hash mode: read the baseline, diff the working tree, select the files.

Renamed from `patch_rollout.py` by ADT #447, which replaced the rollout
machinery with a single complete baseline per environment. The seam is
unchanged and is still the flag pair: everything reached only under `-hash` or
`-baseline` lives here and nothing else does, which is also the boundary `#362`
drew through the help screen when it gave those flags a `HASH MODE` section.

Two things are worth knowing before reading it. The selection is a FILE SET
rather than a commit range, so nothing here is bounded by `patch_scan_commits`
and a file changed long ago and never deployed is still patched. And `-baseline`
WRITES, so its refusals are raised before that write rather than after it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import PatchError, print_adt_header, print_adt_table
from adt_ai.cli.context import _project_relative
from adt_ai.cli.patch_preview_render import _hash_changed_rows
from adt_ai.patch.content import authoritative_commit
from adt_ai.patch.hashes import (
    Baseline,
    HashDiff,
    baseline_change_counts,
    diff_against_baseline,
    hash_working_tree,
    read_baseline,
    read_baseline_hashes,
    resolve_baseline_path,
    write_baseline,
)
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.file_list import print_file_rows

#: The baseline header's own clock. Local wall time to the minute, the same
#: shape the deploy logs stamp with, so a reader comparing a baseline against a
#: deploy log is reading two values in one timezone.
BASELINE_STAMP_FORMAT = "%Y-%m-%d %H:%M"


def hash_mode_error(args: argparse.Namespace) -> str | None:
    """Why this run's hash-mode flags cannot be honoured, or None (ADT #447).

    Three refusals, each of them a question with two answers rather than a
    preference. Every one is raised before the commit scan, so a refusal costs
    nothing and leaves nothing behind, and `-baseline` in particular never
    reaches its own write.

    It lives here rather than beside the other `commands_patch` guards for the
    reason the module docstring gives: everything reached only under `-hash` or
    `-baseline` is on this side of the seam.
    """
    if args.hash is not None and args.baseline is not None:
        return (
            "Pass one of -hash, -baseline, not both: -hash reads a baseline to "
            "build a patch from, -baseline replaces it"
        )
    # `-target` names the environment whose baseline this is, so it is required
    # exactly when the path has to be derived from it. A FILE spells the whole
    # address and leaves nothing for the target to resolve.
    for flag, value in (("-hash", args.hash), ("-baseline", args.baseline)):
        if value is not None and not value and not args.target:
            return (
                f"Missing required target: use -target TARGET with {flag}, or name "
                f"the baseline directly with {flag} FILE"
            )
    if args.hash is not None:
        refused = [flag for flag in ("-head", "-nosnap") if getattr(args, flag[1:], False)]
        if refused:
            return (
                f"Pass -hash without {', '.join(refused)}: hash mode compares the "
                "working tree against the baseline, so the working tree is what it ships"
            )
    return None


def run_baseline(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
    records: list[CommitRecord],
) -> int:
    """`-baseline`: record every current file hash as the deployed state.

    One meaning only. Jan, 2026-08-21: *"In -deploy mode you will do baseline on
    what shipped, but with just -baseline you will hash all."* The per-patch
    advance lives in the deploy path, where ADT knows what actually shipped;
    here the whole working tree is the answer.

    The commit column is filled from the commit records this run already
    scanned, so it costs no extra git. A file whose newest commit sits outside
    `patch_scan_commits` records a blank commit rather than a guessed one, which
    the ALTER-helper base then reports on rather than silently skipping.
    """
    # `-` when no environment was named, which only happens on `-baseline FILE`
    # where the path is the whole address. Writing a token rather than nothing
    # keeps the header one shape, so reading a stamp back never depends on
    # whether an environment was known when it was written.
    target_env = args.target or "-"
    path = resolve_baseline_path(root, config, target_env, args.baseline)
    current = hash_working_tree(root, config)
    commits = {
        file: record.number
        for file in current
        if (record := authoritative_commit(file, records)) is not None
    }
    # Read BEFORE the write, or the comparison is the file against itself.
    previous = read_baseline_hashes(path)
    write_baseline(
        path,
        current,
        commits,
        target_env = target_env,
        stamp      = datetime.now().strftime(BASELINE_STAMP_FORMAT),
    )
    print_adt_header("WRITING BASELINE:")
    # One row, and flat: a baseline file is named rather than listed (ADT #504).
    print_file_rows([_project_relative(path, root)], nested=False)
    print_baseline_stats(baseline_change_counts(previous, current), total=len(current))
    return 0


def print_baseline_stats(counts: dict[str, int] | None, *, total: int) -> None:
    """The table that replaced the number beside the header (`#453`).

    A first baseline has nothing to compare against, so it reports its total
    alone rather than four zeroes and a total. The count column is declared
    numeric so it right-aligns like every other ADT table.
    """
    rows = counts if counts is not None else {"TOTAL": total}
    print_adt_table(
        [{"STATUS": status, "FILES": value} for status, value in rows.items()],
        numeric = ["FILES"],
    )


@dataclass(frozen=True)
class HashSelection:
    """What a `-hash` run selected, and whether the caller should keep going.

    ``keep_going`` is False when hash mode has already answered the question the
    run asked: a read-only run whose working tree matches the baseline has
    nothing further to preview, so the caller returns 0 rather than falling
    through to a commit table with no rows in it.

    ``diff`` travels with the records because the build needs it after the
    selection is made: `diff.changed` is the hash of every file the patch is
    about to ship (hash mode forces the `local` content mode, so the bytes
    compared are the bytes written), and that is what lands in the patch's own
    `hashes.log`.
    """

    records: list[CommitRecord]
    keep_going: bool
    diff: HashDiff
    commits: dict[str, int]


def apply_hash_mode(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
    records: list[CommitRecord],
    *,
    create_requested: bool,
) -> HashSelection:
    baseline = read_baseline(
        resolve_baseline_path(root, config, args.target or "-", args.hash)
    )
    # No table here: `CHANGED FILES:` below is already the detail, and the one
    # number worth knowing rides the path line with its unit spelled out.
    print_adt_header("LOADING BASELINE:")
    print_file_rows(
        [f"{_project_relative(baseline.path, root)} ({_counted(baseline, baseline.stamp)})"],
        nested = False,
    )
    print()
    diff = diff_against_baseline(baseline, hash_working_tree(root, config))
    if diff.is_empty:
        if create_requested:
            raise PatchError(
                "no hash-changed files to patch: the working tree matches "
                f"{baseline.path.name} in every file the layout resolves"
            )
        return HashSelection(records=[], keep_going=False, diff=diff, commits={})
    commits = _diff_commits(diff, records)
    print_adt_header("CHANGED FILES:", str(len(diff.files)))
    print_adt_table(_hash_changed_rows(diff, commits))
    return HashSelection(
        records    = restrict_records_to_diff(records, diff),
        keep_going = True,
        diff       = diff,
        commits    = commits,
    )


def restrict_records_to_diff(
    records: list[CommitRecord], diff: HashDiff
) -> list[CommitRecord]:
    """The changed file set, carried as commit records the builder understands.

    Attribution is preserved rather than collapsed: a changed file stays on
    every in-window record that touched it, so `authoritative_commit` still
    names the newest real commit behind it and the processing report still
    prints one. What no record covers, a file whose commits fell outside
    `patch_scan_commits` or was never committed at all, rides one synthetic
    record carrying the deletions with it.

    That synthetic record is what makes hash mode unbounded by the scan window,
    which is the whole difference from `-rollout`: there, a file outside the
    window was dropped from its own patch and reported under a fallback header.
    """
    changed = diff.changed
    narrowed: list[CommitRecord] = []
    covered: set[str] = set()
    for record in records:
        files = {
            file: file_hash
            for file, file_hash in record.files.items()
            if file in changed
        }
        if not files:
            continue
        covered.update(files)
        narrowed.append(replace(record, files=files, deleted=[]))
    uncovered = {file: changed[file] for file in sorted(set(changed) - covered)}
    if uncovered or diff.deleted:
        narrowed.append(_uncommitted_record(uncovered, sorted(diff.deleted)))
    return narrowed


def _uncommitted_record(files: dict[str, str], deleted: list[str]) -> CommitRecord:
    """One record for what git history does not account for.

    Number `0` rather than a real one: it sorts before every stored commit, and
    a reader meeting it in the install header sees an unnumbered row instead of
    a number that would name somebody else's commit. Its `id` is empty, so the
    version lookups that read a commit hash resolve nothing and skip it rather
    than reading a wrong blob.
    """
    return CommitRecord(
        number   = 0,
        id       = "",
        summary  = "files with no commit in the scanned window",
        author   = "",
        date     = "",
        files    = files,
        deleted  = deleted,
        statuses = {},
    )


def _diff_commits(diff: HashDiff, records: list[CommitRecord]) -> dict[str, int]:
    """The commit to print beside each changed file.

    A live file reports the newest commit that touched it; a deleted one has
    none left in the tree, so the baseline's own record of where it came from is
    the only commit worth showing.
    """
    commits: dict[str, int] = {}
    for file in diff.files:
        record = authoritative_commit(file, records)
        if record is not None:
            commits[file] = record.number
        elif file in diff.baseline.commits:
            commits[file] = diff.baseline.commits[file]
    return commits


def _counted(baseline: Baseline, stamp: str) -> str:
    """`412 files, measured 2026-08-19 17:40`, dropping whatever is not known.

    The unit is spelled because a bare number beside a filename is exactly the
    riddle `#453` was filed on. The source token rides in front of the stamp
    (`#452`) so the question the reader actually has, *is this a reading of the
    target or something somebody assumed about it*, is answered on the line that
    names the file rather than by opening it.
    """
    parts = [f"{len(baseline)} files"]
    when = " ".join(word for word in (baseline.source, stamp) if word)
    if when:
        parts.append(when)
    return ", ".join(parts)


__all__ = [
    "HashSelection",
    "apply_hash_mode",
    "hash_mode_error",
    "restrict_records_to_diff",
    "run_baseline",
]
