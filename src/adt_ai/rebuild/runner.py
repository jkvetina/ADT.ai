from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved helpers.
import adt_ai.rebuild.cache as _cache
from adt_ai.rebuild.cache import (
    _build_records as _cache_build_records,
)
from adt_ai.rebuild.cache import (
    _cache_path,
    _commit_files,
    _current_branch,
    _require_branches_exist,
    _resolve_branches,
)
from adt_ai.rebuild.models import (
    RebuildError,
    RebuildReporter,
    RebuildRequest,
    RebuildResult,
)
from adt_ai.rebuild.reveal import (
    REVEAL_DEFAULT_LIMIT,
    BranchInfo,
    RevealResult,
    branch_commits,
    reveal_branches,
    switch_to_branch,
)


class _NullReporter:
    def on_count(
        self,
        total_commits: int,
        branch_count: int,
        commit_limit: int | None = None,
        missing_commits: int | None = None,
    ) -> None:
        pass

    def on_commit_start(self, index: int, total: int) -> None:
        pass

    def on_commit(self, index: int, total: int) -> None:
        pass


def _build_records(
    request: RebuildRequest,
    branches: list[str],
    reporter: RebuildReporter,
) -> tuple[dict[str, dict[int, object]], dict[str, object]]:
    original_commit_files = _cache._commit_files
    _cache._commit_files = _commit_files
    try:
        return _cache_build_records(request, branches, reporter)
    finally:
        _cache._commit_files = original_commit_files


class RebuildRunner:
    def run(
        self, request: RebuildRequest, reporter: RebuildReporter | None = None
    ) -> RebuildResult:
        _reporter = reporter or _NullReporter()
        branches = _resolve_branches(request)
        _require_branches_exist(request.root, branches)
        # Scanning and writing are one step now: the store IS the write, because
        # a number is assigned by inserting the row, never computed and then
        # serialized.
        branch_records, cache_paths = _build_records(request, branches, _reporter)
        return RebuildResult(
            cache_paths   = cache_paths,
            branches      = branches,
            record_counts = {branch: len(records) for branch, records in branch_records.items()},
        )


__all__ = [name for name in globals() if not name.startswith("__")]
