"""Where a deploy's own logs land inside a patch folder.

Split out of `patch/layout.py` when that module crossed the 20 KB context guard
(ADT #471). The seam is the one its docstring already draws: `layout.py` resolves
a REPO path against `path_objects`, while this pair resolves a PATCH-FOLDER path
against `patch_deploy_logs`. Different template, different tree, one reader each.

`layout.py` re-exports both, so every existing caller is untouched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

#: The environment token plus whichever separator sits beside it, so a run with
#: no target loses both rather than the token alone.
_TARGET_ENV_TOKEN_RE = re.compile(r"[-_. ]?(?:\{\$TARGET_ENV\}|#TARGET_ENV#)[-_. ]?")


def deploy_log_folder(config: dict[str, Any], target_env: str | None) -> str:
    """The patch-folder-relative folder every log for ``target_env`` lands in.

    One resolver, because two writers need the same answer and disagreed: the
    install script's own SPOOL wrote beside `<SCHEMA>.sql` while `-deploy` wrote
    its captured copy under `logs_<ENV>/`, so every deployed patch left a stray
    `<SCHEMA>.log` in the folder root (Jan, 2026-08-09: "The only logs shoud be
    in logs_{ENV}/ subfolder", ADT #260).

    With no `-target` the environment half of the name has nothing to say, so the
    separator holding its place goes too: the shipped `logs_{$TARGET_ENV}` used to
    resolve to a folder literally named `logs_`, which is what a real project
    carries on disk (ADT #431). The separator comes out of the template rather
    than being assumed, so `{$TARGET_ENV}_logs` resolves to `logs` as well.
    """
    raw = str(config.get("patch_deploy_logs") or "logs_{$TARGET_ENV}").strip("/")
    if not target_env:
        return _TARGET_ENV_TOKEN_RE.sub("", raw)
    return (
        raw
        .replace("{$TARGET_ENV}", target_env)
        .replace("#TARGET_ENV#", target_env)
    )


def ensure_deploy_log_folder(
    patch_folder: Path,
    config: dict[str, Any],
    target_env: str | None,
) -> Path:
    """Create the folder the install script's own SPOOL line writes into.

    SQLcl does not create a spool directory: a missing one is `SP2-0556: Invalid
    file name.`, and the script's `WHENEVER OSERROR EXIT ROLLBACK` turns that into
    a deploy that applies nothing (ADT #270). Two callers need it, `-create`, so
    the folder is part of the patch on disk, and `-deploy`, because git does not
    track an empty directory and a cloned patch folder arrives without it.

    Deliberately routed through ``deploy_log_folder`` rather than composing the
    name here: the SPOOL line and this folder must be the same string, and a
    second derivation is exactly how they came apart.
    """
    folder = patch_folder / deploy_log_folder(config, target_env)
    folder.mkdir(parents=True, exist_ok=True)
    return folder
