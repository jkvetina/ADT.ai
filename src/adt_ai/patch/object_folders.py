"""Which folder a console file list groups an exported object under (ADT #504).

`shared/file_list.py` owns the SHAPE of a nested list and takes the folder rule
as an argument, because deciding it means reading `path_objects`, and that
template lives here. This module is that argument, spelled once.

**The level is the `path_objects` type folder, never the file's own directory.**
Jan's example, 2026-08-24, is what settles it: three synonyms all sitting in a
`UT/` group folder listed under `app_owner/database/synonyms/`, with
`UT/ut_dates.sql` on the leaf. Grouping by the parent directory would have
printed `synonyms/UT/`, and so would grouping by the longest prefix the paths
share, so neither is the rule he asked for. A group move relocates a file and
does not change what the file IS (`#498`), so one type still reads as one folder.

Nothing here is a second reader of the layout: the head comes from
`patch/layout.effective_object_head` and the type folder from
`shared/object_files.type_folder_of`, which is the exact-folder-first ordering
`#498` wrote so a multi-segment type folder like `grants/received` never has its
last segment mistaken for a group.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from adt_ai.patch.layout import effective_object_head, object_layouts
from adt_ai.shared.file_list import parent_folder
from adt_ai.shared.object_files import type_folder_of


def object_type_folder(path: str, config: dict[str, Any]) -> str | None:
    """``<schema>/database/<object_type>/`` for ``path``, or None when it is not one.

    The trailing slash is kept: a folder line and a file row open on the same
    dash, and the slash is what tells a reader which is which.
    """
    return _type_folder(path, config, object_layouts(config.get("object_types", {})))


def object_folder_resolver(config: dict[str, Any]) -> Callable[[str], str | None]:
    """The one folder rule a `patch` console list groups by.

    The type folder when the path is an exported object, the file's own directory
    otherwise. Both halves in one callable so no call site ever picks between
    them: a section lists object files, install scripts and per-patch scripts in
    the same breath, and which of the two a given row takes is a property of the
    row rather than of the section.

    The layouts are read once per section rather than once per row: a schema's
    block can list hundreds of files and the configured map does not change
    between two of them.
    """
    layouts = object_layouts(config.get("object_types", {}))

    def resolve(path: str) -> str | None:
        return _type_folder(path, config, layouts) or parent_folder(path)

    return resolve


def _type_folder(
    path: str,
    config: dict[str, Any],
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    head = effective_object_head(path, config, layouts)
    if head is None:
        return None
    parts = Path(path).parts
    owner = type_folder_of(Path(path).name, "/".join(parts[len(head):-1]), layouts)
    if owner is None:
        return None
    return "/".join([*parts[:len(head)], owner]) + "/"


__all__ = [name for name in globals() if not name.startswith("_")]
