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

from adt_ai.shared.path_template import DEFAULT_PATH_APP

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

# Both token dialects a path template may be written in: `<schema>` for the keys
# that carry it, and the old-ADT `{$APP_ID}` for the one key that does
# (`shared/path_template.py` owns which is which). `apexlang_glob` reads a
# template back rather than rendering it, so it has to see either spelling.
_ANY_TOKEN = re.compile(r"<[^<>]*>|\{\$[^{}]*\}")

# `*` runs, collapsed after substitution. Two adjacent tokens leave `**` behind,
# which is a pattern of its own in every glob implementation, and one wildcard
# already matches everything two of them do.
_COLLAPSE_WILDCARDS = re.compile(r"\*{2,}")


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


def apexlang_glob(config: Mapping[str, Any] | None) -> str:
    """The one shape an APEXlang export is written at, as a glob pattern.

    An export lands at `path_apex` / `apex_path_app` / `apexlang`, and those two
    keys are already the project's own statement of where. Read back with every
    token globbed, they describe exactly the folders a bare run should compile
    and nothing else: on the shipped defaults that is `*/apex/*/apexlang`.

    **This replaces a subtraction, and the difference is the point (ADT #765).**
    Discovery used to `rglob` the whole repository and then remove the folders
    ADT.ai writes for itself, `patch_root` and `config/`, which is a list that
    needs a new entry every time a folder full of copies appears; the first
    version of it missed both and one project reported thirteen targets, twelve
    of them patch snapshots of the thirteenth, every row `EMPTY`. A shape needs no
    such list: a snapshot sits three levels too deep to match and
    `config/temp/apexlang` names `temp` where the pattern names `apex`. Jan,
    2026-09-10: "You should just match the /apex/ folder:
    `<schema>/apex/<apex_app>/apexlang`".

    **The two templates are read back differently, and deliberately.** In
    `path_apex` a literal segment is load-bearing (`apex` is the folder Jan
    named), so only the token inside a segment is globbed and `db_<schema>/`
    reads back as `db_*/`. `apex_path_app` contributes its LEVEL COUNT and
    nothing else, one bare `*` per level: the folder it writes is free text a
    developer typed in App Builder, reduced by :func:`app_folder_segment`, so
    reading `{$APP_ID}_{$APP_ALIAS}` back as `*_*` would pin punctuation the
    template only happens to have today and drop every export the moment a
    project re-spells the key. That is the same split the module already makes,
    :func:`app_folder_depth` existing precisely because the writer renders this
    template and the reader counts levels back off it (ADT #474).

    A missing config means the shipped defaults, because both callers reach here
    before a project necessarily has one: `doctor` diagnoses setups that have no
    config at all, and `validate` treats an absent one as the defaults.
    """
    settings = config or {}
    app_levels = app_folder_depth(str(settings.get("apex_path_app") or DEFAULT_PATH_APP))
    segments = [
        *_glob_segments(str(settings.get("path_apex") or "apex/")),
        *["*"] * app_levels,
        APEXLANG_DIR,
    ]
    return "/".join(segments)


def _glob_segments(template: str) -> list[str]:
    """One path template's segments, every token inside them replaced by a wildcard.

    `.` and empty runs are dropped for the reason :func:`app_folder_depth` drops
    them: `_clean_relative` drops them when the folder is built, so a pattern
    keeping them would not match what the export wrote. Adjacent tokens collapse
    to a single `*`, because `**` inside a segment is a pattern glob reads
    differently and two wildcards with nothing between them match what one does.
    """
    parts = str(template).replace("\\", "/").split("/")
    return [
        _COLLAPSE_WILDCARDS.sub("*", _ANY_TOKEN.sub("*", part))
        for part in parts
        if part and part != "."
    ]


def apexlang_folders(root: Path, config: Mapping[str, Any] | None) -> list[Path]:
    """Every APEXlang tree exported under this repo, sorted.

    One reader for the question "does this project export APEXlang, and where":
    `validate` asks it to know what to compile, `doctor` asks it to know whether
    the SQLcl floor applies at all (ADT #723).

    A tree that does not sit at :func:`apexlang_glob`'s shape is not an export,
    whatever else it is; an explicit `-input` still reaches it.
    """
    return sorted(
        path
        for path in root.glob(apexlang_glob(config))
        if path.is_dir() and not under_dot_folder(path, root)
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
    "apexlang_glob",
    "app_folder_depth",
    "app_folder_segment",
    "under_dot_folder",
]
