"""The `export_db -groups` move action: plan the relocations, print them, apply them.

Split out of `groups.py` by ADT #412, which pushed that file past the 20 KB context
guard. The seam is the one the file already carried as a comment: `groups.py` answers
where an object belongs (prefix rules, folder ownership, what a filename means) and is
read by the export itself, while everything here exists only for the one command that
rearranges files on disk.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from adt_ai.export_db.groups import (
    GroupRules,
    extensions_by_folder,
    group_for,
    object_name_from_file,
    owns_file,
)
from adt_ai.shared.file_list import file_rows
from adt_ai.shared.progress import print_adt_header


def parse_group_prefixes(tokens: Iterable[str] | None) -> list[str]:
    """Normalize raw `-groups ABC DEF,GH` tokens into clean uppercased prefixes.

    Tokens may be space- and/or comma-separated; surrounding whitespace is
    stripped, every prefix is upper-cased, and duplicates are dropped while the
    first-seen order is preserved.
    """
    prefixes: list[str] = []
    seen: set[str] = set()
    for token in tokens or []:
        for part in str(token).replace(",", " ").split():
            prefix = part.strip().upper()
            if prefix and prefix not in seen:
                seen.add(prefix)
                prefixes.append(prefix)
    return prefixes


def build_prefix_rules(prefixes: Iterable[str], group: str | None = None) -> GroupRules:
    """Build global rules routing each prefix into a group folder.

    Each prefix names its own group by default, which is one folder per prefix
    per object type. `group` overrides that for every prefix at once, so
    `-groups ICT_VPD ICT_ABC -force VPD` lands both in `<type>/VPD/` (ADT #416).

    Group folder names are always upper-cased, matching the move action's
    contract that explicit prefixes become uppercase `<type>/<GROUP>/` folders.
    """
    folder = str(group).strip().upper() if group else ""
    global_rules: dict[str, str] = {}
    for prefix in prefixes:
        key = str(prefix).strip().upper()
        if key:
            global_rules[key] = folder or key
    return GroupRules(global_rules=global_rules)


@dataclass(frozen=True)
class GroupMove:
    """A single planned file relocation from a flat type folder into a group."""

    object_type: str
    source: Path
    dest: Path


@dataclass(frozen=True)
class GroupCollision:
    """Two or more object files that would share a name within one type subtree."""

    object_type: str
    name: str
    paths: list[Path]


@dataclass(frozen=True)
class GroupMovePlan:
    """The full preview of a `-groups` move: relocations, leftovers, and clashes."""

    moves: list[GroupMove] = field(default_factory=list)
    unmatched: list[tuple[str, Path]] = field(default_factory=list)
    collisions: list[GroupCollision] = field(default_factory=list)


def _is_fix_file(file_path: Path, extension: str) -> bool:
    return file_path.name.endswith(f".fix{extension}")


def plan_group_moves(
    type_roots: Iterable[tuple[str, Path, str]],
    rules: GroupRules | None,
) -> GroupMovePlan:
    """Plan how flat object files relocate into `<type>/<GROUP>/` subfolders.

    For each `(object_type, type_folder, extension)`, files sitting directly in the
    type folder are routed by prefix: a matching object moves into an uppercased
    group subfolder (its `.fix` sidecar travels with it), and an unmatched object is
    left where it is. After planning, the object name (file basename) per object
    type must be unique across the whole subtree, root and every group folder. Any
    duplicate is recorded as a collision so the caller can abort instead of
    overwriting.
    """
    moves: list[GroupMove] = []
    unmatched: list[tuple[str, Path]] = []
    collisions: list[GroupCollision] = []

    roots = [
        (object_type, Path(folder), extension)
        for object_type, folder, extension in type_roots
    ]
    extensions = extensions_by_folder(roots)

    for object_type, folder, extension in roots:
        if not folder.is_dir():
            continue
        # A folder two object types share hands each file to one of them only, so a
        # file cannot be planned once per type and renamed twice (ADT #412).
        siblings = extensions[folder] - {extension}
        final_by_object: dict[str, list[Path]] = {}

        # Every object file already anywhere in the subtree starts at its own path.
        for file_path in sorted(folder.rglob(f"*{extension}")):
            if not file_path.is_file() or _is_fix_file(file_path, extension):
                continue
            if not owns_file(extension, siblings, file_path):
                continue
            name = object_name_from_file(file_path, extension)
            final_by_object.setdefault(name, []).append(file_path)

        # Route the flat (directly-in-folder) object files by prefix.
        for file_path in sorted(folder.glob(f"*{extension}")):
            if not file_path.is_file() or _is_fix_file(file_path, extension):
                continue
            if not owns_file(extension, siblings, file_path):
                continue
            name = object_name_from_file(file_path, extension)
            group = group_for(object_type, name, rules)
            if not group:
                unmatched.append((object_type.upper(), file_path))
                continue
            dest_dir = folder / group.upper()
            moves.append(GroupMove(object_type.upper(), file_path, dest_dir / file_path.name))
            # The object's final location is now the group folder, not the root.
            final_by_object[name] = [
                dest_dir / file_path.name if path == file_path else path
                for path in final_by_object.get(name, [file_path])
            ]
            sidecar = file_path.with_name(f"{file_path.stem}.fix{extension}")
            if sidecar.is_file():
                moves.append(
                    GroupMove(object_type.upper(), sidecar, dest_dir / sidecar.name)
                )

        for name, paths in final_by_object.items():
            if len(paths) > 1:
                collisions.append(GroupCollision(object_type.upper(), name, sorted(paths)))

    return GroupMovePlan(moves=moves, unmatched=unmatched, collisions=collisions)


def apply_group_moves(plan: GroupMovePlan) -> None:
    """Execute the planned moves, creating group folders as needed."""
    for move in plan.moves:
        move.dest.parent.mkdir(parents=True, exist_ok=True)
        move.source.rename(move.dest)


def _file_label(path: Path) -> str:
    """`tables/inv_billing_header.sql`, the file's own folder and its name.

    Every row of a preview shares the same project path, so printing it in full
    spends the width a reader needs on the two fields that differ. The parent
    folder survives the trim because it names the object type the file holds.
    """
    return f"{path.parent.name}/{path.name}"


def _emit_labels(labels: Iterable[str], emit: Callable[[str], None]) -> None:
    """A flat `  - <type>/<name>.sql` list, through the shared renderer (ADT #504).

    Not grouped: `_file_label` already trimmed the path to its type folder and
    filename, which is the grouping a reader of a move preview wants, and
    re-grouping the trimmed form would print half a path as a folder.
    """
    for line in file_rows(labels, nested=False):
        emit(line)


def _emit_planned_moves(moves: Iterable[GroupMove], emit: Callable[[str], None]) -> None:
    """Print the plan as the grouping it is: a group line, then its files under it.

    A group gathers files across object types, so `INV_BILLING` owns rows from
    `tables/` and `views/` alike. Groups sort A to Z and so do the files inside
    each one, because the reader is looking a name up rather than reading a log.

    The rows come from the shared renderer since ADT #504, so this block and the
    twelve `patch` sections share one indent rule rather than agreeing by hand.
    Its grouping key is the GROUP, which no path carries, so the folder line is
    written here and the files go through `file_rows` at the depth below it.
    """
    by_group: dict[str, list[str]] = {}
    for move in moves:
        by_group.setdefault(move.dest.parent.name, []).append(_file_label(move.source))
    for group in sorted(by_group):
        # An empty line above EVERY group, the first one included (ADT #409), so
        # the eye lands on the names rather than counting indents to find them.
        emit("")
        emit(f"  {group}")
        for line in file_rows(sorted(by_group[group]), nested=False, depth=2):
            emit(line)


def execute_group_move(
    plan: GroupMovePlan,
    *,
    emit: Callable[[str], None] = print,
    show_unmatched: bool = True,
    force: bool = False,
) -> int:
    """Print the plan, and move the files when `force` says to.

    Returns an exit code: `2` when a uniqueness collision aborts the run (nothing
    moves), `0` otherwise, the empty case and every preview included.

    **The plan is a report, not a question** (ADT #409). It used to print and then
    ask, and a reader who wanted to see the layout before committing to it had to
    answer no to a prompt, which is a worse spelling of the thing `#406` removed
    when it dropped `-dry-run`. Applying is now an explicit action flag, the shape
    §Command surface prefers over a command that previews by default and then
    negotiates.

    `show_unmatched` is off when the caller named the groups it wants. Bare
    `-groups` proposes a whole layout, so what it declined to move is part of the
    proposal; `-groups INV_BILLING` asks about INV_BILLING, and answering it with
    every other file in the export is the noise ADT #408 was filed on.

    Naming groups also bounds what `force` can touch, and it does so through the
    plan rather than through a second check here: `plan_group_moves` only ever
    routes files sitting directly in a type folder whose name a rule matches, so a
    group somebody arranged by hand is not in `plan.moves` and cannot be moved,
    flattened or renamed by a run that did not name it.
    """
    if plan.collisions:
        emit("Aborting: duplicate object names would collide (file name must be "
             "unique per object type):")
        for collision in plan.collisions:
            joined = ", ".join(str(path) for path in collision.paths)
            emit(f"  {collision.object_type} {collision.name}: {joined}")
        return 2

    leftovers = sorted(_file_label(source) for _object_type, source in plan.unmatched)

    if not plan.moves:
        emit("Nothing to move: no files matched a group.")
        if show_unmatched:
            _emit_labels(leftovers, emit)
        return 0

    print_adt_header("PLANNED MOVES:")
    _emit_planned_moves(plan.moves, emit)
    if show_unmatched and leftovers:
        print_adt_header("UNMATCHED (LEFT IN PLACE):")
        _emit_labels(leftovers, emit)

    if not force:
        return 0

    apply_group_moves(plan)
    # The result of the action, not a fourth file under the last group: flush
    # against the rows above it, it read as one.
    emit("")
    emit(f"Moved {len(plan.moves)} file(s).")
    return 0
