"""Reading a branch's history into the commits a patch is built from.

Split out of `runner.py` when ADT #367 pushed that module past the 20 KB context
guard (`tests/contracts/test_context_file_size.py`), the same call `#276` made
for `commands_patch_actions.py` and `#273` for `patch_deploy_render.py`: a module
that crosses the guard is split, never registered as debt.

The seam is the one the file already had. `PatchWorkspace` is the patch FOLDER,
what is on disk and what gets written to it; this is the COMMIT side, and the
two only meet where the CLI hands one's output to the other. Nothing here writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adt_ai.shared.commit_discovery import (
    CommitRecord,
    GitCommitCache,
    PatchRequest,
    _filter_records,
)

if TYPE_CHECKING:
    # `commit_discovery` itself only names this one for the checker, for
    # the reason it says: `rebuild` imports `shared`, not the other way.
    from adt_ai.rebuild.models import RebuildReporter


class PatchRunner:
    def __init__(self, cache: GitCommitCache | None = None) -> None:
        self.cache = cache or GitCommitCache()

    def run(
        self,
        request: PatchRequest,
        *,
        reporter: RebuildReporter | None = None,
        top_up: bool = True,
    ) -> list[CommitRecord]:
        return self.run_window(request, reporter=reporter, top_up=top_up)[0]

    def run_window(
        self,
        request: PatchRequest,
        *,
        reporter: RebuildReporter | None = None,
        top_up: bool = True,
    ) -> tuple[list[CommitRecord], list[CommitRecord]]:
        """``(selected, window)`` from ONE read of the branch's commit store.

        `#277` needs both, and the read is shared. Nothing is written back: the
        store IS the cache. The write-only YAML this command used to drop under
        `config/internal/` had one writer and no reader anywhere in `src/`, so
        it recorded the window and answered nothing; `#358` retired it.

        ``top_up=False`` says the caller already levelled the store this run,
        which `patch` does at the front of every command since `#367`.
        """
        window = self.cache.scan(request, reporter=reporter, top_up=top_up)
        return _filter_records(window, request), window


__all__ = ["PatchRunner"]
