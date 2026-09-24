"""Which pages and components of a CHANGED application differ, and how (ADT #892).

The first cut of `diff -apex` stopped at the application: its `CHECKSUM-SH256`
differed, so it printed `APEX APPLICATION 122 DIGITAL-APPROVAL CHANGED` and
nothing else. Jan ran it on DEV against TEST and rejected it: *-verbose says
only that the app changed, not WHICH page, WHICH components, WHAT the change
is.* The fingerprint stays the gate, since an application whose fingerprints
match needs no export; this module is what a changed one is drilled into.

**The format is the best one both instances export** (Jan, 2026-09-19: *"if you
dont have -apexlang, you should use -readable, if you dont have readable, you
should use -split. And you should ignore the component id and apex version!"*).
APEXlang (26.1+) carries no ids at all; READABLE_YAML (before 26.1) carries them
as `id:` keys; split SQL carries them in every `wwv_flow_imp.id(...)`. Measured
on app 122, APEX 24.2, DEV against TEST: the readable export differs only where
the application does, plus a subscription's `version-number`, `referenced-id`
and master `app`, and trailing newlines inside list values. The split export
adds default values one side writes and the other omits, which is why it is the
last resort. Every one of those is dropped here, so a component that differs
only in them reads as equal.

Each format is flattened into the same shape: a component, which is a page or
one shared component, holding one value per property. A property inside a
region, button or process is named by that sub-component first, so a row can
say `Check Hydro: execution.sequence` rather than a path nobody can place.

**A component carries its kind and its name as fields** (ADT #893), because the
screen prints them as two columns, `TYPE` and `NAME`, and a label such as `PAGE
TEMPLATE Standard` cannot be split back into the two: the kind is one word or
two. A sub-component is named `<kind> <name>` inside a property's owner, the kind
always one token (`saved-report`, `dynamic-action`), so `sub_component` can.

APEXlang is read here; READABLE_YAML and split SQL in `apex_formats`.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import PurePosixPath

from adt_ai.diff.inventory import CHANGED, EXTRA, MISSING
from adt_ai.shared.apex_version import apex_version_tuple
from adt_ai.shared.object_types import singular_object_type

APEXLANG = "apexlang"
READABLE = "readable"
SPLIT = "split"

#: The release APEXlang arrived in and READABLE_YAML became its alias.
_APEXLANG_RELEASE = (26, 1)

#: The component a file's own application settings are reported as.
APPLICATION_COMPONENT = "APPLICATION"

#: The kind a page is reported as, the one kind whose rows `-page` keeps.
PAGE_KIND = "PAGE"

#: What marks a component the export carries from the workspace.
WORKSPACE_PREFIX = "WORKSPACE "


@dataclass(frozen=True)
class Component:
    """One page or shared component: its kind, its name and every compared property.

    A page is named by its number and its name, `10 Orders`, which is how the
    screen's `NAME` column reads it.
    """

    kind       : str
    name       : str = ""
    properties : Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PropertyChange:
    """One property both sides disagree about. `None` is a side that lacks it."""

    name   : str
    source : str | None
    target : str | None


@dataclass(frozen=True)
class ComponentChange:
    """A page or shared component the two sides disagree about."""

    kind       : str
    name       : str
    status     : str
    properties : tuple[PropertyChange, ...] = ()

    @property
    def page(self) -> int | None:
        """The number of the page this row is, `None` for anything but a page."""
        number = self.name.partition(" ")[0]
        return int(number) if self.kind == PAGE_KIND and number.isdigit() else None


def export_format(source_version: str | None, target_version: str | None) -> str:
    """The richest format both instances can export.

    APEXlang where both run 26.1 or later, READABLE_YAML where neither does, and
    split SQL where they straddle the release, since each of the two better
    formats exists on one side only. An unknown release counts as pre-26.1: its
    readable export either answers or comes back empty, and an empty one falls
    back to split (`apex_export.read_components`).
    """
    new = [apex_version_tuple(v) >= _APEXLANG_RELEASE for v in (source_version, target_version)]
    if all(new):
        return APEXLANG
    if not any(new):
        return READABLE
    return SPLIT


def parse_components(export: str, files: Mapping[str, str]) -> dict[str, Component]:
    """Every component the export's files hold, keyed by what pairs it across sides.

    A page is keyed by its number, since renaming a page does not make it a
    different page; a shared component by its type and name, the only identity
    it has without an id. The order is the one the screen prints: the pages by
    number, then the application, the shared components by type, and the
    workspace's own last (Jan, ADT #893: *"keep it grouped per pages - app
    changes (without page) will be at the bottom"*).
    """
    # Deferred: the two older formats' readers build on this module's model.
    from adt_ai.diff.apex_formats import readable_components, split_components

    parsers: dict[str, Callable[[str, str], Iterable[tuple[str, Component]]]] = {
        # An `.apx` names the members its scripts live in, so it reads them too.
        APEXLANG : partial(_apexlang, members=files),
        READABLE : readable_components,
        SPLIT    : split_components,
    }
    parser = parsers[export]
    components: dict[str, Component] = {}
    for path in sorted(files):
        for key, component in parser(path, files[path]):
            components[_unique(components, key)] = component
    return dict(sorted(components.items(), key=lambda item: _order(item[0])))


def compare_components(
    source: Mapping[str, Component], target: Mapping[str, Component]
) -> tuple[ComponentChange, ...]:
    """`MISSING`, `EXTRA` or `CHANGED` per component, in the source's order."""
    keys = sorted(set(source) | set(target), key=_order)
    changes: list[ComponentChange] = []
    for key in keys:
        mine, theirs = source.get(key), target.get(key)
        if theirs is None and mine is not None:
            changes.append(ComponentChange(mine.kind, mine.name, MISSING))
        elif mine is None and theirs is not None:
            changes.append(ComponentChange(theirs.kind, theirs.name, EXTRA))
        elif mine is not None and theirs is not None and mine.properties != theirs.properties:
            changes.append(
                ComponentChange(mine.kind, mine.name, CHANGED, _property_changes(mine, theirs))
            )
    return tuple(changes)


