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
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

import yaml

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
    back to split (`apex.read_components`).
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
    parser = {APEXLANG: _apexlang, READABLE: _readable, SPLIT: _split}[export]
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


def _workspace(path: str) -> str:
    """`WORKSPACE ` for a component the export carries from the workspace.

    An application's export includes the workspace's own components, such as
    its application groups (`readable/workspace/app_groups.yaml`, measured on
    app 122), and the row must not read as part of the application.
    """
    return WORKSPACE_PREFIX if path.removeprefix("readable/").startswith("workspace/") else ""


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

_APX_BLOCK = re.compile(r"^([A-Za-z][\w-]*)(?:\s+(\S+))?\s*\($")
_APX_GROUP = re.compile(r"^([A-Za-z][\w-]*)\s*\{$")
_APX_PROPERTY = re.compile(r"^([A-Za-z][\w/-]*):\s*(.*)$")


@dataclass
class _Block:
    keyword    : str
    ident      : str | None
    properties : dict[str, str] = field(default_factory=dict)
    groups     : list[str] = field(default_factory=list)
    children   : set[str] = field(default_factory=set)


def _apexlang(path: str, text: str) -> Iterable[tuple[str, Component]]:
    """Each top-level `keyword name ( ... )` block of an `.apx` file.

    `deployments/` and `.apex/` hold the application id and the APEX release,
    which is exactly what two environments may differ in without the
    application differing, so neither is read.
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
            if key not in _APEXLANG_IGNORED:
                block.properties[".".join([*block.groups, key])] = _value(value)


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


# --- READABLE_YAML ------------------------------------------------------------

#: Keys that are ids (`id`, `referenced-id`) or instance bookkeeping, and the
#: one section whose whole content is how this environment subscribes rather
#: than what the component is.
_READABLE_IGNORED_SECTIONS = frozenset({"subscription"})


def _readable_ignored(key: str) -> bool:
    return key == "id" or key.endswith("-id") or key in _READABLE_IGNORED_SECTIONS


def _readable(path: str, text: str) -> Iterable[tuple[str, Component]]:
    """Pages, the application, and each entry of a shared-component file.

    Read with `BaseLoader`, so every scalar stays the text APEX wrote: `true`
    is not `True`, and `0010` is not `10`. The id comments APEX appends
    (`Dashboard # 141319273602527546`) are comments, so the loader drops them.
    """
    if not path.endswith(".yaml"):
        return
    # BaseLoader keeps every scalar a string and builds no objects; it is driven
    # directly because `yaml.load` with any Loader reads as unsafe to scanners.
    document = yaml.BaseLoader(_printable(text)).get_single_data()
    stem = PurePosixPath(path).stem
    if re.fullmatch(r"f\d+", stem):
        yield APPLICATION_COMPONENT, Component(APPLICATION_COMPONENT, "", _flatten(document))
        return
    # The export names a page `pages/p00020.yaml`; `export_apex` writes it to
    # disk as `page_00020.yaml`, so both spellings are one page.
    if page := re.fullmatch(r"(?:page_|p)(\d+)", stem):
        yield _page(str(int(page.group(1))), _yaml_name(document), _flatten(document))
        return
    kind = _workspace(path) + singular_object_type(stem.replace("_", " ").upper())
    for item in document if isinstance(document, list) else [document]:
        name = _yaml_name(item) or ""
        yield f"{kind} {name}".strip(), Component(kind, name, _flatten(item))


#: Control characters a YAML reader refuses. APEX writes them verbatim inside
#: block scalars: app 122's page 20 carries a form feed in a PL/SQL body.
_UNPRINTABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _printable(text: str) -> str:
    """The control characters spelled `\\x0c`, so both sides still compare."""
    return _UNPRINTABLE.sub(lambda match: f"\\x{ord(match.group()):02x}", text)


def _yaml_name(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for section, key in (("identification", "name"), ("label", "label"), ("", "name")):
        holder = item.get(section) if section else item
        if isinstance(holder, dict) and isinstance(holder.get(key), str):
            return str(holder[key])
    return None


def _flatten(node: Any, path: tuple[str, ...] = (), owner: str = "") -> dict[str, str]:
    """Every scalar under `node`, named `owner: a.b.c`.

    A list of mappings is a list of sub-components (a page's processes, a
    list's entries), each named by its kind and its own name, `process Load
    Privs`, so the property keeps pointing at the same process however the list
    is ordered. One nested in another is named after both, `region Orders >
    saved-report Mine`, the kind hyphenated so it stays one token.
    """
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            if not _readable_ignored(str(key)):
                out.update(_flatten(value, (*path, str(key)), owner))
    elif isinstance(node, list) and any(isinstance(item, dict) for item in node):
        kind = (
            singular_object_type(path[-1].replace("-", " ").upper()).lower().replace(" ", "-")
            if path
            else ""
        )
        for index, item in enumerate(node, start=1):
            name = _unique_name_in(out, f"{kind} {_yaml_name(item) or f'#{index}'}".strip())
            out.update(_flatten(item, (), f"{owner} > {name}" if owner else name))
    elif isinstance(node, list):
        out[_property(owner, path)] = _value("\n".join(str(item) for item in node))
    elif node is not None:
        out[_property(owner, path)] = _value(str(node))
    return out


def _unique_name_in(properties: Mapping[str, str], name: str) -> str:
    taken = {key.split(": ", 1)[0].rsplit(" > ", 1)[-1] for key in properties if ": " in key}
    if name not in taken:
        return name
    index = 2
    while f"{name} #{index}" in taken:
        index += 1
    return f"{name} #{index}"


def _property(owner: str, path: tuple[str, ...]) -> str:
    dotted = ".".join(path)
    return f"{owner}: {dotted}" if owner else dotted


# --- split SQL ----------------------------------------------------------------

#: Files that describe the installation rather than the application.
_SPLIT_SKIPPED = re.compile(
    r"(?:^|/)(?:install|f\d+|set_environment|end_environment|delete_application)\.sql$"
)

#: Calls that open or close an install, never a component.
_SPLIT_SKIPPED_CALLS = frozenset(
    {"import_begin", "import_end", "component_begin", "component_end"}
)

#: Parameters that are ids, the application or workspace they were exported
#: from, the APEX release, a subscription's bookkeeping, or audit stamps: what
#: differs between two environments holding the same application. App 122's
#: plugin carried `p_version_scn` on DEV and `p_reference_id` on TEST.
_SPLIT_IGNORED = re.compile(
    r"^p_(?:id|flow_id|page_id|default_\w+|release|version_yyyy_mm_dd|files_version"
    r"|version_scn|reference_id"
    r"|last_updated_by|last_upd_yyyymmddhh24miss|created_by|created_on)$"
)

_SPLIT_CALL = re.compile(r"^wwv_flow_imp\w*\.(\w+)\s*\($")
_SPLIT_PARAMETER = re.compile(r"^[ ,](p_\w+)=>(.*)$")
_SPLIT_NAME = re.compile(r"^p_(?:\w+_)?name$")
_SPLIT_ID = re.compile(r"wwv_flow_imp\.id\(\d+\)(?:\s+--.*)?")

#: A call named for what a page builder calls the thing it creates, so a split
#: export reads `region Orders` where the other two formats do. Any other
#: `page_` call drops the prefix: `page_button` is a `button`.
_SPLIT_KINDS = {
    "page_plug"      : "region",
    "page_da_event"  : "dynamic-action",
    "page_da_action" : "action",
    "list_item"      : "entry",
}


def _split(path: str, text: str) -> Iterable[tuple[str, Component]]:
    """One component per `.sql` file, a property per call parameter.

    Every `wwv_flow_imp.id(...)` reads `id`, the way `#778`'s first measurement
    asked: two copies of one application disagreed on every id and on nothing
    else in `plugin_settings.sql`.

    The file's first call creates the component itself (`create_page`,
    `create_flow`, `create_list`), so its parameters are the component's own
    properties and its name is the component's name; every later call is a
    sub-component named by its kind and name.
    """
    if not path.endswith(".sql") or _SPLIT_SKIPPED.search(path):
        return
    properties: dict[str, str] = {}
    own_name: str | None = None
    for index, (call, parameters) in enumerate(_split_calls(text)):
        # Only a quoted value is a name: `p_show_name=>true` is a switch.
        name = next(
            (
                _unquote(value)
                for key, value in parameters
                if _SPLIT_NAME.match(key) and value.startswith("'")
            ),
            None,
        )
        owner = ""
        if index == 0:
            own_name = name
        else:
            owner = _unique_name_in(properties, f"{_split_kind(call)} {name or ''}".strip())
        for key, value in parameters:
            if not _SPLIT_IGNORED.match(key):
                properties[_property(owner, (key.removeprefix("p_"),))] = _value(
                    _SPLIT_ID.sub("id", value)
                )
    parts = PurePosixPath(path).parts
    stem = PurePosixPath(path).stem
    if page := re.fullmatch(r"page_(\d+)", stem):
        yield _page(str(int(page.group(1))), own_name, properties)
        return
    if stem == "create_application":
        yield APPLICATION_COMPONENT, Component(APPLICATION_COMPONENT, "", properties)
        return
    folder = parts[-2] if len(parts) > 1 else ""
    kind = _workspace(path) + singular_object_type(folder.replace("_", " ").upper())
    yield f"{kind} {stem}".strip(), Component(kind, own_name or stem, properties)


def _split_kind(call: str) -> str:
    call = call.removeprefix("create_")
    return _SPLIT_KINDS.get(call, call.removeprefix("page_"))


def _split_calls(text: str) -> Iterable[tuple[str, list[tuple[str, str]]]]:
    call: str | None = None
    parameters: list[tuple[str, str]] = []
    for line in text.splitlines():
        if call is None:
            match = _SPLIT_CALL.match(line.strip())
            if match and match.group(1) not in _SPLIT_SKIPPED_CALLS:
                call, parameters = match.group(1), []
            continue
        if line.strip() == ");":
            yield call, parameters
            call = None
            continue
        if match := _SPLIT_PARAMETER.match(line):
            parameters.append((match.group(1), match.group(2)))
        elif parameters:
            key, value = parameters[-1]
            parameters[-1] = (key, f"{value}\n{line}")


def _unquote(value: str, quote: str = "'") -> str:
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] == quote else value


__all__ = [name for name in globals() if not name.startswith("_")]
