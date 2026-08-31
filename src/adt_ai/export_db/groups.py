from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

# `owns_file` and `object_name_from_file` were written here for ADT #412 and
# moved to `shared/` for ADT #471, where `patch` and `search_repo` can reach them
# too. Re-exported so this module's own callers are untouched.
from adt_ai.shared.object_files import extensions_by_folder as extensions_by_folder
from adt_ai.shared.object_files import object_name_from_file as object_name_from_file
from adt_ai.shared.object_files import owns_file as owns_file
from adt_ai.shared.safe_paths import simple_component
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
    return simple_component(candidates[-1][2], role="group name")


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


def resolve_group_rules(request, resolver) -> GroupRules:
    """Seed with explicit/persisted rules, then learn from the tree on disk.

    Both halves it joins already live here, so it moved off `ExportDbRunner`
    (ADT `#605`) when that module reached its context cap; the runner keeps a
    thin method delegating to this, which is what its callers still hold.
    """
    seed = request.group_rules or GroupRules.empty()
    type_roots = resolver.iter_type_roots(request.schemas)
    return seed.merged(detect_groups_from_tree(type_roots))


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
    roots = [
        (object_type, Path(folder), extension)
        for object_type, folder, extension in type_roots
    ]
    extensions = extensions_by_folder(roots)
    for object_type, folder, extension in roots:
        if not folder.is_dir():
            continue
        # A folder two object types share reads each file under one of them only,
        # or a `.spec.sql` name joins the `.sql` cluster and blocks its prefix.
        siblings = extensions[folder] - {extension}
        for subfolder in sorted(folder.iterdir()):
            if not subfolder.is_dir():
                continue
            names = [
                object_name_from_file(file_path, extension)
                for file_path in sorted(subfolder.rglob(f"*{extension}"))
                if file_path.is_file() and owns_file(extension, siblings, file_path)
            ]
            prefix = _common_prefix(names, max_words)
            if prefix:
                type_rules.setdefault(object_type.upper(), {})[prefix] = subfolder.name
    return GroupRules(type_rules=type_rules)


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