def _property_changes(source: Component, target: Component) -> tuple[PropertyChange, ...]:
    names = [*source.properties, *(n for n in target.properties if n not in source.properties)]
    return tuple(
        PropertyChange(name, source.properties.get(name), target.properties.get(name))
        for name in names
        if source.properties.get(name) != target.properties.get(name)
    )


def _order(key: str) -> tuple[int, int, str]:
    """Pages by number, then the application, the shared components, the workspace's.

    A shared component's type may be two words (`PAGE TEMPLATE Standard`), so
    its key sorts as a whole.
    """
    kind, _, rest = key.partition(" ")
    if kind == PAGE_KIND and rest.isdigit():
        return (0, int(rest), "")
    if key == APPLICATION_COMPONENT:
        return (1, 0, "")
    return (3 if key.startswith(WORKSPACE_PREFIX) else 2, 0, key)


def _unique(components: Mapping[str, Component], key: str) -> str:
    """Two components under one key get `#2`, `#3`, so neither is dropped."""
    if key not in components:
        return key
    index = 2
    while f"{key} #{index}" in components:
        index += 1
    return f"{key} #{index}"


def _value(text: str) -> str:
    """A value as compared: trailing whitespace is never a difference."""
    return "\n".join(line.rstrip() for line in text.rstrip().splitlines())


# --- APEXlang -----------------------------------------------------------------

#: Instance bookkeeping rather than application source: set on import, so two
#: environments holding the same application never agree on it.
_APEXLANG_IGNORED = frozenset({"allowUrlsCreatedAfter"})

#: The static files' metadata. Their bytes are already compared as `APEX APP
#: FILE` rows, so reading this too would report one missing file twice.
_APEXLANG_STATIC_FILES = "shared-components/static-files.apx"

#: A block opens `keyword (`, `keyword name (`, or `keyword "name" (` when the
#: name has a space: 434 blocks of the 26.1 sample applications in
#: `tests/fixtures/sample/` do, most of them install and upgrade scripts. Read
#: as `\S+`, each was no block at all (ADT #923).
_APX_BLOCK = re.compile(r'^([A-Za-z][\w-]*)(?:\s+("[^"]*"|\S+))?\s*\($')
_APX_GROUP = re.compile(r"^([A-Za-z][\w-]*)\s*\{$")
_APX_PROPERTY = re.compile(r"^([A-Za-z][\w/-]*):\s*(.*)$")

#: What ends a property that names a member of the export instead of holding a
#: value: a script's `contentFile`, the deinstall `scriptFile`, a report
#: layout's `pageFile`.
_APX_MEMBER_SUFFIX = "File"


@dataclass
class _Block:
    keyword    : str
    ident      : str | None
    properties : dict[str, str] = field(default_factory=dict)
    groups     : list[str] = field(default_factory=list)
    children   : set[str] = field(default_factory=set)


