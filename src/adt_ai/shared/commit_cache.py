"""Where a branch's commit store lives, and how an older cache becomes one.

The store itself is `commit_store.py`; this module owns the path, which is the
part a project configures. `repo_commits_file` and `#BRANCH#` still choose it,
one file per branch, because that is the control Jan asked for: *"For a better
user control and easier removals for bloated branches I would prefer 1 file per
branch"* (2026-08-15).

Only the SUFFIX follows the format. A project still carrying the pre-`#358`
`./config/commits/#BRANCH#.yaml` keeps its configured folder layout and writes
`main.db` beside the `main.yaml` it already has, which is also what makes the
conversion unambiguous: the two paths differ by one suffix, so nothing has to
guess where the old cache was.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from adt_ai.shared.commit_store import CommitStore, StoredCommit

#: Shipped `repo_commits_file`. One store per branch, under the project's own
#: `config/commits/`, which `#316`'s sweep deliberately left where it was.
DEFAULT_COMMITS_TEMPLATE = "./config/commits/#BRANCH#.db"

#: Characters a branch name may carry into a cache filename verbatim.
#:
#: Git is far more permissive about branch names than a filesystem is about
#: filenames, and `/` is the one that bites: `build/JANK` substituted raw into
#: `./config/commits/#BRANCH#.yaml` wrote `config/commits/build/JANK.yaml`, a
#: folder where a file belongs. An allowlist is the honest shape, because the
#: set of characters a branch may legally hold is open ended (accented letters,
#: `#`, `&`, `%`) while the set a filename should hold is small and fixed.
_UNSAFE_IN_FILENAME = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class HistoryRecord:
    number: int
    id: str
    summary: str
    author: str
    date: str
    files: dict[str, str]
    deleted: list[str]
    patch: str | None = None

    @property
    def commit_hash(self) -> str:
        return self.id


def current_branch(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "HEAD"


def branch_filename(branch: str) -> str:
    """``branch`` reduced to something usable as a single filename.

    Replacement is per character, so the mapping never depends on which
    character was replaced and two branches differing only outside the
    allowlist stay distinguishable. Normalizing happens here, at the path
    layer, and nowhere else: the real branch name still drives every git
    operation and every line the console prints.
    """
    return _UNSAFE_IN_FILENAME.sub("-", branch)


def cache_path(
    root: Path,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
    branch: str = "main",
) -> Path:
    # Only the substituted branch is sanitized. The template's own separators
    # are the folder layout the project configured through `repo_commits_file`,
    # and flattening those would move every existing cache.
    resolved = cache_file_template.replace("#BRANCH#", branch_filename(branch))
    path = Path(resolved).expanduser()
    return path if path.is_absolute() else root / path


def store_path(
    root: Path,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
    branch: str = "main",
) -> Path:
    """Where this branch's store file sits."""
    return cache_path(root, cache_file_template, branch).with_suffix(".db")


def legacy_cache_path(
    root: Path,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
    branch: str = "main",
) -> Path:
    """Where the YAML cache this branch may still have sits."""
    return cache_path(root, cache_file_template, branch).with_suffix(".yaml")


def open_store(
    root: Path,
    branch: str,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
) -> CommitStore:
    """The branch's store, converting an older YAML cache the first time.

    Conversion, never a rebuild: the YAML's numbers come across verbatim and
    the fields it could not carry (git's status letters, full app exports) are
    filled in by the next scan that reaches those commits. A from-scratch build
    happens only where there is nothing to convert.

    The YAML is then deleted, and `#368` widened that to every open rather than
    only the converting one. `#358` left the original standing beside the store
    it had just produced, so a branch carried two caches of one history and the
    one nothing reads was the one a reader would open first. Jan, 2026-08-15, on
    the same shape in `db_dependencies.yaml`: *"it is a duplicate junk at that
    point"*. Conditional on the store existing, so a root that has not converted
    yet keeps the only copy it has.
    """
    path = store_path(root, cache_file_template, branch)
    first_open = not path.exists()
    store = CommitStore.open(path)
    if first_open:
        import_legacy_cache(store, root, branch, cache_file_template)
    drop_legacy_cache(root, branch, cache_file_template)
    return store


def drop_legacy_cache(
    root: Path,
    branch: str,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
) -> bool:
    """Delete this branch's YAML cache once its store exists. True when it went.

    Never raises: a read-only checkout keeps the file and the command proceeds,
    exactly as `shared/internal_paths` treats every other housekeeping sweep.
    """
    store = store_path(root, cache_file_template, branch)
    legacy = legacy_cache_path(root, cache_file_template, branch)
    if not store.is_file() or not legacy.is_file():
        return False
    try:
        legacy.unlink()
    except OSError:
        return False
    return True


def import_legacy_cache(
    store: CommitStore,
    root: Path,
    branch: str,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
) -> int:
    """Adopt a YAML cache into ``store`` unchanged. Returns how many rows moved."""
    records = load_history_cache(root, branch, cache_file_template)
    if not records:
        return 0
    store.adopt(
        branch,
        [
            StoredCommit(
                number  = number,
                id      = record.id,
                summary = record.summary,
                author  = record.author,
                date    = record.date,
                files   = record.files,
                deleted = record.deleted,
                # The YAML never carried statuses. Leaving them empty is the
                # honest shape: a guess here would be indistinguishable from a
                # real letter downstream, which is the approximation
                # `search_repo` is losing.
                patch   = record.patch,
            )
            for number, record in sorted(records.items())
        ],
    )
    return len(records)


def load_history_cache(
    root: Path,
    branch: str,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
) -> dict[int, HistoryRecord]:
    """The pre-`#358` YAML cache, read for conversion and nothing else."""
    path = legacy_cache_path(root, cache_file_template, branch)
    if not path.is_file():
        return {}
    # A hand-edited or truncated cache must degrade to "no cache" (the next
    # rebuild rewrites it from git), not crash the caller, but never silently:
    # a partial cache resumed as-is could pin history to the wrong tip.
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("cache root is not a mapping")
        records: dict[int, HistoryRecord] = {}
        for number, fields in data.items():
            records[int(number)] = HistoryRecord(
                number  = int(number),
                id      = fields["id"],
                summary = fields.get("summary", ""),
                author  = fields.get("author", ""),
                date    = fields.get("date", ""),
                files   = fields.get("files") or {},
                deleted = fields.get("deleted") or [],
                patch   = fields.get("patch"),
            )
        return records
    except Exception as error:
        print(
            f"Warning: ignoring unreadable commit cache {path}: {error}",
            file=sys.stderr,
        )
        return {}
