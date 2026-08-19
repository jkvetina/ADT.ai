"""Scanning git history into commit records, and narrowing them to a selection.

Reading a patch FOLDER back off disk is the other half and lives in
`patch_folders.py` since ADT #309 split this file at the 20 KB context guard.
Everything there is re-exported below, so an importer that reached for
`PatchFolder`, `patch_id`, `discover_patch_folders` or `parse_patch_folder` here
still finds them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared.commit_cache import (
    DEFAULT_COMMITS_TEMPLATE,
    current_branch,
    open_store,
)
from adt_ai.shared.commit_store import StoredCommit
from adt_ai.shared.git_files import ChangedFile
from adt_ai.shared.patch_folders import (  # noqa: F401  (re-exported for existing importers)
    PATCH_FOLDER_RE,
    PatchFolder,
    _patch_file_references,
    _patch_script_commits,
    _patch_script_files,
    _read_lines,
    _repo_relative_reference,
    discover_patch_folders,
    matches_patch_selector,
    parse_patch_folder,
    patch_folder_match_targets,
    patch_id,
)

FIELD_SEPARATOR = "\x1f"


def ensure_commit_store_current(
    root: Path,
    *,
    branch: str,
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE,
    history_bottom_days: int | None = None,
    reporter: object | None = None,
) -> str:
    """Bring one branch's commit store level with git, and say which branch.

    `patch` reads commit NUMBERS out of this store and writes them into a patch
    folder, so a store short of `HEAD` does not merely miss a commit: it hands
    out a window that disagrees with the repository the operator is looking at,
    and `-commit 41` means one thing in the console and another in the folder.
    Jan, 2026-08-15: *"before running anything it must check that commits .db for
    requested branch is up to date"*.

    Update-only, so it costs a bounded walk from the stored tip rather than a
    rebuild, and it allocates nothing that already carries a number. Returns the
    branch it worked on so a caller that passed ``None`` learns the resolved name
    without asking git twice.
    """
    from adt_ai.rebuild.models import RebuildRequest
    from adt_ai.rebuild.runner import RebuildRunner

    RebuildRunner().run(
        RebuildRequest(
            root                = root,
            branches            = [branch],
            cache_file_template = cache_file_template,
            update_only         = True,
            history_bottom_days = history_bottom_days,
        ),
        reporter = reporter,
    )
    return branch


@dataclass(frozen=True)
class PatchRequest:
    root: Path
    commit_limit: int
    patch_code: str | None = None
    # Hash mode picks its commits by which file HASHES moved, so the patch code
    # must not also narrow them by subject, old ADT replaced the filtered list
    # outright in that mode (`filtered_commits = self.hash_commits`,
    # patch.py:417).
    hash_mode: bool = False
    # `rebuild` sat here until ADT #345. Nothing ever read it, so `patch
    # -rebuild` filled it and the run carried on unchanged.
    search_terms: list[str] | None = None
    authors: list[str] | None = None
    commit_refs: list[str] | None = None
    ignore_commits: list[str] | None = None
    files_only: bool = False
    include_full_exports: bool = False
    # `patch_commit_pattern`, a regex a commit SUMMARY must match to be
    # selected at all (old ADT config.yaml:113, patch.py:1012-1017). Empty or
    # `None` is the shipped default and filters nothing.
    commit_pattern: str | None = None
    # The branch to scan, or `None` for whatever is checked out (ADT #309,
    # was #275). Old ADT's `-branch` overrode the active branch (patch.py:70);
    # here it stays a read-only selector, it changes which commits are
    # scanned, never which branch the working tree is on.
    branch: str | None = None
    # Where the branch's commit store lives (`repo_commits_file`). `patch` reads
    # the SAME store `rebuild` writes rather than keeping a private copy: Jan,
    # 2026-08-15, *"Dependencies are reused, the commits should be reused too.
    # If your source data (commits cache) is not up to speed, you should refresh
    # the shared commits file, not to create a fucking copy!"*
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE
    # `patch_history_bottom_days`, forwarded to the top-up so the store `patch`
    # builds on first use is bounded the same way `rebuild` would build it.
    history_bottom_days: int | None = None


@dataclass(frozen=True)
class CommitRecord:
    number: int
    id: str
    summary: str
    author: str
    date: str
    files: dict[str, str]
    deleted: list[str]
    patch: str | None = None
    # Git's per-file status letter for this commit (`A`/`M`/`D`/...), kept so the
    # install script can split its file list into NEW / DELETED / MODIFIED the way
    # old ADT did (patch.py:1766-1780). The text cache could not carry it, which
    # is what left `search_repo` guessing; the store does, so `#358` made it a
    # real field rather than one that survived only inside a single run.
    statuses: dict[str, str] | None = None

    @property
    def commit_hash(self) -> str:
        return self.id

    @property
    def file_statuses(self) -> dict[str, str]:
        return self.statuses or {}

    @property
    def usable_files(self) -> dict[str, str]:
        return self.files

    @property
    def deleted_files(self) -> list[str]:
        return self.deleted

    @property
    def detected_patch(self) -> str | None:
        return self.patch

    @property
    def file_classes(self) -> dict[str, str]:
        return {
            path: file_class
            for path in self.files
            if (file_class := _classify_file(path, include_full_exports=True)) is not None
        }



class GitCommitCache:
    """`patch`'s view of history: the shared commit store, topped up on read.

    It used to walk git itself and number what it found by position, which made
    it a second, differently-numbered copy of the cache `rebuild` maintains, and
    the YAML it wrote back had no reader anywhere in the package. Now there is
    one store per branch and both commands use it.
    """

    def build(
        self,
        request: PatchRequest,
        *,
        reporter: object | None = None,
        top_up: bool = True,
    ) -> list[CommitRecord]:
        return _filter_records(
            self.scan(request, reporter=reporter, top_up=top_up), request
        )

    def scan(
        self,
        request: PatchRequest,
        *,
        reporter: object | None = None,
        top_up: bool = True,
    ) -> list[CommitRecord]:
        """Every commit in the scanned window, before any filter runs.

        ``build`` is this narrowed to what the patch selects. Both halves are
        needed: the selection is what gets patched, and the full window is what
        `#277` compares it against, a newer commit dropped by the patch-code or
        author filter is exactly the one worth warning about, because nothing else
        in the run mentions it (old ADT scanned `self.all_files`, everything it
        had cached, patch.py:1636).

        The window (`patch_scan_commits`) bounds the READ, never the numbering:
        the numbers come out of the store, which allocated them once, so a wider
        or narrower scan reports the same commit under the same number.

        ``top_up=False`` says the caller has already brought the store level with
        git this run. `#367` moved that call to the front of the `patch` command
        so the deploy-only, `-install` and `-archive` paths, all of which return
        before this scan, get a current store too; passing the flag here keeps
        that from walking the branch a second time.
        """
        branch = request.branch or current_branch(request.root)
        if top_up:
            self._top_up(request, branch, reporter=reporter)
        with open_store(request.root, branch, request.cache_file_template) as store:
            # `recent` is an indexed lookup, newest first, and never materialises
            # the branch: reading forty rows off the end of an 85,000-commit
            # history is the query this store exists for. Reversed here because
            # every consumer downstream expects oldest first.
            stored = store.recent(branch, request.commit_limit)
        return [_as_record(item, request) for item in reversed(stored)]

    @staticmethod
    def _top_up(request: PatchRequest, branch: str, *, reporter: object | None) -> None:
        ensure_commit_store_current(
            request.root,
            branch              = branch,
            cache_file_template = request.cache_file_template,
            history_bottom_days = request.history_bottom_days,
            reporter            = reporter,
        )

    @staticmethod
    def file_history(records: list[CommitRecord]) -> dict[str, dict[str, object]]:
        history: dict[str, dict[str, object]] = {}
        for record in records:
            for path in record.usable_files:
                entry = history.setdefault(
                    path,
                    {
                        "first_commit": record.number,
                        "last_commit": record.number,
                        "last_hash": record.commit_hash,
                        "newer_committed": False,
                    },
                )
                if record.number != entry["last_commit"]:
                    entry["last_commit"] = record.number
                    entry["last_hash"] = record.commit_hash
                    entry["newer_committed"] = True
        return history


def _as_record(stored: StoredCommit, request: PatchRequest) -> CommitRecord:
    """One stored commit as `patch` reads it, with this run's file policy applied.

    The store holds every changed file, because what a commit touched is a fact
    about history. Whether `apex/<app>/f<id>.sql` counts is a fact about the run
    (`-fullapp`), so it is decided here, on the way out, and the same store
    serves a run that wants it and one that does not.
    """
    return CommitRecord(
        number   = stored.number,
        id       = stored.id,
        summary  = stored.summary,
        author   = stored.author,
        date     = stored.date,
        files    = {
            path: file_hash
            for path, file_hash in stored.files.items()
            if _classify_file(path, include_full_exports=request.include_full_exports) is not None
        },
        deleted  = stored.deleted,
        patch    = stored.patch,
        statuses = stored.statuses,
    )


def _detected_patch(changed_files: list[ChangedFile]) -> str | None:
    for changed_file in changed_files:
        parts = Path(changed_file.path).parts
        if len(parts) >= 2 and parts[0].lower() == "patch":
            return parts[1]
    return None



def _filter_records(records: list[CommitRecord], request: PatchRequest) -> list[CommitRecord]:
    filtered = records
    if request.ignore_commits:
        ignored = {value.lower() for value in request.ignore_commits}
        filtered = [
            record
            for record in filtered
            if not _matches_any_ref(record, ignored)
        ]
    if request.commit_refs:
        selected = {value.lower() for value in request.commit_refs}
        filtered = [
            record
            for record in filtered
            if _matches_any_ref(record, selected)
        ]
    if request.authors:
        authors = [value.lower() for value in request.authors]
        filtered = [
            record
            for record in filtered
            if any(author in record.author.lower() for author in authors)
        ]
    if request.search_terms:
        terms = [value.lower() for value in request.search_terms]
        filtered = [
            record
            for record in filtered
            if all(_record_contains(record, term) for term in terms)
        ]
    elif request.patch_code and not request.commit_refs and not request.hash_mode:
        # With no explicit `-search`, the patch code IS the search term, old ADT
        # patch.py:147, matched against the commit SUMMARY only (patch.py:1005),
        # never the file paths. Without this, `-patch 65` listed every recent
        # commit and left the operator to spot theirs (ADT #257).
        #
        # The one deliberate divergence: an explicit `-commit <n>` bypasses it.
        # Old ADT filtered those too, so a named commit whose subject missed the
        # code was dropped silently, and `-patch <name> -create -commit <n>` is
        # the documented IVORY build sequence, where that would build an empty
        # patch and still report success.
        needle = request.patch_code.lower()
        filtered = [record for record in filtered if needle in record.summary.lower()]
    if request.commit_pattern and not request.search_terms and not request.commit_refs:
        # `patch_commit_pattern`, a project whose commits all carry a ticket
        # reference declares the shape once and gets every stray `wip` commit
        # kept out of every patch (old ADT config.yaml:113, patch.py:1012-1017).
        #
        # Two exemptions, and old ADT only had the first. It skipped the pattern
        # when `-search` was given (patch.py:1013), because an explicit search IS
        # the filter. `-commit` is exempted here as well: old ADT applied the
        # pattern to commits the user had NAMED (the check runs after the
        # `add_commits` gate at :979), so `-commit 12` on a commit whose subject
        # missed the pattern built an empty patch and still reported success.
        # That is the same failure `#257` fixed for the patch-code filter, and it
        # gets the same answer, a commit you named is an instruction.
        pattern = re.compile(request.commit_pattern)
        filtered = [record for record in filtered if pattern.search(record.summary)]
    if request.files_only:
        filtered = [record for record in filtered if record.usable_files or record.deleted_files]
    return filtered


# `12-40`, an inclusive commit-number range. Both sides must be digits: that is
# what keeps a hash prefix from ever being read as a range, whatever characters it
# happens to carry.
_COMMIT_RANGE_RE = re.compile(r"^(?P<start>\d+)-(?P<stop>\d+)$")
# `12+`, that commit and everything newer.
_COMMIT_FROM_RE = re.compile(r"^(?P<start>\d+)\+$")


def commit_ref_matches(number: int, commit_hash: str, ref: str) -> bool:
    """Does one `-commit` / `-ignore` argument select this commit?

    Three spellings, all old ADT's (`util.ranged_str`, util.py:755-767, resolved
    by `get_search_full`, patch.py:1073-1082):

    * `12`     , that commit number, or a hash prefix
    * `12+`    , commit 12 and everything newer
    * `12-40`  , the inclusive span

    Shared by `patch` and `search_repo` rather than written twice: they are one
    concept at two call sites, and `search_repo` already understood `N+` while
    `patch` understood neither, so `docs/patch.md`'s documented "commit numbers
    or ranges" selected nothing at all, silently (ADT #309, was #15).
    """
    value = str(ref).strip().lower()
    if not value:
        return False
    span = _COMMIT_RANGE_RE.match(value)
    if span:
        return int(span.group("start")) <= number <= int(span.group("stop"))
    onward = _COMMIT_FROM_RE.match(value)
    if onward:
        return number >= int(onward.group("start"))
    return value == str(number) or commit_hash.lower().startswith(value)


def _matches_any_ref(record: CommitRecord, refs: set[str]) -> bool:
    return any(
        commit_ref_matches(record.number, record.commit_hash, ref)
        for ref in refs
    )


def _record_contains(record: CommitRecord, term: str) -> bool:
    haystack = "\n".join(
        [
            record.summary,
            record.author,
            *record.usable_files.keys(),
            *record.deleted_files,
        ]
    ).lower()
    return term in haystack


def _classify_file(path: str, *, include_full_exports: bool) -> str | None:
    parts = Path(path).parts
    if not parts:
        return "file"
    # `database`/`apex` lead in the legacy layout and follow the schema in the
    # default layout (<schema>/database/..., <schema>/apex/...). Recognise both.
    if len(parts) >= 4 and parts[0].lower() == "database":
        return f"database:{parts[1]}:{parts[2]}"
    if len(parts) >= 4 and parts[1].lower() == "database":
        return f"database:{parts[0]}:{parts[2]}"
    apex_at = (
        0 if parts[0].lower() == "apex"
        else 1 if len(parts) >= 2 and parts[1].lower() == "apex"
        else None
    )
    if apex_at is not None and len(parts) >= apex_at + 3:
        app = parts[apex_at + 1]
        if len(parts) == apex_at + 3 and re.fullmatch(r"f\d+\.sql", parts[apex_at + 2].lower()):
            return f"apex:{app}:full" if include_full_exports else None
        return f"apex:{app}:component"
    if len(parts) >= 2 and parts[0].lower() == "patch":
        return f"patch:{parts[1]}"
    return "file"
