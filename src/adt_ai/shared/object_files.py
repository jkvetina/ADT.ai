"""Which object an exported file holds: its type, and its name.

`object_types` maps a type onto a folder and an extension, and it puts TWO types
on one folder twice in the shipped config: `PACKAGE` and `PACKAGE BODY` on
`packages/` under `.spec.sql` and `.sql`, `TYPE` and `TYPE BODY` on `types/`
under `.sql` and `.body.sql`. In each pair the shorter extension is a SUFFIX of
the longer one, so a reader that stops at the first extension a filename ends
with answers by dict order, and a reader that strips one suffix keeps the other
half of the pair in the name.

Both mistakes were live at once. `types/t_row.body.sql` resolved to `TYPE`
because the shipped config lists `TYPE` above `TYPE BODY`, and
`packages/core.spec.sql` produced the object name `CORE.SPEC`, which is not an
Oracle identifier and killed `patch -create` outright (ADT #471).

The rule that settles both is one line long, longest matching extension wins,
and `export_db` has applied it correctly since ADT #412. It lived in
`export_db/groups.py`, where `patch` and `search_repo` could not import it,
`patch` sitting beside `export_db` rather than under it, so each grew its own
reading and the three disagreed. It lives here for the same reason
`shared/object_types.py` does: what a file IS has to mean the same thing on
every command that reads one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any


def object_layouts(raw_types: Any) -> dict[str, tuple[str, str]]:
    """The configured ``object_types`` as ``TYPE -> (folder, extension)``.

    Both spellings a project may write are accepted, the two-item list the
    shipped config uses and the ``{folder, extension}`` mapping.
    """
    if not isinstance(raw_types, dict):
        return {}
    layouts: dict[str, tuple[str, str]] = {}
    for object_type, raw_layout in raw_types.items():
        if isinstance(raw_layout, dict):
            folder = raw_layout.get("folder")
            extension = raw_layout.get("extension")
        elif isinstance(raw_layout, list | tuple) and len(raw_layout) == 2:
            folder, extension = raw_layout
        else:
            continue
        if isinstance(folder, str) and isinstance(extension, str):
            layouts[str(object_type).upper()] = (folder.strip("/"), extension)
    return layouts


def extensions_for_folder(layouts: dict[str, tuple[str, str]], folder: str) -> set[str]:
    """Every extension configured onto ``folder``, so ``owns_file`` has its siblings."""
    target = folder.strip("/")
    return {
        extension
        for _object_type, (layout_folder, extension) in layouts.items()
        if layout_folder.strip("/") == target
    }


def group_parent_folder(folder: str) -> str | None:
    """The type folder a GROUP sub-folder sits in, or None when there is none.

    `export_db -groups` arranges a type folder's files into sub-folders, and
    `export_db/groups.py::detect_groups_from_tree` defines a group as an
    IMMEDIATE sub-folder of a type folder. So the reader answers for exactly one
    level: a tree nested deeper than the writer can produce is not a group
    arrangement, and resolving it would invent an object for a path nothing
    exports.
    """
    head, separator, _group = folder.strip("/").rpartition("/")
    return head if separator and head else None


def _type_owning_file_in(
    file_name: str,
    folder: str,
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    """`owning_object_type` for one exact folder, no group tolerance."""
    candidates = {
        object_type: extension
        for object_type, (layout_folder, extension) in layouts.items()
        if layout_folder.strip("/") == folder
    }
    siblings = set(candidates.values())
    for object_type, extension in candidates.items():
        if owns_file(extension, siblings - {extension}, Path(file_name)):
            return object_type
    return None


def type_folder_of(
    file_name: str,
    folder: str,
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    """Which of ``folder`` or its group parent is the TYPE folder owning the file.

    The same question `owning_object_type` answers, stopping one step earlier at
    WHERE the type is configured rather than WHICH type it is. A console list
    groups an object file under that folder, so an `export_db -groups` sub-folder
    stays on the leaf and one type reads as one folder (ADT #504).

    Split out rather than re-derived beside the renderer: the exact-folder-first
    ordering is `#498`'s and a second reading of it would put a multi-segment type
    folder like ``grants/received`` back at risk of having its last segment read
    as a group.
    """
    target = folder.strip("/")
    if _type_owning_file_in(file_name, target, layouts) is not None:
        return target
    parent = group_parent_folder(target)
    if parent is None:
        return None
    return parent if _type_owning_file_in(file_name, parent, layouts) is not None else None


def owning_object_type(
    file_name: str,
    folder: str,
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    """The configured type that owns ``file_name`` in ``folder``, or None.

    Order-independent by construction: each candidate is asked whether it owns
    the file against the other extensions on the same folder, so exactly one can
    answer yes and reordering `object_types` changes nothing.

    ``folder`` may be a GROUP sub-folder rather than the type folder itself. A
    group move relocates a file and does not change what the file IS, so a folder
    no type claims is retried one level up (ADT #498). The exact folder is tried
    FIRST, because a type folder may legitimately span more than one segment
    (`grants/received`) and its last segment must never be read as a group.

    Before this, `patch` matched the group folder against `object_types` whole:
    `packages/CORE/core_lock.sql` resolved to no type and no name at all while
    its flat spelling resolved to `PACKAGE BODY`/`CORE_LOCK`. That made
    `patch -create` read a group move as a mass deletion, writing a `DROP` per
    relocated object into the same script that installed it, and sorting every
    moved file into the trailing `patch_map` bucket.
    """
    owner = type_folder_of(file_name, folder, layouts)
    return None if owner is None else _type_owning_file_in(file_name, owner, layouts)


def object_name_for_type(
    file_name: str,
    object_type: str,
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    """The object name ``file_name`` carries, uppercased, read through its extension."""
    stem = object_stem_for_type(file_name, object_type, layouts)
    return None if stem is None else stem.upper()


def object_stem_for_type(
    file_name: str,
    object_type: str,
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    """The same name with the casing the export wrote on disk.

    Stripping the extension and uppercasing are two different questions and only
    the first one was ever wrong. A caller that renders the name into generated
    SQL keeps whatever case the file carries, because a patch script is a
    compatibility contract and `app_areas` becoming `APP_AREAS` would rewrite
    every ALTER a project has, for no behaviour: an unquoted Oracle identifier is
    case-insensitive. A caller matching against the data dictionary wants
    `object_name_for_type`, since `user_objects` stores the upper form.
    """
    layout = layouts.get(str(object_type).upper())
    if layout is None:
        return None
    name = Path(file_name).name
    extension = layout[1]
    return name[: -len(extension)] if extension and name.endswith(extension) else name


def object_name_from_file(file_path: Path, extension: str) -> str:
    """Uppercased object name from an exported file: the filename minus its extension."""
    name = file_path.name
    if name.endswith(extension):
        name = name[: -len(extension)]
    return name.upper()


def owns_file(extension: str, sibling_extensions: Iterable[str], file_path: Path) -> bool:
    """Whether a file in a shared folder belongs to `extension` or to a sibling of it.

    Two object types can be configured onto the same folder with different
    extensions, and the shipped config does it twice: `PACKAGE` and `PACKAGE BODY`
    both write `packages/` under `.spec.sql` and `.sql`, `TYPE` and `TYPE BODY` both
    write `types/` under `.sql` and `.body.sql`. In each pair the shorter extension
    is a suffix of the longer one, so a glob run for the shorter type matches the
    longer type's files as well. The file belongs to the longest extension it ends
    with, and to nothing else (ADT #412, where a package spec was planned for a move
    twice and the second rename found the source already gone).
    """
    name = file_path.name
    if not name.endswith(extension):
        return False
    return not any(
        len(sibling) > len(extension) and name.endswith(sibling)
        for sibling in sibling_extensions
    )


def extensions_by_folder(
    type_roots: Sequence[tuple[str, Path, str]],
) -> dict[Path, set[str]]:
    """Every extension configured onto each folder, so `owns_file` has its siblings.

    The resolved-tree counterpart of `extensions_for_folder`: `export_db` walks
    real directories and holds `(type, Path, extension)` triples, while the
    config-driven readers hold the layout mapping. Two collectors, one rule.
    """
    by_folder: dict[Path, set[str]] = {}
    for _object_type, folder, extension in type_roots:
        by_folder.setdefault(folder, set()).add(extension)
    return by_folder
