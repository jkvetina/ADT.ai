from __future__ import annotations

import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from adt_ai.shared.progress import print_adt_header
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping


def prefix_words(name: str, max_words: int = 2) -> str:
    """Return the leading `max_words` underscore-delimited words of an object name.

    `INV_BILLING_HEADER` yields `INV_BILLING` (two words) or `INV` (one word).
    Names with fewer words than requested are returned whole.
    """
    words = [word for word in name.upper().split("_") if word]
    return "_".join(words[:max_words])


def _name_words(name: str) -> list[str]:
    return [word for word in name.upper().split("_") if word]


def _prefix_matches(words: list[str], prefix: str) -> bool:
    prefix_parts = _name_words(prefix)
    if not prefix_parts:
        return False
    return words[: len(prefix_parts)] == prefix_parts


@dataclass(frozen=True)
class GroupRules:
    """Prefix→group routing rules, split by global and per-object-type scope."""

    global_rules: dict[str, str] = field(default_factory=dict)
    type_rules: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> GroupRules:
        return cls()

    @classmethod
    def from_mapping(cls, mapping: dict[str, str] | None) -> GroupRules:
        """Build rules from a flat `config/groups.yaml` mapping.

        Keys are either `PREFIX` (applies to every object type) or `TYPE/PREFIX`
        (applies only to that object type). Object-type tokens are upper-cased;
        prefixes are upper-cased so matching is case-insensitive.
        """
        global_rules: dict[str, str] = {}
        type_rules: dict[str, dict[str, str]] = {}
        for raw_key, raw_group in (mapping or {}).items():
            key = str(raw_key).strip()
            group = str(raw_group).strip()
            if not key or not group:
                print(
                    f"Warning: groups.yaml: ignoring entry {raw_key!r}: {raw_group!r}, "
                    "empty key or group value",
                    file=sys.stderr,
                )
                continue
            if "/" in key:
                object_type, _, prefix = key.partition("/")
                object_type = object_type.strip().upper()
                prefix = prefix.strip().upper()
                if object_type and prefix:
                    type_rules.setdefault(object_type, {})[prefix] = group
            else:
                global_rules[key.upper()] = group
        return cls(global_rules=global_rules, type_rules=type_rules)

    def to_mapping(self) -> dict[str, str]:
        """Inverse of `from_mapping`, for persisting back to `config/groups.yaml`."""
        mapping: dict[str, str] = {}
        for prefix, group in sorted(self.global_rules.items()):
            mapping[prefix] = group
        for object_type, prefixes in sorted(self.type_rules.items()):
            for prefix, group in sorted(prefixes.items()):
                mapping[f"{object_type}/{prefix}"] = group
        return mapping

    @property
    def is_empty(self) -> bool:
        return not self.global_rules and not self.type_rules

    def merged(self, other: GroupRules) -> GroupRules:
        """Overlay `other` on top of these rules; `other` wins on conflicts."""
        global_rules = {**self.global_rules, **other.global_rules}
        type_rules: dict[str, dict[str, str]] = {
            object_type: dict(prefixes) for object_type, prefixes in self.type_rules.items()
        }
        for object_type, prefixes in other.type_rules.items():
            type_rules.setdefault(object_type, {}).update(prefixes)
        return GroupRules(global_rules=global_rules, type_rules=type_rules)


def group_for(object_type: str, name: str, rules: GroupRules | None) -> str | None:
    """Return the target group for an object, or None when no prefix matches.

    Type-specific rules beat global rules; among rules of the same scope the
    longest matching prefix wins.
    """
    if rules is None or rules.is_empty:
        return None
    words = _name_words(name)
    if not words:
        return None

    # (scope_rank, prefix_word_count, group), higher sorts later, last wins.
    candidates: list[tuple[int, int, str]] = []
    for prefix, group in rules.type_rules.get(object_type.upper(), {}).items():
        if _prefix_matches(words, prefix):
            candidates.append((1, len(_name_words(prefix)), group))
    for prefix, group in rules.global_rules.items():
        if _prefix_matches(words, prefix):
            candidates.append((0, len(_name_words(prefix)), group))
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: (candidate[0], candidate[1]))
    return candidates[-1][2]


def detect_groups_by_prefix(
    names: Iterable[str],
    min_count: int,
    max_words: int = 2,
) -> dict[str, str]:
    """Cluster flat object names by their leading prefix.

    Names are first grouped by their `max_words`-word prefix; any cluster with at
    least `min_count` members becomes a group named after the prefix. Names left in
    undersized clusters fall back to progressively shorter prefixes (down to one
    word). Returns a `{prefix: group}` mapping where the group equals the prefix.
    """
    rules: dict[str, str] = {}
    remaining = [name for name in names if _name_words(name)]
    for words in range(max_words, 0, -1):
        clusters: dict[str, list[str]] = {}
        for name in remaining:
            clusters.setdefault(prefix_words(name, words), []).append(name)
        leftover: list[str] = []
        for prefix, members in clusters.items():
            if prefix and prefix not in rules and len(members) >= min_count:
                rules[prefix] = prefix
            else:
                leftover.extend(members)
        remaining = leftover
    return rules


