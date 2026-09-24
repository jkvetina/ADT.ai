"""READABLE_YAML and split SQL read as components (ADT #892).

The two formats an instance before APEX 26.1 exports. Split out of
`apex_components` by ADT #923, which took that module past the 20 KB context
size: what a component is, how two compare, which format a pair of instances
gets and how APEXlang is read stay there; how each of these two is read, and
the noise each one drops, moved here.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

import yaml

from adt_ai.diff.apex_components import (
    APPLICATION_COMPONENT,
    WORKSPACE_PREFIX,
    Component,
    _page,
    _unquote,
    _value,
)
from adt_ai.shared.object_types import singular_object_type


def _workspace(path: str) -> str:
    """`WORKSPACE ` for a component the export carries from the workspace.

    An application's export includes the workspace's own components, such as
    its application groups (`readable/workspace/app_groups.yaml`, measured on
    app 122), and the row must not read as part of the application.
    """
    return WORKSPACE_PREFIX if path.removeprefix("readable/").startswith("workspace/") else ""


# --- READABLE_YAML ------------------------------------------------------------

#: Keys that are ids (`id`, `referenced-id`) or instance bookkeeping, and the
#: one section whose whole content is how this environment subscribes rather
#: than what the component is.
_READABLE_IGNORED_SECTIONS = frozenset({"subscription"})


def _readable_ignored(key: str) -> bool:
    return key == "id" or key.endswith("-id") or key in _READABLE_IGNORED_SECTIONS


def readable_components(path: str, text: str) -> Iterable[tuple[str, Component]]:
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


def split_components(path: str, text: str) -> Iterable[tuple[str, Component]]:
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


__all__ = [name for name in globals() if not name.startswith("_")]
