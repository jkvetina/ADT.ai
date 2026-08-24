"""Resolving a repo path against the project's `path_objects` object layout.

`path_objects` is a TEMPLATE, not a folder, `<schema>/database/<object_type>/` by
default, which puts the schema FIRST. Everything here derives from that template, so
the same code answers for any configured layout.

It did not always. Until ADT #196 these helpers hardcoded the layout: a database file
was one whose first path segment was literally `database`, its schema was segment 1,
and its object-type folder started at segment 2. That is the LEGACY layout, and it is
not the shipped default, so for a project exporting to the default,
`patch -create` matched no file at all, mapped nothing, and wrote an empty
`files.txt` while reporting a patch folder and exiting 0. Found from the IVORY iCRM
repo, where every commit in the project's history had silently produced an empty
patch (Brain card IVORY #36).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# The deploy-log resolvers moved to `patch/deploy_paths.py` when this module
# crossed the 20 KB context guard (ADT #471). Re-exported: every caller imports
# them from here.
from adt_ai.patch.deploy_paths import deploy_log_folder as deploy_log_folder
from adt_ai.patch.deploy_paths import ensure_deploy_log_folder as ensure_deploy_log_folder
from adt_ai.shared.config import reject_unresolved_placeholders

# `object_layouts` moved to `shared/` with the ownership rule it feeds (ADT #471),
# so `search_repo` reads the same vocabulary. Re-exported: this module was its
# home and several patch modules import it from here.
from adt_ai.shared.object_files import object_layouts as object_layouts
from adt_ai.shared.object_files import (
    object_name_for_type,
    object_stem_for_type,
    owning_object_type,
)
from adt_ai.shared.path_template import (
    APEX_APP_TOKEN_NAMES,
    DEFAULT_PATH_APP,
    contains_run,
    is_schema_placeholder,
    object_type_token,
)

# What the helpers assumed before they read config. Still tried last, see
# `head_variants`.
LEGACY_HEAD = ("database", "<schema>")

# The same thing on the APEX side, which read it a card later (ADT #429).
LEGACY_APEX_HEAD = ("apex",)

# The tokens `apex_path_app` may carry. `export_apex/files.py::_render_app_folder`
# is the writer this reading inverts, and it now substitutes exactly this list
# (ADT #474). It used to match any `{$[A-Z_]+}`, so this reader would parse a
# folder name the writer could never have produced, which is the drift row E of
# that card is about: writer and reader knowing different vocabularies.
_APP_TOKEN_RE = re.compile(r"\{\$(?:" + "|".join(APEX_APP_TOKEN_NAMES) + r")\}")


def is_placeholder(name: str) -> bool:
    return "<" in name and ">" in name


def object_path_head(config: dict[str, Any]) -> tuple[str, ...]:
    """The ``path_objects`` segments above ``<object_type>``, placeholders kept."""
    # The fallback is the layout these helpers were written against, so a caller
    # that passes no config keeps its old answer exactly. Production always sets
    # `path_objects` from ADT's own config.yaml, where the default is schema-first.
    template = reject_unresolved_placeholders(
        str(config.get("path_objects") or "database/<schema>/<object_type>/")
    ).strip("/")
    head, _, _ = template.partition(object_type_token(template) or "<object_type>")
    return Path(head.strip("/")).parts if head.strip("/") else ()


def schema_index(head: tuple[str, ...]) -> int | None:
    """Where the schema level sits in ``head``, in whichever case it is spelled."""
    for index, segment in enumerate(head):
        if is_schema_placeholder(segment):
            return index
    return None


def drop_schema(head: tuple[str, ...]) -> tuple[str, ...]:
    index = schema_index(head)
    if index is None:
        return head
    return head[:index] + head[index + 1:]


def head_variants(head: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Every layout head a database file may sit under, most specific first.

    Two axes, and both are load-bearing:

    * **The schema level is optional.** An export can omit it
      (``database/views/x.sql`` beside ``database/app/views/x.sql``), so each head is
      tried with and without ``<schema>``. The full head goes first, so a real schema
      folder is never mistaken for a type folder.
    * **The legacy head is always tried last.** Before ADT #196 these helpers ignored
      ``path_objects`` entirely, so every project with a legacy tree works today
      *regardless* of what its config says, including under the shipped schema-first
      default. Deriving the head purely from config would fix the schema-first layout
      by breaking those. Keeping the legacy shape as a trailing fallback fixes one
      without costing the other.
    """
    seen: list[tuple[str, ...]] = []
    for variant in (head, drop_schema(head), LEGACY_HEAD, drop_schema(LEGACY_HEAD)):
        if variant and variant not in seen:
            seen.append(variant)
    return seen


