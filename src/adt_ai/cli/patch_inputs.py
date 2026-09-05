"""What a `patch` run resolves before it acts on anything.

Inputs read from the repository and from config rather than typed at the command
line, and needed by every action rather than by one of them: the branch's commit
store, the author filter, and the assembled `PatchRequest` that says which commits
this run is about. Split out of `commands_patch.py` when ADT #367 pushed that
module past the 20 KB context guard (`tests/contracts/test_context_file_size.py`),
and grown again by ADT #430 for the same reason.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

from adt_ai.cli.context import _config_search_paths, _repo_root
from adt_ai.cli.context_apex import _flatten_arg_groups
from adt_ai.cli.patch_preview_render import patch_scan_commits
from adt_ai.patch.apex_import import resolve_target
from adt_ai.patch.layout import apex_head_variants
from adt_ai.patch.topup import ConsoleTopUpReporter
from adt_ai.shared.commit_cache import DEFAULT_COMMITS_TEMPLATE, current_branch
from adt_ai.shared.commit_discovery import PatchRequest, ensure_commit_store_current
from adt_ai.shared.commit_window import resolve_history_floor
from adt_ai.shared.identity import resolve_commit_email
from adt_ai.shared.patch_folders import PatchFolder
from adt_ai.shared.recent_state import is_bare_recent


def searching_without_narrowing(args: argparse.Namespace) -> bool:
    """Whether this run asked to FIND commits rather than to ship them.

    The three narrowing flags are Jan's own list, 2026-08-27, and they are read
    for presence rather than for content: `-commit` and `-ignore` both parse as
    `append` + `nargs="+"`, so an empty list can only mean the flag was absent.
    `-search` narrows the preview and never the selection, which is exactly why
    it does not appear here as a narrowing flag itself.

    Moved here from `commands_patch.py` by ADT #592 for the reason everything
    else in this module moved: that file sits on the 20 KB context guard, and a
    predicate that reads nothing but `args` is an input question rather than a
    step of the patch flow.
    """
    if not getattr(args, "search", None):
        return False
    return not (
        getattr(args, "force", False)
        or getattr(args, "commit", None)
        or getattr(args, "ignore", None)
    )


def build_patch_request(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
    *,
    selected_folder: PatchFolder | None,
    patch_ref: str | None,
) -> PatchRequest:
    """Which commits this run is about, from the flags and the project config.

    Both halves of ONE scan: the selection is what gets patched, and the window is
    what `#277` compares it against (ADT #276/#277).
    """
    return PatchRequest(
        root                 = root,
        commit_limit         = patch_scan_commits(config),
        branch               = args.branch or None,
        # A resolved folder searches by its OWN patch code, never by the string
        # that selected it: `-name 260810-1-BLOCK_B_TESTS` and `-name 68` both
        # have to reach the commits carrying `TASK68_BLOCK_B_TESTS` (ADT #285).
        patch_code           = (
            selected_folder.patch_code if selected_folder else patch_ref
        ),
        hash_mode            = args.hash is not None,
        search_terms         = args.search,
        authors              = patch_authors(args, root),
        # A bare `-recent` is one day here (ADT #467), the same resolution
        # `search_repo` makes at its own edge and for the same reason: the
        # sentinel means "since my last export of this scope", and git history
        # keeps no such watermark. Resolved at the edge, so `PatchRequest` holds
        # a number and no reader below it has to know the sentinel exists.
        recent               = 1 if is_bare_recent(args.recent) else args.recent,
        commit_refs          = _flatten_arg_groups(args.commit),
        # Flattened exactly like `-commit` since ADT #354: same three spellings,
        # same list-of-lists shape out of `append` + `nargs="+"`.
        ignore_commits       = _flatten_arg_groups(args.ignore),
        # The flag's PRESENCE, never its contents: a bare `-app` is an empty
        # list, so reading it as `bool()` made asking for every application read
        # as asking for none, and the full exports never reached the run at all
        # (ADT #576, carried through the `-fullapp` -> `-app` fold in #592). A
        # target id is still a presence, which is why this reads `selected`
        # rather than the id.
        include_full_exports = resolve_target(args.app).selected,
        # The scanner suppresses a full app export unless `-app` asked for
        # one, and it can only do that where it recognises the export root, so it
        # is handed the heads `path_apex` resolves to (ADT #429).
        apex_heads           = tuple(apex_head_variants(config)),
        commit_pattern       = str(config.get("patch_commit_pattern") or "") or None,
        # One store, shared with `rebuild`, at the path the project configured.
        cache_file_template  = str(
            config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE
        ),
        history_bottom_days  = resolve_history_floor(config),
    )


def ensure_commit_store(
    args: argparse.Namespace,
    root: Path,
    config: dict[str, Any],
) -> str | None:
    """Level the requested branch's commit store with git; name it, or None.

    ``None`` means the top-up could not run, which is not a failure: a project
    root that is not a git checkout, or one with no commits yet, still gets its
    patch-folder listing (ADT #352), and a build that genuinely needs history
    fails later with the message naming what is missing. Returning the branch is
    what lets the commit scan skip a second walk over the same history.

    ``-branch NAME`` is honoured here as well as in the scan, so the store that
    gets levelled is the history the run is about to read, not whichever branch
    the working tree happens to be standing on.
    """
    try:
        branch = args.branch or current_branch(root)
        return ensure_commit_store_current(
            root,
            branch              = branch,
            cache_file_template = str(
                config.get("repo_commits_file") or DEFAULT_COMMITS_TEMPLATE
            ),
            history_bottom_days = resolve_history_floor(config),
            reporter            = ConsoleTopUpReporter(root, branch),
        )
    except (subprocess.CalledProcessError, OSError):
        return None


def patch_authors(args: argparse.Namespace, root: Path) -> list[str] | None:
    """The `-by` / `-my` author filter, or None when the run sets none.

    `-my` resolves through `shared/identity.resolve_commit_email`, one entry
    point for every git-backed `-my` in the tool since ADT #469: `IDENTITY.yaml`'s
    `email` when the project states one, `git config user.email` when it does
    not. `-config-dir` is honoured, because a caller that has one must not lose
    to a root-derived default.

    The value matched is an EMAIL either way, because that is what the commit
    STORE holds: `rebuild/cache.py` formats history with `%ae`, so a
    `CommitRecord.author` is an email in every project and `_filter_records`
    compares against exactly that field.

    It read `user.name` until ADT #467, on `#364`'s stated premise that a commit
    author line carries the name here. A commit does carry both; the store keeps
    one, and a filter reads the store. So `patch -my` selected nothing at all, in
    every project, and `-by "Jan Kvetina"` did the same while `-by` with an email
    worked.

    The generalisable half, and it is why `#469` followed: `#364` compared the
    two flags' HELP TEXT, found it inconsistent, and settled the difference by
    writing down which fact each module claimed, without asking any module what
    it read. `#467` then fixed the fact and left five spellings of the lookup in
    place. A difference between modules is a question about behaviour, and the
    answer is the data they share.
    """
    authors = list(args.by or [])
    if args.my:
        current_user = resolve_commit_email(
            _config_search_paths(args.config_dir, root, _repo_root()), root
        )
        if current_user:
            authors.append(current_user)
    return authors or None


__all__ = [
    "ensure_commit_store",
    "patch_authors",
]
