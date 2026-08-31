"""Scanning git history into commit records, and narrowing them to a selection.

Reading a patch FOLDER back off disk is the other half and lives in
`patch_folders.py` since ADT #309 split this file at the 20 KB context guard.
Everything there is re-exported below, so an importer that reached for
`PatchFolder`, `patch_id`, `discover_patch_folders` or `parse_patch_folder` here
still finds them. ADT #429 split the same guard again and moved deciding what a
changed path IS into `commit_file_classes.py`, re-exported for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared.commit_cache import (
    DEFAULT_COMMITS_TEMPLATE,
    current_branch,
    open_store,
)
from adt_ai.shared.commit_file_classes import (  # noqa: F401  (re-exported)
    classify_file,
)
from adt_ai.shared.commit_file_classes import (  # noqa: F401  (re-exported)
    classify_file as _classify_file,
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
    named_patch_refs,
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
    # `-recent`, a day window over the commit DATE (ADT #467). Resolved at the
    # CLI edge, so what arrives here is a number or `None`, never the bare-flag
    # sentinel: git history has no export watermark for a bare `-recent` to
    # resume from, which is the same call `search_repo` makes.
    recent: int | float | None = None
    commit_refs: list[str] | None = None
    ignore_commits: list[str] | None = None
    files_only: bool = False
    include_full_exports: bool = False
    # The APEX export heads to recognise, most specific first, as
    # `patch/layout.apex_head_variants` derives them from `path_apex`. Empty is
    # the classic reading, `apex/` at the top or one level under the schema,
    # which is what every caller but `patch` still passes: the layout keys are
    # `patch`'s to resolve and this module stays free of its imports (ADT #429).
    apex_heads: tuple[tuple[str, ...], ...] = ()
    # `patch_commit_pattern`, a regex a commit SUMMARY must match to be
    # selected at all (old ADT config.yaml:113, patch.py:1012-1017). Empty or
    # `None` is the shipped default and filters nothing.
    commit_pattern: str | None = None
    # No `skip_merge` field, and that is a re-confirmed MEASUREMENT rather than an
    # omission: ADT #430 set out to port old ADT's `patch_skip_merge` and found
    # #309's pin still holding. See
    # `tests/patch/test_commit_filters.py::test_a_merge_commit_carries_no_files_in_the_scan`.
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
    (`-app`), so it is decided here, on the way out, and the same store
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
            if _classify_file(
                path,
                include_full_exports = request.include_full_exports,
                apex_heads           = request.apex_heads,
            ) is not None
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

# The selection half moved to `commit_selection.py` when ADT #467 pushed this
# module past the 20 KB context guard. Re-exported so every existing importer,
# `patch`, `search_repo` and their tests, keeps reaching them here.
from adt_ai.shared.commit_selection import (  # noqa: E402,F401  (re-exported)
    _filter_records,
    _like_pattern,
    _matches_any_ref,
    _record_contains,
    _within_window,
    commit_ref_matches,
    matches_author,
)
