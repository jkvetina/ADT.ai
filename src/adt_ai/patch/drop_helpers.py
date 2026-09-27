"""The DROP helper a patch writes for an object its window deleted.

Split out of `patch/helpers.py` (ADT #983), which is re-exporting both names
for the importers that already read them there. The rules live on
`_write_drop_helpers`; this module owns nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch import queries
from adt_ai.patch.generated_helpers import drop_helper_filename, drop_helper_slot
from adt_ai.patch.object_identity import _object_exported_elsewhere, _object_identity
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.sql_identifiers import safe_identifier, safe_object_type


def _write_drop_helpers(
    root: Path,
    script_root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    baseline: Mapping[str, str] | None = None,
) -> list[str]:
    """Write a DROP script per object THIS patch window deletes.

    The window, not the filesystem, decides (ADT #290). `(root / file).exists()`
    alone answers "gone now", which is a different question: a file some LATER
    commit deleted, outside the selection, is equally absent and used to earn a
    helper this patch never made, shipping the object's content and a script
    dropping it in the same folder. Old ADT read the diff between the baseline
    commit and the window's last commit instead (patch.py:1333) and never had
    that failure.

    Deliberate divergence from old ADT, decided on ADT.ai's merits: an object the
    window BOTH created and deleted earns no helper either. The database this
    patch deploys onto sits at the pre-window state and has no such object, so
    the DROP is a no-op at best and hits an unrelated same-named object at worst.
    Old ADT emitted it, its `first_commit <= self.first_commit_id` gate
    (patch.py:1338-1341) reads like it covers this and does not: `relevant_comms`
    only holds ids from `relevant_commits` and `first_commit_id` only ever moves
    down from `min(relevant_commits) - 1`, so the comparison is false in every
    window that does not open at the edge of known history.

    **The unit is the OBJECT, never the file path (ADT #498).** A `DROP` names an
    object, so every question here has to be asked about the object: an
    `export_db -groups` move is a delete at one path plus an add at another, and
    both path-keyed tests above pass it through. That shipped a patch installing
    63 relocated objects and then dropping every one of them, `DROP TABLE` on two
    live tables included. So the created-by-window test compares identities, and
    the disk test asks whether the object is still exported ANYWHERE under its
    type folder rather than whether its old filename is still there.

    **Existence at the window's two ends decides, never "any addition" (ADT
    #983).** Suppressing the DROP for any object with an `A` somewhere in the
    window lost it for one the target already held: deleted, re-added and
    deleted again, the middle `A` read as "this window created it", and the
    object stayed on the target for good. `_window_ends` reads the object's
    first and last in-window commits instead. A hash-built patch has the better
    answer for the start: ``baseline`` is what the target is believed to hold,
    so a deletion it records existed, whatever history the scan happened to
    reach.

    A DATA script is rows, and a GRANT is granted or not; neither is an object
    a `DROP` names, so neither earns a helper (`DROP DATA <TABLE>` is not a
    statement Oracle has, audit V5).
    """
    deleted_by_window = {file for record in records for file in record.deleted_files}
    ends = _window_ends(records, config)
    on_disk: dict[tuple[str, ...], set[tuple[str, str, str]]] = {}
    written: list[str] = []
    for file in files:
        if file not in deleted_by_window:
            continue
        # `_object_identity` resolves through the configured `path_objects`
        # layout, so a path outside it answers None here. Requiring the first
        # part to be literally `database` on top of that was a condition old
        # ADT's drop loop never had (patch.py:1327-1352) and one the SHIPPED
        # default `<schema>/database/<object_type>/` can never satisfy, part 0 is
        # the schema, so every project on the default layout silently got no DROP
        # helper (ADT #287). Same hardcoded assumption ADT #196 lifted out of
        # `layout.py`, surviving at the one call site that sweep missed.
        #
        # It reads the name through the type's own extension, never `Path.stem`,
        # which strips one suffix and turned `core.spec.sql` into the `CORE.SPEC`
        # that `safe_identifier` refuses (ADT #471). This loop spelled the tuple
        # out a second time beside the import that already builds it (ADT #554).
        identity = _object_identity(file, config)
        if identity is None:
            continue
        _schema, object_type, object_name = identity
        if object_type in _NOT_DROPPABLE:
            continue
        existed_at_start, exists_at_end = ends.get(identity, (True, False))
        if baseline is not None:
            existed_at_start = file in baseline
        if not existed_at_start or exists_at_end:
            continue
        if _object_exported_elsewhere(root, file, identity, config, on_disk):
            continue
        folder = script_root / drop_helper_slot(config)
        folder.mkdir(parents=True, exist_ok=True)
        helper = folder / drop_helper_filename(object_type, object_name)
        text_files.write_text(helper, _drop_helper_sql(object_type, object_name))
        written.append(helper.relative_to(root).as_posix())
    return written

#: Deleted files a DROP helper never answers: rows and privileges, not objects.
_NOT_DROPPABLE = frozenset({"DATA", "GRANT"})


def _window_ends(
    records: list[CommitRecord], config: dict[str, Any]
) -> dict[tuple[str, str, str], tuple[bool, bool]]:
    """Per object: did it exist when the window opened, and when it closed?

    Read off the object's first and last commits inside the window, in the
    order the records arrive (oldest first). A commit that only ADDS the object
    opens the window without it; any other status there (`M`, `D`, or the `D`
    half of an `-groups` move beside its `A`) means the target already had it.
    Symmetrically, a last commit that only DELETES it closes the window
    without it. A path with no recorded status counts as a modification, which
    is what the pre-status store could say about a file it listed.
    """
    touched: dict[tuple[str, str, str], list[set[str]]] = {}
    for record in records:
        statuses = {file: "M" for file in record.files}
        statuses.update({file: "D" for file in record.deleted_files})
        statuses.update(record.file_statuses)
        per_commit: dict[tuple[str, str, str], set[str]] = {}
        for file, status in statuses.items():
            identity = _object_identity(file, config)
            if identity is not None:
                per_commit.setdefault(identity, set()).add(status)
        for identity, seen in per_commit.items():
            touched.setdefault(identity, []).append(seen)
    return {
        identity: (commits[0] != {"A"}, commits[-1] != {"D"})
        for identity, commits in touched.items()
    }


def _drop_helper_sql(object_type: str, object_name: str) -> str:
    safe_object_type(object_type, role="object type")
    safe_identifier(object_name, role="object name")
    return queries.DROP_HELPER_TEMPLATE.format(
        object_type = object_type,
        object_name = object_name,
        statement   = f"DROP {object_type} {object_name}",
    )


__all__ = ["_drop_helper_sql", "_write_drop_helpers"]
