"""What a repo path IS, database-object-wise, and whether it still exists.

Split out of `helpers.py` when it crossed the 20 KB context guard (ADT #499),
the same guard and the same reason `table_alter.py` split out of it earlier
(ADT #494). The seam is the question this file answers, what object a path
holds and whether that object is still exported anywhere, as opposed to what
`helpers.py` does with the answer (suppress a DROP, in `_write_drop_helpers`)
or what the callers in `create.py`/`summary.py`/`report.py` do with it (skip a
`[DELETED]` line). `helpers.py` re-exports these three for existing importers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adt_ai.patch.layout import (
    database_object_name as _database_object_name,
)
from adt_ai.patch.layout import (
    database_object_type as _database_object_type,
)
from adt_ai.patch.layout import (
    database_schema as _database_schema,
)
from adt_ai.patch.layout import object_layouts, resolved_object_head


def _object_identity(path: str, config: dict[str, Any]) -> tuple[str, str, str] | None:
    """The object a repo path holds, as `(SCHEMA, TYPE, NAME)`, or None for no object.

    The key a `DROP` is actually about, and the one thing a group move does not
    change: `packages/core_lock.sql` and `packages/CORE/core_lock.sql` are the
    same row of `user_objects`. Upper-cased on both ends because that is the form
    the dictionary stores and the form a comparison has to survive an export
    writing `App_Owner` in one place and `app_owner` in another.
    """
    object_type = _database_object_type(path, config)
    if object_type is None:
        return None
    object_name = _database_object_name(path, config)
    # defensive: `object_type` resolved non-None off the same layout, so
    # `_database_object_name` reads through that type's own extension and cannot
    # itself come back empty (the same pairing `_write_drop_helpers` relies on)
    if not object_name:  # pragma: no cover
        return None
    return (_database_schema(path, config).upper(), object_type.upper(), object_name)


def _object_exported_elsewhere(
    root: Path,
    path: str,
    identity: tuple[str, str, str],
    config: dict[str, Any],
    cache: dict[tuple[str, ...], set[tuple[str, str, str]]],
    *,
    excluded_path: str | None = None,
) -> bool:
    """Is this object still exported somewhere under its own type folder?

    `(root / path).exists()` answered the narrower question, is this FILENAME
    still there, which a group move makes the wrong one: the file moved and the
    object never left. So the type folder is read instead, flat level plus one
    group level, which is exactly the arrangement `-groups` can produce, and each
    file found there is resolved to its own identity. Reading identities rather
    than rebuilding the expected filename is what keeps the PACKAGE/PACKAGE BODY
    pair honest and survives whatever case the export wrote.

    Scanned once per type folder per run: a patch deleting 63 files off four
    folders reads four directories, not 63.
    """
    head = resolved_object_head(path, config)
    if head is None:  # pragma: no cover - `_database_object_type` already resolved a head
        return False
    layout = object_layouts(config.get("object_types", {})).get(identity[1])
    if layout is None:  # pragma: no cover - the type came from this same layout mapping
        return False
    type_root = root.joinpath(*head, *Path(layout[0].strip("/")).parts)
    key = (*head, layout[0], excluded_path or "")
    if key not in cache:
        found: set[tuple[str, str, str]] = set()
        if type_root.is_dir():
            for candidate in (*type_root.glob("*"), *type_root.glob("*/*")):
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(root).as_posix()
                if relative == excluded_path:
                    continue
                found_identity = _object_identity(relative, config)
                if found_identity is not None:
                    found.add(found_identity)
        cache[key] = found
    return identity in cache[key]


def _path_is_deleted(
    root: Path,
    path: str,
    config: dict[str, Any],
    cache: dict[tuple[str, ...], set[tuple[str, str, str]]],
    *,
    present: bool | None = None,
) -> bool:
    """Whether `path` names a deletion, asked of the OBJECT rather than the file.

    Every `-- DELETED FILES:` / `[DELETED]` listing answered `not (root /
    path).exists()` until ADT #499, the same file-path question
    `_write_drop_helpers` answered before #498, and wrong for the same reason: a
    group move is a delete at one path plus an add at another, so the old flat
    path is genuinely gone while the object is not. A path that resolves to a
    recognized database object defers to `_object_exported_elsewhere`; anything
    else (a grant script, a template, a path outside `path_objects`) keeps the
    plain existence check, the only question that made sense for it.

    Build-time callers pass the selected source's presence. A carried Git blob
    is live even when missing locally; a local restoration cannot turn a
    selected deletion into a carried file. Other exported paths still suppress
    the deletion for a grouped move.
    """
    if present:
        return False
    identity = _object_identity(path, config)
    if identity is None:
        return present is not None or not (root / path).exists()
    return not _object_exported_elsewhere(
        root, path, identity, config, cache,
        excluded_path=path if present is not None and (root / path).is_file() else None,
    )