def head_matches(path: str, head: tuple[str, ...]) -> bool:
    """Does ``path`` sit under ``head``?

    Every literal segment has to match; a placeholder matches whatever occupies its
    place. The file itself must sit BELOW the head, so a path no longer than the head
    is not a database file however well its prefix matches.
    """
    parts = Path(path).parts
    if len(parts) <= len(head):
        return False
    return all(
        is_placeholder(segment) or segment == parts[index]
        for index, segment in enumerate(head)
    )


def object_type_under(
    path: str,
    head: tuple[str, ...],
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    """The object type ``path`` resolves to when read under ``head``.

    Everything between the head and the filename is the object-type folder. It can
    span more than one segment (``grants/received``), so it is matched against the
    configured layouts whole rather than segment by segment.

    Which of the folder's types owns the file is `owning_object_type`'s question,
    never dict order. This returned the first layout whose extension the path
    merely ENDED WITH, so the shipped `TYPE`/`.sql` listed above
    `TYPE BODY`/`.body.sql` resolved every type body to `TYPE` (ADT #471).
    """
    folder = "/".join(Path(path).parts[len(head):-1])
    return owning_object_type(Path(path).name, folder, layouts)


def effective_object_head(
    path: str,
    config: dict[str, Any],
    layouts: dict[str, tuple[str, str]],
) -> tuple[str, ...] | None:
    """The layout head ``path`` actually sits under, or None when it sits under none.

    A variant that resolves an OBJECT TYPE wins over one that merely matches: for a
    schema-less ``database/views/x.sql`` the full head still matches, because
    ``<schema>`` happily swallows ``views``, and then the type folder is empty and
    the file resolves to no type at all. Preferring the variant that yields a type is
    what keeps both export shapes working.
    """
    matched = [
        head for head in head_variants(object_path_head(config))
        if head_matches(path, head)
    ]
    if not matched:
        return None
    for head in matched:
        if object_type_under(path, head, layouts) is not None:
            return head
    return matched[0]


def _head_for(path: str, config: dict[str, Any]) -> tuple[str, ...] | None:
    return effective_object_head(path, config, object_layouts(config.get("object_types", {})))


def resolved_object_head(path: str, config: dict[str, Any]) -> tuple[str, ...] | None:
    """The head ``path`` sits under, spelled the way the folders are on disk.

    `object_path_head` keeps its placeholders, which is what MATCHING needs. A
    caller looking for a sibling file under the same head needs the real folder
    names instead, casing included, because the schema level carries whatever the
    export wrote and no `lower()`/`upper()` guess covers `App_Owner`.
    """
    head = _head_for(path, config)
    if head is None:
        return None
    return Path(path).parts[:len(head)]


def apex_path_head(config: dict[str, Any]) -> tuple[str, ...]:
    """The ``path_apex`` segments, placeholders kept."""
    template = reject_unresolved_placeholders(
        str(config.get("path_apex") or "apex"),
        key     = "path_apex",
        allowed = ("schema",),
    ).strip("/")
    return Path(template).parts if template else ()


def apex_head_variants(config: dict[str, Any]) -> list[tuple[str, ...]]:
    """Every head an exported APEX artifact may sit under, most specific first.

    The same two axes `head_variants` uses for database objects, for the same
    reasons: the schema level is optional, and the classic head is tried last so
    a repo exported before `path_apex` existed keeps working whatever its config
    says now.
    """
    apex = apex_path_head(config)
    heads: list[tuple[str, ...]] = []
    for variant in (apex, drop_schema(apex), LEGACY_APEX_HEAD):
        if variant and variant not in heads:
            heads.append(variant)
    return heads


def apex_head_for(path: str, config: dict[str, Any]) -> tuple[str, ...] | None:
    for head in apex_head_variants(config):
        if head_matches(path, head):
            return head
    return None


def is_apex_path(path: str, config: dict[str, Any]) -> bool:
    """Does ``path`` sit under the configured APEX export root?

    Until ADT #429 this asked whether the first segment was the literal `apex`,
    which is one layout `path_apex` can produce and not the shipped default. On
    a project running `<schema>/apex/` no APEX file could enter a patch at all,
    `_patch_files` keeping only database and APEX paths.
    """
    return apex_head_for(path, config) is not None


def apex_app_root(path: str, config: dict[str, Any]) -> tuple[str, ...] | None:
    """The head plus the folder ``apex_path_app`` names for one application.

    The template can span more than one segment (`{$APP_GROUP}/{$APP_ID}`), so
    the depth is read off the template rather than assumed to be one.
    """
    head = apex_head_for(path, config)
    if head is None:
        return None
    depth = _apex_app_depth(config)
    parts = Path(path).parts
    if len(parts) <= len(head) + depth:
        return None
    return parts[: len(head) + depth]


def apex_app_id(path: str, config: dict[str, Any]) -> int | None:
    """The application id ``apex_path_app`` wrote into that folder's name.

    Inverts `export_apex/files.py::_render_app_folder`, the writer: every token
    but `{$APP_ID}` matches whatever it rendered, and `{$APP_ID}` is the digits
    read back out. A workspace-level artifact (`workspace/rest/`) resolves to no
    id, which is what keeps it on the database route (ADT #314).
    """
    head = apex_head_for(path, config)
    root = apex_app_root(path, config)
    if head is None or root is None:
        return None
    folder = "/".join(root[len(head):])
    pattern = _apex_app_pattern(config)
    if pattern is not None:
        match = pattern.fullmatch(folder)
        if match:
            return int(match.group("app_id"))
    # The classic all-digit folder, which is what a repo exported before the
    # template carried an alias still has on disk, and what every patch fixture
    # in the suite spells. Tried second, so a configured template wins.
    return int(folder) if folder.isdigit() else None


def is_apex_full_export(path: str, config: dict[str, Any]) -> bool:
    """``f<id>.sql`` sitting directly in that application's own folder."""
    app_id = apex_app_id(path, config)
    root = apex_app_root(path, config)
    if app_id is None or root is None:
        return False
    return Path(path).parts == (*root, f"f{app_id}.sql")


def is_apex_static_file(path: str, config: dict[str, Any]) -> bool:
    """A payload under the configured static-files folder, application or workspace.

    `apex_path_files` is a folder name relative to the application root and to
    the workspace root alike, so the run is matched wherever it sits under the
    APEX head rather than at one fixed depth. A `.sql` file is a script whatever
    folder holds it: what deploys for a static file is the generated
    `wwv_flow_imp` wrapper, and a script needs no wrapper.
    """
    if Path(path).as_posix().endswith(".sql"):
        return False
    head = apex_head_for(path, config)
    if head is None:
        return False
    configured = str(config.get("apex_path_files") or "files/").strip("/")
    return contains_run(Path(path).parts[len(head):-1], Path(configured).parts)


def _apex_app_depth(config: dict[str, Any]) -> int:
    return len([part for part in _apex_app_template(config).split("/") if part]) or 1


def _apex_app_pattern(config: dict[str, Any]) -> re.Pattern[str] | None:
    """A reader for the folder ``apex_path_app`` writes, or None when it names no id.

    A template carrying no `{$APP_ID}` cannot say which application a folder
    holds, so nothing is guessed from it: the classic all-digit reading in
    `apex_app_id` answers instead, exactly as it does today.
    """
    template = _apex_app_template(config)
    if "{$APP_ID}" not in template:
        return None
    pattern: list[str] = []
    position = 0
    for match in _APP_TOKEN_RE.finditer(template):
        pattern.append(re.escape(template[position:match.start()]))
        pattern.append(r"(?P<app_id>\d+)" if match.group(0) == "{$APP_ID}" else "[^/]*")
        position = match.end()
    pattern.append(re.escape(template[position:]))
    return re.compile("".join(pattern))


def _apex_app_template(config: dict[str, Any]) -> str:
    return str(config.get("apex_path_app") or DEFAULT_PATH_APP).strip("/")


def rest_path_heads(config: dict[str, Any]) -> list[tuple[str, ...]]:
    """Every head ``export_apex -rest`` writes under, most specific first.

    `apex_path_rest` is relative to the APEX export root, so the head is the two
    templates joined. The schema-dropped variant is tried for the same reason
    `head_variants` tries it for database objects: a project can flatten the
    schema level out of its export layout.
    """
    rest = str(config.get("apex_path_rest") or "workspace/rest/").strip("/")
    if not rest:
        return []
    suffix = Path(rest).parts
    apex = apex_path_head(config)
    heads: list[tuple[str, ...]] = []
    for variant in (apex, drop_schema(apex)):
        candidate = variant + suffix
        if candidate not in heads:
            heads.append(candidate)
    return heads


def is_rest_path(path: str, config: dict[str, Any]) -> bool:
    """Does ``path`` hold an exported ORDS REST module?

    Gated on a configured ``REST`` object type rather than on the folder alone: a
    project whose `object_types` predates REST support keeps the behaviour it has
    today, and the config entry is what opts it into the `rest` patch group.
    """
    layout = object_layouts(config.get("object_types", {})).get("REST")
    if layout is None:
        return False
    _folder, extension = layout
    return any(
        head_matches(path, head) and path.endswith(extension)
        for head in rest_path_heads(config)
    )


def is_database_path(path: str, config: dict[str, Any]) -> bool:
    # A REST module is schema-level PL/SQL that runs as the schema owner, not an
    # APEX application artifact: it belongs in the database install script, in the
    # group `patch_map` puts it in (ADT #314).
    return is_rest_path(path, config) or _head_for(path, config) is not None


def database_object_type(path: str, config: dict[str, Any]) -> str | None:
    if is_rest_path(path, config):
        return "REST"
    head = _head_for(path, config)
    if head is None:
        return None
    return object_type_under(path, head, object_layouts(config.get("object_types", {})))


def database_object_name(path: str, config: dict[str, Any]) -> str | None:
    """The Oracle object name ``path`` holds, upper-cased, or None for no object.

    Read through the CONFIGURED extension, the only thing that can strip a
    compound one. Callers spelled this `Path(path).stem.upper()`, and `.stem`
    strips a single suffix: `packages/core.spec.sql` came back as `CORE.SPEC`,
    which `safe_identifier` refused in the DROP-helper writer and which matched
    no dictionary row in the staleness gate (ADT #471).
    """
    return _object_name(path, config, object_name_for_type)


def database_object_stem(path: str, config: dict[str, Any]) -> str | None:
    """The same name with the casing on disk, for a name rendered into DDL.

    A generated patch script is a compatibility contract, so nothing rendering
    into SQL uppercases what the file spells.
    """
    return _object_name(path, config, object_stem_for_type)


def _object_name(path: str, config: dict[str, Any], read: Any) -> str | None:
    object_type = database_object_type(path, config)
    if object_type is None:
        return None
    return read(Path(path).name, object_type, object_layouts(config.get("object_types", {})))


def database_schema(path: str, config: dict[str, Any]) -> str:
    # "DATABASE" is the sentinel for a layout that carries no schema level, and
    # `_patch_group` names the install script off this answer, so a path that
    # sits outside the layout must report it too, never a stray segment.
    head = apex_path_head(config) if is_rest_path(path, config) else _head_for(path, config)
    if head is None or not head_matches(path, head):
        return "DATABASE"
    index = schema_index(head)
    if index is None:
        return "DATABASE"
    return Path(path).parts[index].upper()
