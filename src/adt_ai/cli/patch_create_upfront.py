"""`RELEVANT COMMITS:` and `RECENT UNPATCHED COMMITS:`, before the patch folder exists (ADT #988).

Split out of `patch_create_render.py` at the context-size guard, the same
seam `#465` drew once already in that module. The seam here is timing: this
owns the answer `PatchBuildStages.validated` prints while `patch/build.py` is
still mid-build, with no folder on disk yet to read the two tables off.
"""

from __future__ import annotations

from adt_ai.cli.constants import preview_rows, preview_rows_from, print_adt_header, print_adt_table
from adt_ai.cli.patch_preview_render import (
    RECENT_COMMITS_HEADER,
    RELEVANT_COMMITS_HEADER,
    patch_show_commits,
)
from adt_ai.shared.commit_discovery import CommitRecord


def upfront_commit_entries(records: list[CommitRecord]) -> list[tuple[object, str, str]]:
    """The `-- COMMITS:` rows a build is about to write, read forward (ADT #988).

    `patch/summary.py::change_summary_comment` writes
    `f"--   {record.number}) {record.summary}"` for every record in
    ``records``, in that order, into every schema script the build writes at
    least one of; `folder_commit_entries` then reads that header back off disk
    and REVERSES it (newest first). This is the same two steps without the
    round trip through disk -- writing in `records` order and reversing the
    result is `reversed(records)` -- so it needs no folder to exist. Pinned
    byte-identical to `folder_commit_entries` on a built folder by
    `tests/cli/test_patch_create_streams_sections.py`.
    """
    return [(record.number, "", record.summary) for record in reversed(records)]


def print_create_commit_listings_upfront(
    records: list[CommitRecord],
    will_write: bool,
    config: dict[str, object],
) -> None:
    """`RELEVANT COMMITS:` and `RECENT UNPATCHED COMMITS:`, before the folder exists (ADT #988).

    `PatchBuildStages.validated` fires ahead of `folder.mkdir`, so there is no
    freshly written folder on disk yet to read the tables back off. Both come
    from ``records`` and ``will_write`` (`bool(files)`, `patch/build.py`).

    `will_write=True`, the ordinary build: `change_summary_comment` writes
    every one of ``records`` into the schema script(s) it writes, unfiltered
    by which record's own files landed where, so every one is about to be
    carried. `RELEVANT COMMITS:` prints `upfront_commit_entries`; `RECENT
    UNPATCHED COMMITS:` has nothing left to name.

    `will_write=False`, the one build #988's brief flags as unreconcilable
    with a pure `records` read: every selected commit resolves to no
    patchable file (`test_create_lists_a_matching_but_unshippable_commit_as_
    outstanding`), so no schema script is ever written and every record stays
    outstanding. `RELEVANT COMMITS:` prints nothing; `RECENT UNPATCHED
    COMMITS:` prints all of ``records`` (nothing on disk can already carry a
    commit `records` still holds, by `run_window`'s own contract).
    """
    if will_write:
        entries = upfront_commit_entries(records)
        if not entries:
            return
        print_adt_header(RELEVANT_COMMITS_HEADER)
        print_adt_table(preview_rows_from(entries), columns=["#", "MESSAGE"])
        return
    if not records:
        return
    print_adt_header(RECENT_COMMITS_HEADER)
    print_adt_table(preview_rows(records, patch_show_commits(config)))


__all__ = [
    "print_create_commit_listings_upfront",
    "upfront_commit_entries",
]
