"""Folder names an APEX export writes, spelled once (ADT #602).

Four packages ask about the same folder for four different reasons:
`export_apex` writes it, `validate` compiles what is inside it, `patch` decides
that a file in it installs through an application import rather than through
SQLcl, and `doctor` reads whether this project exports APEXlang at all (ADT
#723). Until this module the name lived as a constant in `validate/files.py` and
as a literal in `export_apex/files.py`, which is two spellings of one folder and
the shape ADT #474 made a rule about.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# The whole-application APEXlang tree. Not a config key, unlike `path_apex` and
# its siblings: the exporter copies SQLcl's own layout verbatim under one root,
# so a project renaming this would be renaming somebody else's output.
APEXLANG_DIR = "apexlang"

# The one file under `workspace/rest/` that is not a module. `rest export` is a
# single PL/SQL block, and its preamble and trailer carry the roles and
# privileges no module owns, so `export_apex` parks them here. Spelled once for
# the same reason the folder above is: `export_apex` writes it and `patch` has to
# know it names no `user_ords_modules` row (ADT #724).
REST_SCHEMA_DEFINITION = "__enable_schema"

# Everything a value must not carry into a folder name: the two path separators,
# the characters Windows refuses in a component, and the control range. A run of
# them collapses to one underscore, so `Q1 */?"reports"` reads as `Q1 _reports_`
# rather than as five of them in a row. Underscores the developer typed are not
# in the class and are never collapsed.
_UNSAFE_SEGMENT_RUN = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]+')

# A leading or trailing dot or space, which is legal on POSIX and quietly dropped
# by Windows, so the folder ADT reports and the folder on disk stop being the
# same one. `..` alone is the case that matters most: rendered into
# `{$APP_ID}_{$APP_NAME}` it produced `100_..`, which `_clean_relative` does not
# refuse because the traversal is not a whole segment.
_EDGE_DOTS_OR_SPACES = (re.compile(r"^[. ]+"), re.compile(r"[. ]+$"))


def app_folder_segment(value: str) -> str:
    """One APEX value, reduced to something safe inside a single folder name.

    `apex_path_app` substitutes `{$APP_ALIAS}`, `{$APP_NAME}` and `{$APP_GROUP}`,
    and all three are free text a developer typed in App Builder. Substituted
    raw, an application named `ORDERS/23` silently nested a folder the `patch`
    reader could not see, `ORDERS:23` produced a path Windows cannot check out,
    and `..` produced a traversal segment (ADT #670). The template's own separators
    are untouched, because they are the layout the project configured; only the
    values passing through it are reduced.

    An empty value stays empty. It is the caller that knows whether a segment may
    legitimately vanish, and `_render_app_folder` refuses the folder when one
    does.
    """
    if not value:
        return ""
    cleaned = _UNSAFE_SEGMENT_RUN.sub("_", value)
    for pattern in _EDGE_DOTS_OR_SPACES:
        cleaned = pattern.sub("_", cleaned)
    return cleaned


def app_folder_depth(template: str) -> int:
    """How many folder levels `apex_path_app` names.

    The writer renders the template and the reader counts levels back off it, so
    the count has to be one function or the two disagree the moment a template
    spans more than one segment (`{$APP_GROUP}/{$APP_ID}`, ADT #474). `.` is
    dropped here because `_clean_relative` drops it when the folder is built.
    """
    parts = str(template).replace("\\", "/").split("/")
    return len([part for part in parts if part and part != "."])


def apexlang_search_root(root: Path, config: Mapping[str, Any] | None) -> Path:
    """The folder a whole-repo scan for APEXlang trees walks.

    ``path_apex`` may carry a ``<schema>`` token, which only resolves once a
    schema is bound, and a repo-wide scan has none. Walking from the static
    prefix before that token covers every schema at once without guessing which
    ones exist on disk.

    A missing config means the shipped default, because both callers reach here
    before a project necessarily has one: `doctor` diagnoses setups that have no
    config at all, and `validate` treats an absent one as the defaults.
    """
    configured = str((config or {}).get("path_apex") or "apex/")
    prefix = configured.split("<")[0].strip("/")
    candidate = root / prefix if prefix else root
    return candidate if candidate.is_dir() else root


def apexlang_folders(root: Path, config: Mapping[str, Any] | None) -> list[Path]:
    """Every APEXlang tree exported under this repo, sorted.

    One reader for the question "does this project export APEXlang, and where":
    `validate` asks it to know what to compile, `doctor` asks it to know whether
    the SQLcl floor applies at all (ADT #723).
    """
    base = apexlang_search_root(root, config)
    return sorted(
        path
        for path in base.rglob(APEXLANG_DIR)
        if path.is_dir() and not under_dot_folder(path, base)
    )


def under_dot_folder(path: Path, base: Path) -> bool:
    """Skip hidden folders *below the base* only.

    The base itself routinely sits under a dot folder, every ADT.ai task
    worktree lives in ``.worktrees/``, so judging the absolute path's parts
    would discover nothing there.
    """
    try:
        relative = path.relative_to(base)
    except ValueError:
        return False
    return any(part.startswith(".") for part in relative.parts)


__all__ = [
    "APEXLANG_DIR",
    "REST_SCHEMA_DEFINITION",
    "apexlang_folders",
    "apexlang_search_root",
    "app_folder_depth",
    "app_folder_segment",
    "under_dot_folder",
]
