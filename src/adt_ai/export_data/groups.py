"""`export_data -groups`: the same prefix-routed `<group>/` layout `export_db` has (ADT #520).

Jan, 2026-08-24: *"ADT export_data should also support GROUPS which we have in
export_db."* `export_db/groups.py` and `export_db/group_moves.py` already answer
this question generically, over `(object_type, folder, extension)` tuples and
plain object names, never over a `DatabaseObject`: `export_data` has exactly one
object type, `DATA`, one folder, and one extension, so it is a caller of that
machinery rather than a second implementation of it. `GroupRules`, `group_for`,
`resolve_group_inputs`, `detect_groups_from_tree`, `parse_group_prefixes`,
`build_prefix_rules` and `execute_group_move` are re-exported here unchanged.

**The one thing export_db's mover does not know about is the sidecar folder.**
A table with a BLOB/CLOB/JSON/XMLTYPE column writes its values beside the CSV in
a same-stem directory (`customers.csv` + `customers/`), and `plan_group_moves`
only ever relocates a file and its `.fix<ext>` twin, a shape `export_data` does
not have and sidecar folders are not. Moving the CSV alone would strand every
BLOB a grouped table ever wrote. `plan_data_group_moves` below is the one place
that difference lives; `execute_group_move` still does the printing and the
`-force` apply unchanged, because a directory `Path.rename()`s exactly like a
file and the `GroupMove` shape already carries an arbitrary `(source, dest)`.
"""

from __future__ import annotations

from pathlib import Path

from adt_ai.export_db.group_moves import (
    GroupCollision,
    GroupMove,
    GroupMovePlan,
    apply_group_moves,
    build_prefix_rules,
    execute_group_move,
    parse_group_prefixes,
)
from adt_ai.export_db.groups import (
    GroupRules,
    detect_groups_by_prefix,
    detect_groups_from_tree,
    group_for,
    resolve_group_inputs,
)

__all__ = [
    "GroupCollision",
    "GroupMove",
    "GroupMovePlan",
    "GroupRules",
    "apply_group_moves",
    "build_prefix_rules",
    "detect_groups_by_prefix",
    "detect_groups_from_tree",
    "execute_group_move",
    "group_for",
    "parse_group_prefixes",
    "plan_data_group_moves",
    "resolve_data_group_rules",
    "resolve_group_inputs",
]


def resolve_data_group_rules(data_folder: Path, seed: GroupRules | None) -> GroupRules:
    """Seed rules merged with whatever `data_folder` already learned from its own tree.

    Same split `export_db.runner._resolve_group_rules` makes: an explicit or
    persisted rule always wins, and a table already sitting in a group folder
    (arranged by a prior `-groups -force`, or by hand) re-derives that same
    rule from the folder it is already in, which is what makes a later export
    keep writing it there rather than starting a second copy flat.
    """
    type_roots = [("DATA", data_folder, ".csv")]
    return (seed or GroupRules.empty()).merged(detect_groups_from_tree(type_roots))


def plan_data_group_moves(data_folder: Path, rules: GroupRules | None) -> GroupMovePlan:
    """Plan how flat table exports relocate into `data/<GROUP>/` subfolders.

    Same shape as `export_db.group_moves.plan_group_moves` (one `GroupMove` per
    relocation, unmatched tables left where they are, a name collision aborts
    the run), narrowed to DATA's own file layout, anchored on the CSV rather
    than on `object_types.DATA`'s configured extension: every table export
    writes one, while the merge `.sql` companion is conditional (only a table
    with a primary key gets one) and cannot be the anchor without silently
    excluding every table that lacks it. Every table's export moves as a unit:
    the CSV, its `.sql` merge script when the export wrote one, and its
    same-stem sidecar directory when the table carries a BLOB/CLOB/JSON/XMLTYPE
    column. Table names are already unique by construction (one CSV per table),
    so `collisions` is carried for shape parity with `execute_group_move` and
    never actually populates.
    """
    data_folder = Path(data_folder)
    moves: list[GroupMove] = []
    unmatched: list[tuple[str, Path]] = []
    if not data_folder.is_dir():
        return GroupMovePlan()

    for file_path in sorted(data_folder.glob("*.csv")):
        if not file_path.is_file():
            continue
        name = file_path.stem.upper()
        group = group_for("DATA", name, rules)
        if not group:
            unmatched.append(("DATA", file_path))
            continue
        dest_dir = data_folder / group.upper()
        moves.append(GroupMove("DATA", file_path, dest_dir / file_path.name))
        merge_sql = file_path.with_suffix(".sql")
        if merge_sql.is_file():
            moves.append(GroupMove("DATA", merge_sql, dest_dir / merge_sql.name))
        sidecar_dir = file_path.with_suffix("")
        if sidecar_dir.is_dir():
            moves.append(GroupMove("DATA", sidecar_dir, dest_dir / sidecar_dir.name))

    return GroupMovePlan(moves=moves, unmatched=unmatched)
