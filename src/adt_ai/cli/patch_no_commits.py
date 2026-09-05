"""What a named patch answers when the commit scan reached nothing.

Split out of `commands_patch.py` when ADT #467 pushed that module past the 20 KB
context guard (`tests/contracts/test_context_file_size.py`), the sixth carve off
that dispatcher after `patch_preview_render`, `patch_hash_mode`,
`patch_dependency_refresh`, `patch_create_render` and `patch_inputs`, and the
same call every one of them made: a module that crosses the guard is split, never
registered as debt.

The seam is a decision, not a slice of line count. Three different situations
used to share one error message and were separated by `#285`, `#353` and `#417`
over three cards, and what tells them apart is one question, did the code name a
folder that exists on disk. That question and its three answers are one idea, and
`commands_patch.py` is left dispatching verbs.
"""

from __future__ import annotations

from adt_ai.cli.commands_patch_actions import (
    RECENT_PATCH_FOLDERS_HEADER,
    preview_folders,
    print_patch_folders,
    print_patch_plan,
)
from adt_ai.cli.constants import (
    PatchError,
    PatchWorkspace,
    print_adt_header,
)
from adt_ai.cli.patch_create_render import print_folder_commits
from adt_ai.cli.patch_preview_render import (
    patch_scan_commits,
    patch_show_patches,
)
from adt_ai.shared.commit_discovery import CommitRecord, PatchRequest
from adt_ai.shared.patch_folders import PatchFolder


def answer_without_commits(
    workspace: PatchWorkspace,
    config: dict[str, object],
    request: PatchRequest,
    records: list[CommitRecord],
    patch_ref: str,
    selected_folder: PatchFolder | None,
    create_requested: bool,
) -> int:
    """The exit code for `-name <ref>` when the scan selected no commit.

    Two different failures used to share one message. Which one it is turns on
    whether the code named a real folder (ADT #285).
    """
    if selected_folder is None and not create_requested:
        # The code matches no patch on disk. Jan, 2026-08-10: "asking for non
        # existing patch should show the same things as when just asking for
        # -patch", the useful answer is the inventory, because the question
        # behind a miss is always "then what IS there?". Settled with Jan on
        # chips the same day: it still SAYS nothing matched and still exits
        # non-zero, so a typo cannot masquerade as a deliberate listing in
        # either the output or the exit code.
        print_adt_header(f'NO PATCH MATCHED "{patch_ref}":')
        # `RECENT PATCH FOLDERS:` since ADT #510, the same header the bare run
        # prints, because this is the same narrowed table: capped, and cut down
        # by the run's own filters. The `-archive` listing is the uncapped one
        # and carries `ALL PATCH FOLDERS:` instead.
        print_patch_folders(
            # The run's own filters apply here too (ADT #467). The question
            # behind a miss is "then what IS there?", and under `-my` that
            # is "what is there of mine".
            preview_folders(workspace, records, request.authors, request.recent),
            RECENT_PATCH_FOLDERS_HEADER,
            patch_show_patches(config),
        )
        return 2
    # An existing folder under the LOOK verb still answers (ADT #353). The
    # question `-name ABC` asks is "what is in this patch", and its contents are
    # on disk whether or not the scan still reaches the commits that built it, so
    # refusing here would withhold the answer over a detail of the commit window.
    # A build still refuses: it has nothing to build from.
    if selected_folder is not None and not create_requested:
        # The commits it holds come off its own header, which is the only source
        # left when the scan reached none (ADT #417).
        print_folder_commits(selected_folder)
        print_patch_plan(workspace, config, patch_ref)
        return 0
    # The folder exists, so the scan is the thing that is short. The knob is
    # `patch_scan_commits` since ADT #351 removed `-window`, so the message names
    # the config key rather than a flag that no longer parses.
    raise PatchError(
        f'NO COMMITS FOUND for "{patch_ref}" in the last '
        f"{patch_scan_commits(config)} commits "
        "- raise patch_scan_commits in config, or select commits explicitly with -commit N"
    )


__all__ = [
    "answer_without_commits",
]
