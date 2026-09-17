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

from dataclasses import replace

from adt_ai.cli.commands_patch_actions import (
    RECENT_PATCH_FOLDERS_HEADER,
    preview_folders,
    print_patch_folders,
    print_patch_plan,
)
from adt_ai.cli.constants import (
    PatchError,
    PatchWorkspace,
    outstanding_records,
    print_adt_header,
)
from adt_ai.cli.patch_create_render import print_folder_commits
from adt_ai.cli.patch_preview_render import (
    patch_scan_commits,
    patch_show_patches,
)
from adt_ai.shared.commit_discovery import CommitRecord, PatchRequest, _filter_records
from adt_ai.shared.commit_selection import _like_pattern, recent_patch_marker
from adt_ai.shared.patch_folders import PatchFolder


def name_is_the_commit_filter(request: PatchRequest) -> bool:
    """Did `-name` narrow the COMMITS, as well as naming the patch?

    The same condition `commit_selection._filter_records` selects that branch on,
    asked here so a failure can only blame the name filter where it actually ran.
    All three exemptions are real: `-search` replaces the term, a `-commit` is an
    instruction the code filter never touches, and hash mode picks by file hash
    rather than by subject.
    """
    return bool(
        request.patch_code
        and not request.search_terms
        and not request.commit_refs
        and not request.hash_mode
    )


def _candidates_without_the_name(
    workspace: PatchWorkspace,
    request: PatchRequest,
    window: list[CommitRecord],
    selected_folder: PatchFolder | None,
) -> list[CommitRecord]:
    """The commits this run would offer if the name had not filtered them.

    Every OTHER narrowing the run asked for still applies, so what this counts is
    the list the operator gets from the same command without `-create`, not a raw
    scan total answering a question nobody asked. The already-patched subtraction
    follows `records_for_header`'s rule: a listing of a named patch exempts that
    patch's own folder and no other.
    """
    others = [
        folder
        for folder in workspace.discover()
        if selected_folder is None or folder.folder != selected_folder.folder
    ]
    return outstanding_records(
        _filter_records(window, replace(request, patch_code = None)), others
    )


def answer_without_commits(
    workspace: PatchWorkspace,
    config: dict[str, object],
    request: PatchRequest,
    records: list[CommitRecord],
    patch_ref: str,
    selected_folder: PatchFolder | None,
    create_requested: bool,
    window: list[CommitRecord] | None = None,
) -> int:
    """The exit code for `-name <ref>` when the scan selected no commit.

    Two different failures used to share one message. Which one it is turns on
    whether the code named a real folder (ADT #285).

    ``window`` is the run's scan BEFORE any filter, which is what tells a build
    refusal which of its own two failures it is looking at (ADT #752).
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
    # A BUILD, and there is nothing to build from. Two very different things put
    # a run here and the message said only the rarer one (ADT #752). A client hit
    # the other on 2026-09-09: `patch -recent 2` listed two unpatched commits,
    # `patch -name ADT1 -recent 2 -create` then answered `NO COMMITS FOUND ... in
    # the last 100 commits`, which reads as a contradiction and sent him to a
    # config knob that was never short. His words: *"the command without -create
    # sees 2 commits, but with -create it sees nothing"*.
    #
    # So the run asks its own scan which failure this is instead of assuming.
    scan = window or []
    shipped = recent_patch_marker(scan, request)
    if shipped is not None and _filter_records(scan, replace(request, include_patched=True)):
        # The code DID match commits, and an earlier patch of it had shipped them
        # all (ADT #851). Without this the branch below said no subject carried
        # the name, which is the one thing that was not true. Jan chose this
        # screen on chips, 2026-09-16, over a one-line refusal.
        number, folder = shipped
        raise PatchError(
            f'NO NEW COMMITS FOR "{patch_ref}".\n\n'
            f"Patch {folder} was committed in commit {number},\n"
            "so every older commit carrying it has already shipped.\n\n"
            "  1) -force                     include the commits that patch shipped\n"
            "  2) -commit N [-ignore N]      select them by number, hash or range"
        )
    candidates = (
        _candidates_without_the_name(workspace, request, scan, selected_folder)
        if scan and name_is_the_commit_filter(request)
        else []
    )
    if candidates:
        # The name emptied the list, and the operator has no reason to know that
        # `-name` is two things at once. So the message says which of the two did
        # this, quotes the pattern it actually ran, and counts what it rejected,
        # because that count is the very listing he just saw.
        #
        # Two numbered OPTIONS rather than a paragraph of remedies, and a NOTE
        # for the habit that stops the whole thing happening again. Jan, having
        # read the first version: *"this looks very book style (too long,
        # prose). I would like to see more like options: 1) apply -search tag
        # 2) use -commit -ignore filters. Then mention a note that this can be
        # prevented if he use desired patch name (card number) in the commit
        # message."* The prevention note is the useful half: `-name 65` matches
        # `%65%` against subjects, so a repository that writes its ticket into
        # the subject never reaches this screen at all.
        raise PatchError(
            f'NO COMMITS MATCHED "{patch_ref}".\n\n'
            # The pattern closes its own line: it is the operator's string and
            # can be any length, so nothing after it inherits the overflow.
            "-name is also the commit filter, matched against commit SUBJECTS as\n"
            f'"{_like_pattern(request.patch_code or patch_ref)}", and no subject carries it.\n'
            f"The scan holds {len(candidates)} commit(s) that pass every other filter.\n\n"
            "  1) -search PATTERN            select them by a different term\n"
            "  2) -commit N [-ignore N]      select them by number, hash or range\n\n"
            "NOTE: put the patch name in the commit message and -name finds them\n"
            "      by itself."
        )
    # Nothing survived even without the name, so the scan really is the thing
    # that is short. The knob is `patch_scan_commits` since ADT #351 removed
    # `-window`, so the message names the config key rather than a flag that no
    # longer parses.
    # Same option shape as the branch above, so one screen has one vocabulary
    # and the reader is choosing between remedies rather than parsing a
    # sentence for them (ADT #752).
    raise PatchError(
        f'NO COMMITS FOUND for "{patch_ref}" in the last '
        f"{patch_scan_commits(config)} commits scanned.\n\n"
        "  1) patch_scan_commits         raise it in config to reach further back\n"
        "  2) -commit N [-ignore N]      select them by number, hash or range"
    )


__all__ = [
    "answer_without_commits",
    "name_is_the_commit_filter",
]