def _apexlang(path: str, text: str, members: Mapping[str, str]) -> Iterable[tuple[str, Component]]:
    """Each top-level `keyword name ( ... )` block of an `.apx` file.

    `deployments/` and `.apex/` hold the application id and the APEX release,
    which is exactly what two environments may differ in without the
    application differing, so neither is read.

    A property naming a member reads as that member's body, from `members`:
    the `.apx` carries only the file name, so a changed install script changed
    no line the drill-down read, and the application's checksum differed with
    no component listed under it (ADT #923).
    """
    if not path.endswith(".apx") or path.startswith((".apex/", "deployments/")):
        return
    if path == _APEXLANG_STATIC_FILES:
        return
    lines = text.splitlines()
    stack: list[_Block] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue
        if line == ")" and stack:
            block = stack.pop()
            if stack:
                _merge(stack[-1], block)
            else:
                yield _apexlang_component(block)
            continue
        if match := _APX_BLOCK.match(line):
            stack.append(_Block(match.group(1), _unquote(match.group(2) or "", '"') or None))
            continue
        if not stack:
            continue
        block = stack[-1]
        if line == "}":
            if block.groups:
                block.groups.pop()
            continue
        if match := _APX_GROUP.match(line):
            block.groups.append(match.group(1))
            continue
        if match := _APX_PROPERTY.match(line):
            key, value = match.group(1), match.group(2)
            if value == "[":
                items: list[str] = []
                while index < len(lines) and lines[index].strip() != "]":
                    items.append(lines[index].strip())
                    index += 1
                index += 1
                value = "\n".join(items)
            elif not value and index < len(lines) and lines[index].strip().startswith("```"):
                indent = len(lines[index]) - len(lines[index].lstrip())
                index += 1
                code: list[str] = []
                while index < len(lines) and lines[index].strip() != "```":
                    code.append(lines[index][indent:])
                    index += 1
                index += 1
                value = "\n".join(code)
            if key.endswith(_APX_MEMBER_SUFFIX):
                value = _member(path, value, members)
            if key not in _APEXLANG_IGNORED:
                block.properties[".".join([*block.groups, key])] = _value(value)


def _member(path: str, name: str, members: Mapping[str, str]) -> str:
    """The body of the member `name` the `.apx` at `path` names, else the name.

    A script's body sits in the folder named after its `.apx`
    (`supporting-objects/install-scripts/emp-adams.sql`), the deinstall script
    and a report layout beside it (`supporting-objects/deinstall-script.sql`):
    all 575 references in the 26.1 sample applications resolve one of the two
    ways. A member the export did not carry compares by its name.
    """
    apx = PurePosixPath(path)
    for candidate in (apx.parent / apx.stem / name, apx.parent / name):
        body = members.get(str(candidate))
        if body is not None:
            return body
    return name


def _merge(parent: _Block, child: _Block) -> None:
    """A nested block's properties, named by the block, land in its parent.

    A block with no identifier is named by its `name` property, which is read
    only once the block has closed; two blocks under one name get `#2`.
    """
    name = child.ident or child.properties.get("name") or ""
    prefix = f"{child.keyword} {name}".strip()
    if prefix in parent.children:
        index = 2
        while f"{prefix} #{index}" in parent.children:
            index += 1
        prefix = f"{prefix} #{index}"
    parent.children.add(prefix)
    for key, value in child.properties.items():
        parent.properties[f"{prefix} > {key}" if ": " in key else f"{prefix}: {key}"] = value


def split_property(name: str) -> tuple[str, str]:
    """`region Orders: layout.sequence` as the sub-component and its property.

    A property of the component itself (`identification.name` on a page) has
    no sub-component and answers `""` for it.
    """
    owner, separator, prop = name.rpartition(": ")
    return (owner, prop) if separator else ("", name)


def sub_component(owner: str) -> tuple[str, str]:
    """`region Orders > saved-report Mine` as `("SAVED REPORT", "Mine")`.

    The innermost sub-component is the one that changed. Its kind is the first
    token in every format, an APEXlang keyword, a READABLE key or a split call,
    so a two-word kind is joined by `-` or `_` and read back with spaces.
    """
    kind, _, name = owner.rsplit(" > ", 1)[-1].partition(" ")
    return kind.replace("-", " ").replace("_", " ").upper(), name


def _apexlang_component(block: _Block) -> tuple[str, Component]:
    if block.keyword == "app":
        return APPLICATION_COMPONENT, Component(APPLICATION_COMPONENT, "", block.properties)
    kind = block.keyword.upper()
    if kind == PAGE_KIND:
        return _page(str(block.ident), block.properties.get("name"), block.properties)
    kind = singular_object_type(kind.replace("-", " "))
    name = block.ident or ""
    return f"{kind} {name}".strip(), Component(kind, name, block.properties)


def _page(number: str, name: str | None, properties: Mapping[str, str]) -> tuple[str, Component]:
    """A page keyed by its number and named by its number and name, `10 Orders`."""
    label = f"{number} {name or ''}".strip()
    return f"{PAGE_KIND} {number}", Component(PAGE_KIND, label, properties)


def _unquote(value: str, quote: str = "'") -> str:
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] == quote else value


__all__ = [name for name in globals() if not name.startswith("_")]
