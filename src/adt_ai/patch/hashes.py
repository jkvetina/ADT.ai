"""The baseline: what the target environment is believed to already hold.

Rebuilt by ADT #447 around one complete file per environment. What stood here
before was old ADT's rollout machinery: a folder of `rollout.<N>.log` snapshots,
each holding only the lines that CHANGED at commit N, which a run reassembled by
merging every prior log below its target. Two things were wrong with it and
neither was the file format.

It compared the **commit store's** hashes rather than the working tree, so it
answered "which committed blobs moved" and never "what does my repo look like
now", the question the old OPY workflow Jan described was actually asking. And
it narrowed the result to commits inside `patch_scan_commits`, so a file changed
long ago and never deployed silently fell out of its own patch.

A baseline answers both by being complete. Reading it needs no merge, a diff
against it is three named classes rather than one dict, and nothing about it
depends on how far back the commit scan reached.

One hash function does both sides: `file_payload_hash` reduces a payload to one
canonical form before hashing it, so the value the commit store recorded for a
committed file and the value read here off disk are the same string for the same
content. Since ADT #454 that canonical form is LF line endings with the whole
payload trimmed once, which is what lets a baseline recorded on Windows be read
on macOS, and it is why the value is no longer `git hash-object` minus its
header. Read the baseline rather than reproducing a hash by hand.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.patch.layout import is_apex_path as _is_apex_path
from adt_ai.patch.layout import is_database_path as _is_database_path

# `models`, not `runner`: `runner` imports this module to write a patch's own
# `hashes.log`, so reading `PatchError` back off it would close a cycle. It is
# the same class either way, `runner` only re-exports it.
from adt_ai.patch.models import PatchError
from adt_ai.shared import text_files
from adt_ai.shared.git_files import file_payload_hash, run_git_paths

DEFAULT_HASH_FOLDER = "patch_hashes/"

#: The pre-`#453` layout: one folder per environment, each holding a fixed
#: `baseline.log`. Still read, never written, so an upgrading project's refusal
#: can point at the file it already has instead of claiming there is none.
LEGACY_BASELINE_FILE = "baseline.log"

#: `baseline.<ENV>.log` matched back to its environment, for that same pointer.
_BASELINE_NAME_RE = re.compile(r"^baseline\.(?P<env>.+)\.log$")


def baseline_filename(target_env: str) -> str:
    """The baseline's filename, which carries its own environment (`#453`).

    Jan, 2026-08-21, on a folder-per-environment layout: *"I also think this
    filename should contain ENV_NAME, like baseline.DEV.log, baseline.UAT.log."*
    One flat folder holding every environment a project tracks, so `ls` answers
    which environments have a baseline, and a file copied out of that folder is
    still self-describing.

    Fixed rather than configurable: `patch_hashes` already places the folder and
    `-hash FILE` / `-baseline FILE` already spell a whole address, so a third
    knob would only add a way to disagree with itself.
    """
    return f"baseline.{target_env.upper()}.log"

#: Written into a patch folder by `-create -hash`, in the same three-column
#: shape. Its presence is what marks a patch as hash-built, which is how
#: `-deploy` tells the two modes apart with no extra flag.
PATCH_HASHES_FILE = "hashes.log"


@dataclass(frozen=True)
class Baseline:
    path: Path
    hashes: dict[str, str]
    commits: dict[str, int]
    stamp: str
    #: `snapshot`, `deployed` or `measured` (`#452`); blank for a file written
    #: before the token existed. It is the difference between a belief about the
    #: target and a reading of it, which nothing else on the file records.
    source: str = ""

    def __len__(self) -> int:
        return len(self.hashes)


@dataclass(frozen=True)
class HashDiff:
    """What moved since the baseline, split by what kind of move it was.

    The three classes are kept apart because the build treats them differently:
    modified and added files are snapshotted and linked, deleted ones generate
    the DROP helper. Merging them into one dict, which is what the rollout code
    did, is what made a deletion indistinguishable from an unrecorded file.
    """

    baseline: Baseline
    modified: dict[str, str]
    added: dict[str, str]
    deleted: dict[str, str]

    @property
    def changed(self) -> dict[str, str]:
        """Everything the patch ships: modified plus added, deletions excluded."""
        return {**self.modified, **self.added}

    @property
    def is_empty(self) -> bool:
        return not (self.modified or self.added or self.deleted)

    @property
    def files(self) -> list[str]:
        return sorted({*self.modified, *self.added, *self.deleted})

    def status(self, path: str) -> str:
        if path in self.modified:
            return "MODIFIED"
        if path in self.added:
            return "NEW"
        if path in self.deleted:
            return "DELETED"
        return ""


def baseline_change_counts(
    previous: Mapping[str, str] | None,
    current: Mapping[str, str],
) -> dict[str, int] | None:
    """What moved between the baseline being replaced and the one being written.

    `None` when there was no previous file, which is the first-run case: five
    rows of zero beside a total is noise, so the caller prints the total alone.

    This is the answer `WRITING BASELINE: 412` was failing to give. Jan,
    2026-08-21: *"remove the number after header, I dont know what it is, number
    of files? Lines? who knows"*. A count of files in the new file says nothing
    about what changed; these four classes do.
    """
    if previous is None:
        return None
    unchanged = sum(1 for file, value in current.items() if previous.get(file) == value)
    modified = sum(
        1 for file, value in current.items()
        if file in previous and previous[file] != value
    )
    return {
        "UNCHANGED": unchanged,
        "MODIFIED" : modified,
        "NEW"      : sum(1 for file in current if file not in previous),
        "REMOVED"  : sum(1 for file in previous if file not in current),
        "TOTAL"    : len(current),
    }


def read_baseline_hashes(path: Path) -> dict[str, str] | None:
    """The hashes a baseline already records, or None when there is no file.

    Separate from `read_baseline` because the caller here is about to OVERWRITE
    the file and a missing one is the ordinary first run, not a refusal.
    """
    if not path.is_file():
        return None
    hashes, _commits, _stamp, _source = _parse_hash_lines(path)
    return hashes


def hash_baseline_folder(root: Path, config: Mapping[str, object], target_env: str) -> Path:
    raw = str(config.get("patch_hashes") or DEFAULT_HASH_FOLDER).strip("/")
    raw = raw.replace("{$TARGET_ENV}", target_env.upper())
    raw = raw.replace("#TARGET_ENV#", target_env.upper())
    return root / raw


def resolve_baseline_path(
    root: Path,
    config: Mapping[str, object],
    target_env: str,
    override: str | None,
) -> Path:
    """Where this run's baseline lives.

    ``override`` is the optional value on `-hash` / `-baseline`. Empty string is
    argparse's flag-given-with-no-value sentinel, not a filename, so it resolves
    exactly as an absent flag does. A relative value resolves against the
    project root, the way every other path on the command line is spelled; an
    absolute one stands, which is what lets a baseline live outside the repo.
    """
    if override:
        candidate = Path(override).expanduser()
        return candidate if candidate.is_absolute() else root / candidate
    return hash_baseline_folder(root, config, target_env) / baseline_filename(target_env)


def legacy_baseline_path(path: Path) -> Path | None:
    """Where this baseline would have lived before `#453`, or None.

    Derived from the filename rather than from the config, so it answers for a
    resolved path alone and needs no second read of the layout. A `-baseline
    FILE` override matches no pattern and reports None: a hand-named file has no
    old address to have moved from.
    """
    matched = _BASELINE_NAME_RE.match(path.name)
    if matched is None:
        return None
    return path.parent / matched.group("env") / LEGACY_BASELINE_FILE


def read_baseline(path: Path) -> Baseline:
    if not path.is_file():
        legacy = legacy_baseline_path(path)
        if legacy is not None and legacy.is_file():
            # The upgrade case, and the reason it is worth a branch: without it
            # a project that HAS a baseline is told it has none, which reads as
            # a defect rather than as a one-command move (`#453`).
            raise PatchError(
                f"no baseline at {path.name}, but {legacy.parent.name}/{legacy.name} is "
                "still there from the folder-per-environment layout: the environment "
                "moved onto the filename, so record it again with "
                "adtai patch -target <ENV> -baseline"
            )
        raise PatchError(
            f"no baseline at {path.name}: hash mode compares the working tree against "
            "a recorded baseline, and there is none yet "
            "- run adtai patch -target <ENV> -baseline to record one"
        )
    hashes, commits, stamp, source = _parse_hash_lines(path)
    return Baseline(
        path=path, hashes=hashes, commits=commits, stamp=stamp, source=source
    )


def hash_working_tree(root: Path, config: dict[str, Any]) -> dict[str, str]:
    """Every file the project's own layout resolves, hashed as it stands on disk.

    The candidate list comes from git rather than a directory walk, so an
    ignored file is not part of the repo and not part of the baseline. A root
    that is not a checkout falls back to walking, because `export_db` supports
    an ordinary folder and a baseline should not be the one thing that needs git.
    """
    return {
        path: file_payload_hash((root / path).read_bytes())
        for path in _candidate_files(root)
        if (_is_database_path(path, config) or _is_apex_path(path, config))
        and (root / path).is_file()
    }


def diff_against_baseline(baseline: Baseline, current: Mapping[str, str]) -> HashDiff:
    modified: dict[str, str] = {}
    added: dict[str, str] = {}
    for path, file_hash in current.items():
        if path not in baseline.hashes:
            added[path] = file_hash
        elif file_hash != baseline.hashes[path]:
            modified[path] = file_hash
    deleted = {
        path: file_hash
        for path, file_hash in baseline.hashes.items()
        if path not in current
    }
    return HashDiff(baseline=baseline, modified=modified, added=added, deleted=deleted)


#: How a baseline knows what it knows (`#452`). Appended AFTER the count, never
#: before the stamp, because `_header_stamp` reads the date and time by position.
SNAPSHOT = "snapshot"   # `patch -baseline` assumed the working tree
DEPLOYED = "deployed"   # a deploy advanced it by what a patch shipped
MEASURED = "measured"   # `export_db -baseline` connected and read the target


def write_baseline(
    path: Path,
    hashes: Mapping[str, str],
    commits: Mapping[str, int],
    *,
    target_env: str,
    stamp: str,
    source: str = SNAPSHOT,
) -> Path:
    """Write the baseline whole, recording how it knows.

    ``source`` is the difference between a belief and a measurement, and it is
    on the file because nothing else can tell them apart afterwards. Jan,
    2026-08-21, having proposed advancing a baseline from deploys and then
    argued against his own proposal: *"That does not reflect what we actually
    might have there."* Correct, and the answer is to say which one it is.
    """
    return _write_hash_lines(
        path,
        hashes,
        commits,
        header = (
            f"# baseline {target_env.upper()} {stamp} ({len(hashes)} files) {source}"
        ),
    )


def merge_into_baseline(
    path: Path,
    shipped: Mapping[str, str],
    commits: Mapping[str, int],
    *,
    target_env: str,
    stamp: str,
) -> tuple[Path, int]:
    """Advance the baseline by exactly what a patch shipped, and nothing else.

    Everything the patch did not carry keeps the hash and commit it already had,
    which is what leaves work done between `-create` and `-deploy` pending
    instead of silently recording it as deployed. Returns the number of paths
    that actually moved, so the console can report it.
    """
    existing_hashes: dict[str, str] = {}
    existing_commits: dict[str, int] = {}
    if path.is_file():
        existing_hashes, existing_commits, _stamp, _source = _parse_hash_lines(path)
    advanced = sum(
        1 for file, value in shipped.items() if existing_hashes.get(file) != value
    )
    existing_hashes.update(shipped)
    existing_commits.update(commits)
    written = write_baseline(
        path,
        existing_hashes,
        {file: commits for file, commits in existing_commits.items() if file in existing_hashes},
        target_env = target_env,
        stamp      = stamp,
        # A deploy records what a patch CLAIMS it landed, which is a belief about
        # the target however careful the three rules above are (`#452`).
        source     = DEPLOYED,
    )
    return written, advanced


def replace_measured_scope(
    path: Path,
    measured: Mapping[str, str],
    commits: Mapping[str, int],
    *,
    covered: Callable[[str], bool],
    target_env: str,
    stamp: str,
) -> tuple[Path, dict[str, int]]:
    """Fold a measured reading of one scope into the baseline (`#452`).

    **Inside the scope it REPLACES, outside it leaves alone.** Every recorded
    path that ``covered`` claims is dropped and the measurement put in its place;
    everything else keeps what it had. Merging instead would leave a stale entry
    for an object the target no longer holds, and `patch -hash` would then never
    ship that object again, which is the silent half of the failure.

    The scope is what the run actually looked at, so a measurement of one schema
    cannot speak for another, and APEX paths, which `export_db` never sees, keep
    whatever belief they already carried.

    Returns the counts for the console beside the written path.
    """
    previous: dict[str, str] = {}
    previous_commits: dict[str, int] = {}
    if path.is_file():
        previous, previous_commits, _stamp, _source = _parse_hash_lines(path)
    kept = {file: value for file, value in previous.items() if not covered(file)}
    combined = {**kept, **measured}
    kept_commits = {
        file: value for file, value in previous_commits.items() if file in kept
    }
    written = write_baseline(
        path,
        combined,
        {**kept_commits, **commits},
        target_env = target_env,
        stamp      = stamp,
        source     = MEASURED,
    )
    in_scope_before = {file: value for file, value in previous.items() if covered(file)}
    return written, {
        "UNCHANGED": sum(
            1 for file, value in measured.items() if in_scope_before.get(file) == value
        ),
        "MODIFIED": sum(
            1 for file, value in measured.items()
            if file in in_scope_before and in_scope_before[file] != value
        ),
        "NEW": sum(1 for file in measured if file not in in_scope_before),
        "REMOVED": sum(1 for file in in_scope_before if file not in measured),
        "TOTAL": len(combined),
    }


def write_patch_hashes(
    folder: Path,
    shipped: Mapping[str, str],
    commits: Mapping[str, int],
    *,
    patch_code: str,
    stamp: str,
) -> Path:
    """Record what this patch ships, inside the patch folder.

    Two jobs, and the second is why it is a file rather than a return value: it
    makes the folder self-describing, and its PRESENCE is the hash-built marker
    `-deploy` reads to decide whether advancing the baseline is this patch's to
    do. A commit-built patch has none and advances nothing.
    """
    return _write_hash_lines(
        folder / PATCH_HASHES_FILE,
        shipped,
        commits,
        header = f"# patch {patch_code} {stamp} ({len(shipped)} files)",
    )


def read_patch_hashes(folder: Path) -> tuple[dict[str, str], dict[str, int]]:
    """What a patch folder recorded, or two empty dicts for a commit-built one."""
    path = folder / PATCH_HASHES_FILE
    if not path.is_file():
        return {}, {}
    hashes, commits, _stamp, _source = _parse_hash_lines(path)
    return hashes, commits


def _write_hash_lines(
    path: Path,
    hashes: Mapping[str, str],
    commits: Mapping[str, int],
    *,
    header: str,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [header]
    lines.extend(
        f"{file} | {commits.get(file, '')} | {hashes[file]}" for file in sorted(hashes)
    )
    text_files.write_text(path, "\n".join(lines) + "\n")
    return path


def _parse_hash_lines(
    path: Path,
) -> tuple[dict[str, str], dict[str, int], str, str]:
    hashes: dict[str, str] = {}
    commits: dict[str, int] = {}
    stamp = ""
    source = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            stamp = _header_stamp(line)
            source = _header_source(line)
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) != 3 or not parts[0]:
            continue
        hashes[parts[0]] = parts[2]
        if parts[1].isdigit():
            commits[parts[0]] = int(parts[1])
    return hashes, commits, stamp, source


def _header_stamp(line: str) -> str:
    """The `YYYY-MM-DD HH:MM` out of `# baseline UAT <stamp> (n files)`.

    Read positionally rather than by pattern: the header is this module's own
    output, and a stamp it cannot recognise is worth reporting as blank rather
    than worth a regex nobody will maintain.
    """
    parts = line.split()
    return " ".join(parts[3:5]) if len(parts) >= 5 else ""


def _header_source(line: str) -> str:
    """The trailing `snapshot` / `deployed` / `measured` token, or blank.

    `write_baseline` appends it after the count, so it is the last word and is
    read as one. A header written before `#452` ends on `files)` and reports
    blank, which is the honest answer: that file predates the question.
    """
    parts = line.split()
    tail = parts[-1] if parts else ""
    return tail if tail in {SNAPSHOT, DEPLOYED, MEASURED} else ""


def _candidate_files(root: Path) -> list[str]:
    """Every path a baseline can consider: git's own view of a checkout, or a
    plain filesystem walk when there is no checkout to ask (ADT #670).

    The two fallbacks used to be one `except Exception`, which read a git
    failure INSIDE a real checkout (git not on PATH, a stale index lock) the
    same as no checkout at all, and answered with an unfiltered `rglob("*")`
    that silently folded a `.gitignore`d file into the baseline. The checkout
    question is answered first and cheaply, off the filesystem alone: no
    `.git` entry means `export_db`'s ordinary-folder support is what applies,
    and the layout predicate decides membership rather than git, exactly as
    before. Once there IS a `.git`, a git failure is real and is raised as a
    refusal naming the cause, never swallowed into "walk everything".
    """
    if not (root / ".git").exists():
        return _walk_all_files(root)
    try:
        # Through the shared NUL-splitting reader: `ls-files` C-quotes any
        # non-ASCII path in its default output, so a Czech-named object was
        # missing from the hash index with no message (ADT #664).
        tracked = run_git_paths(root, ["ls-files"])
        untracked = run_git_paths(root, ["ls-files", "--others", "--exclude-standard"])
    except Exception as error:
        raise PatchError(
            f"git ls-files failed inside {root}, so the baseline cannot tell "
            f"which files are ignored: {error}\n"
            "Run: fix the git failure (PATH, a stale index lock) before "
            "baselining a real checkout"
        ) from error
    return sorted({*tracked, *untracked})


def _walk_all_files(root: Path) -> list[str]:
    """Every file under ``root``, `.git` itself excluded, for a non-checkout."""
    return [
        str(item.relative_to(root).as_posix())
        for item in sorted(root.rglob("*"))
        if item.is_file() and ".git" not in item.parts
    ]