def _common_prefix(names: Iterable[str], max_words: int = 2) -> str:
    """Longest leading word-prefix (capped at `max_words`) shared by every name."""
    word_lists = [_name_words(name) for name in names]
    word_lists = [words for words in word_lists if words]
    if not word_lists:
        return ""
    shared: list[str] = []
    for index in range(max_words):
        column = {words[index] for words in word_lists if index < len(words)}
        if len(column) != 1 or any(index >= len(words) for words in word_lists):
            break
        shared.append(next(iter(column)))
    return "_".join(shared)


def detect_groups_from_tree(
    type_roots: Iterable[tuple[str, Path, str]],
    max_words: int = 2,
) -> GroupRules:
    """Learn prefix→group rules from how files are manually arranged on disk.

    `type_roots` is an iterable of `(object_type, type_folder, extension)`. Each
    immediate sub-folder of a type folder is treated as a group whose name is the
    folder name; the shared prefix of the object files inside it becomes the rule.
    """
    type_rules: dict[str, dict[str, str]] = {}
    for object_type, folder, extension in type_roots:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for subfolder in sorted(folder.iterdir()):
            if not subfolder.is_dir():
                continue
            names = [
                object_name_from_file(file_path, extension)
                for file_path in sorted(subfolder.rglob(f"*{extension}"))
                if file_path.is_file()
            ]
            prefix = _common_prefix(names, max_words)
            if prefix:
                type_rules.setdefault(object_type.upper(), {})[prefix] = subfolder.name
    return GroupRules(type_rules=type_rules)


def object_name_from_file(file_path: Path, extension: str) -> str:
    """Uppercased object name from an exported file: the filename minus its extension."""
    name = file_path.name
    if name.endswith(extension):
        name = name[: -len(extension)]
    return name.upper()


def load_groups_file(path: Path | str) -> GroupRules:
    """Load persisted `config/groups.yaml` rules, or empty rules when absent."""
    return GroupRules.from_mapping(load_yaml_mapping(Path(path)))


def save_groups_file(path: Path | str, rules: GroupRules) -> None:
    """Persist rules as a flat `config/groups.yaml` mapping."""
    store_yaml_mapping(Path(path), rules.to_mapping())


def resolve_group_inputs(
    config_search_paths: Iterable[Path | str],
) -> GroupRules:
    """Merge persisted `config/groups.yaml` rules across every config search path.

    A project-local `config/groups.yaml` overrides shipped defaults because later
    search paths win. These rules seed the export so DDL is routed into the
    `<type>/<group>/` subfolders the move action created.
    """
    rules = GroupRules.empty()
    for directory in config_search_paths:
        rules = rules.merged(load_groups_file(Path(directory) / "groups.yaml"))
    return rules


# --- -groups move action -----------------------------------------------------


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


def build_prefix_rules(prefixes: Iterable[str]) -> GroupRules:
    """Build global rules where each prefix routes into a group named after itself.

    Group folder names are always upper-cased, matching the move action's
    contract that explicit prefixes become uppercase `<type>/<GROUP>/` folders.
    """
    global_rules: dict[str, str] = {}
    for prefix in prefixes:
        key = str(prefix).strip().upper()
        if key:
            global_rules[key] = key
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

    for object_type, folder, extension in type_roots:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        final_by_object: dict[str, list[Path]] = {}

        # Every object file already anywhere in the subtree starts at its own path.
        for file_path in sorted(folder.rglob(f"*{extension}")):
            if not file_path.is_file() or _is_fix_file(file_path, extension):
                continue
            name = object_name_from_file(file_path, extension)
            final_by_object.setdefault(name, []).append(file_path)

        # Route the flat (directly-in-folder) object files by prefix.
        for file_path in sorted(folder.glob(f"*{extension}")):
            if not file_path.is_file() or _is_fix_file(file_path, extension):
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


def _emit_planned_moves(moves: Iterable[GroupMove], emit: Callable[[str], None]) -> None:
    """Print the plan as the grouping it is: a group line, then its files under it.

    A group gathers files across object types, so `INV_BILLING` owns rows from
    `tables/` and `views/` alike. Groups sort A to Z and so do the files inside
    each one, because the reader is looking a name up rather than reading a log.
    """
    by_group: dict[str, list[str]] = {}
    for move in moves:
        by_group.setdefault(move.dest.parent.name, []).append(_file_label(move.source))
    for group in sorted(by_group):
        # An empty line above EVERY group, the first one included (ADT #409), so
        # the eye lands on the names rather than counting indents to find them.
        emit("")
        emit(f"  {group}")
        for label in sorted(by_group[group]):
            emit(f"    - {label}")


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
            for label in leftovers:
                emit(f"  - {label}")
        return 0

    print_adt_header("PLANNED MOVES:")
    _emit_planned_moves(plan.moves, emit)
    if show_unmatched and leftovers:
        print_adt_header("UNMATCHED (LEFT IN PLACE):")
        for label in leftovers:
            emit(f"  - {label}")

    if not force:
        return 0

    apply_group_moves(plan)
    # The result of the action, not a fourth file under the last group: flush
    # against the rows above it, it read as one.
    emit("")
    emit(f"Moved {len(plan.moves)} file(s).")
    return 0
