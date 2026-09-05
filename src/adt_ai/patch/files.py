from __future__ import annotations

import heapq
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any

from adt_ai.dependencies.store import DependencyStore
from adt_ai.patch import archive_paths as _archive_paths
from adt_ai.patch import settings as _settings
from adt_ai.patch.layout import (
    is_apex_static_file as _is_apex_static_file,
)
from adt_ai.patch.layout import (
    is_placeholder as _is_placeholder,
)
from adt_ai.patch.layout import (
    object_layouts as _object_layouts,
)
from adt_ai.patch.models import PatchError
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import PatchFolder
from adt_ai.shared.config import DEFAULT_PATH_OBJECTS, reject_unresolved_placeholders
from adt_ai.shared.git_files import git_blob_exists, git_status_paths
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.object_files import (
    extensions_for_folder,
    owning_object_type,
    owns_file,
)
from adt_ai.shared.path_template import object_type_token, schema_token
from adt_ai.shared.safe_paths import simple_relative_path, under_root


@dataclass(frozen=True)
class InstallScriptResult:
    path: Path
    files: list[str]
    schema: str = ""
    overview: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class InstallTarget:
    """One rendered ``path_objects`` layout: where an INSTALL.sql belongs.

    ``root`` is the folder the script is written to (the template truncated at
    ``<object_type>``), ``suffix`` whatever the template puts *after* that
    placeholder, and ``schema`` the folder name ``<schema>`` resolved to (empty
    for a layout with no ``<schema>`` placeholder).
    """

    schema: str
    root: Path
    suffix: str

    def folder_for(self, layout_folder: str) -> Path:
        return self.root / Path(f"{layout_folder}{self.suffix}".strip("/"))


@dataclass(frozen=True)
class ArchiveResult:
    """What an archive run took, and where each piece of it landed.

    ``folders`` replaced a plain list of names in ADT #346: the console prints
    the patch code and the card id beside the folder now, and re-parsing those
    back out of a string when the caller already holds the parsed record is how
    two spellings of one value start to disagree.
    """

    folders: list[PatchFolder]
    archive_paths: list[Path]
    script_archives: list[Path]

    @property
    def archived(self) -> list[str]:
        return [folder.folder for folder in self.folders]


def next_patch_folder(
    patch_root: Path,
    patch_code: str,
    *,
    today: date | None = None,
    config: dict[str, Any] | None = None,
) -> Path:
    """`<yymmdd>-<seq>-<CODE>`, where the sequence is unique for the DAY.

    Old ADT (patch.py:208-223) globbed every folder created today, no code
    filter, and took `max(seq + 1)`, except when the code already had a folder,
    which it reused so a re-created patch rewrites its own folder instead of
    growing a second one. ADT.ai required the code to match on BOTH counts, which
    inverted both: every patch that day drew `1`, and re-creating one invented a
    new sequence (ADT #266, Jan 2026-08-10). Identical names across a day are
    also what makes `#255`'s wrong-folder deploy reachable.

    The NAME comes from `patch_folder`, `today_patch` and `patch_folder_splitter`
    since ADT #430, and the reader is derived from the same template. The day is
    compared as the RENDERED string rather than as a date, so a project stamping
    `%Y%m%d` compares against its own spelling and never has to know what the
    default writes.
    """
    config = config or {}
    today = today or date.today()
    code = _patch_code(patch_code)
    day = today.strftime(_settings.today_patch_format(config))
    pattern = _settings.patch_folder_re(config)
    sequence = 1
    if patch_root.exists():
        for path in sorted(patch_root.iterdir()):
            match = pattern.match(path.name)
            if not match or match.group("day") != day:
                continue
            if match.group("code").upper() == code:
                return path
            sequence = max(sequence, int(match.group("sequence")) + 1)
    return patch_root / _settings.patch_folder_name(
        config, day=today, sequence=sequence, code=code
    )


def write_install_script(root: Path, config: dict[str, Any]) -> list[InstallScriptResult]:
    """Write one ``INSTALL.sql`` per exported schema and report what went in it.

    ``path_objects`` is a path *template* (``<schema>/database/<object_type>/``),
    never a literal folder: ``<schema>`` resolves against the schema folders that
    actually exist on disk, and the install script sits above the per-type level,
    so it lands at ``<schema>/database/INSTALL.sql``.
    """
    edges = _dependency_edges(root)
    results: list[InstallScriptResult] = []
    for target in _install_targets(root, config):
        grouped = _install_groups(target, config, edges)
        if not grouped:
            # A schema root with no exported objects gets no script and no
            # section: an empty overview table is noise, not a report.
            continue
        overview = _install_overview(grouped, config)
        install_path = target.root / _settings.install_script_name(config)
        install_path.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_text(install_path, _install_payload(grouped, overview, config))
        results.append(
            InstallScriptResult(
                path     = install_path,
                files    = [f for files in grouped.values() for f in files],
                schema   = target.schema,
                overview = overview,
            )
        )
    return results


def archive_patch_folders(
    root: Path,
    config: dict[str, Any],
    folders: list[PatchFolder],
) -> ArchiveResult:
    archive_root = root / str(config.get("patch_archive") or "patch_archive").strip("/")
    archive_root.mkdir(parents=True, exist_ok=True)
    archive_paths: list[Path] = []
    script_archives: list[Path] = []
    # `patch_archive_format` (ADT #431). The path each archive landed at is taken
    # from `make_archive`'s own return value rather than composed from the format
    # name, because `gztar` writes `.tar.gz` and a second derivation is how the
    # reported path and the file on disk come apart.
    archive_format = _settings.archive_format(config)
    for folder in folders:
        script_folder = _patch_scripts_folder(root, config, folder.patch_code)
        if folder.patch_code and script_folder.exists():
            script_archive = folder.path / folder.patch_code
            made = shutil.make_archive(str(script_archive), archive_format, root_dir=script_folder)
            shutil.rmtree(script_folder)
            script_archives.append(Path(made))

        # `patch_archive_subfolder` (ADT #517): the month the patch itself was
        # built in, `""` when the archive is flat or the day is unreadable. The
        # folder is created here rather than up front, so a run that files
        # nothing under a month never leaves an empty one behind.
        destination = archive_root / _archive_paths.archive_subfolder(config, folder=folder.folder)
        destination.mkdir(parents=True, exist_ok=True)
        archive_path = destination / folder.folder
        made = shutil.make_archive(str(archive_path), archive_format, root_dir=folder.path)
        shutil.rmtree(folder.path)
        archive_paths.append(Path(made))

    return ArchiveResult(
        folders         = list(folders),
        archive_paths   = archive_paths,
        script_archives = script_archives,
    )


def file_source_modes(root: Path, path: str) -> dict[str, bool]:
    """Whether HEAD carries a blob for ``path`` and whether it is locally dirty.

    ``local`` reads through the same batched, `-z` NUL-safe reader
    `patch/report.py::_uncommitted` uses (`#670`), rather than the plain
    `git status --porcelain` this used before: a single path is still one
    subprocess call, but it is the same call that decodes a non-ASCII path
    correctly instead of the one that C-quotes it (`#664`'s defect, in the
    other reader).
    """
    return {
        "head": git_blob_exists(root, "HEAD", path),
        "local": path in git_status_paths(root, [path]),
    }


def _patch_code(value: str) -> str:
    code = str(value).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]*", code):
        raise ValueError(
            f"Unsupported patch name {value!r}; use only letters, digits, _ or -"
        )
    return code


def _patch_scripts_folder(root: Path, config: dict[str, Any], patch_code: str) -> Path:
    code = _patch_code(patch_code)
    raw = str(config.get("patch_scripts_dir") or "patch_scripts/{$PATCH_CODE}/").strip("/")
    if "{$PATCH_CODE}" in raw or "#PATCH_CODE#" in raw or "{PATCH_CODE}" in raw:
        rendered = (
            raw
            .replace("{$PATCH_CODE}", code)
            .replace("#PATCH_CODE#", code)
            .replace("{PATCH_CODE}", code)
        )
    elif "/None/" in raw:
        rendered = raw.replace("/None/", f"/{code}/")
    elif Path(raw).name.upper() == code:
        rendered = raw
    else:
        rendered = f"{raw}/{code}"
    return under_root(
        root,
        root / simple_relative_path(rendered, role="patch_scripts_dir"),
        role="patch scripts folder",
    )


# A conflict marker is a LINE of exactly seven `<`, `=` or `>`, optionally carrying
# a label, git's own format. Matching the bare substring instead flagged any run of
# seven `=` anywhere in a file, which is the separator-comment style every PL/SQL
# codebase uses (`-- =========`), so a perfectly clean patch was refused outright.
# The line anchors are what make the check mean "conflict" rather than "equals
# signs" (`#197`).
_MERGE_MARKER_RE = re.compile(r"^(?:<{7}|={7}|>{7})(?: .*)?$", re.MULTILINE)


def _reject_unresolved_merges(root: Path, files: list[str]) -> None:
    conflicted: list[str] = []
    for path in files:
        file_path = root / path
        if not file_path.exists():
            continue
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if _MERGE_MARKER_RE.search(text):
            conflicted.append(path)
    if conflicted:
        raise PatchError(f"unresolved merge markers in patch file(s): {', '.join(conflicted)}")


# Where an APEX artifact SITS is a layout question, so it is answered in
# `patch/layout.py` beside the database one, from `path_apex`, `apex_path_app`
# and `apex_path_files` (ADT #429). What remains here reads a file's own NAME,
# which no layout key moves.


def _is_apex_set_environment(path: str) -> bool:
    return Path(path).as_posix().endswith("/application/set_environment.sql")


def _is_apex_end_environment(path: str) -> bool:
    return Path(path).as_posix().endswith("/application/end_environment.sql")


def _is_apex_page(path: str) -> bool:
    return _apex_page_id(path) is not None and Path(path).as_posix().endswith(".sql")


def _apex_page_id(path: str) -> int | None:
    match = re.search(r"/pages/page_(\d+)\.sql$", Path(path).as_posix())
    return int(match.group(1)) if match else None


def _snapshot_link(path: str, config: dict[str, Any]) -> str:
    static = _is_apex_static_file(path, config)
    folder = _settings.snapshots_folder(config)
    return f"{folder}/{path}.sql" if static else f"{folder}/{path}"



def _install_targets(root: Path, config: dict[str, Any]) -> list[InstallTarget]:
    """Resolve ``path_objects`` into the folders that own an ``INSTALL.sql``.

    Everything from ``<object_type>`` onward is dropped, that placeholder is the
    per-type level, and the install script sits above it. A ``<schema>`` template
    yields one target per schema folder that exists on disk (the exported tree is
    the source of truth); a template without it yields exactly one.
    """
    template = reject_unresolved_placeholders(
        str(config.get("path_objects") or DEFAULT_PATH_OBJECTS)
    ).strip("/")
    object_type = object_type_token(template) or "<object_type>"
    head, marker, suffix = template.partition(object_type)
    head = head.strip("/")
    suffix = suffix.strip("/") if marker else ""
    # The token carries its own case, so the split has to use the spelling this
    # project configured rather than the lowercase one (ADT #411).
    schema_placeholder = schema_token(head)
    if schema_placeholder is None:
        return [InstallTarget(schema="", root=root / Path(head) if head else root, suffix=suffix)]

    before, _, after = head.partition(schema_placeholder)
    targets: list[InstallTarget] = []
    for candidate in sorted(root.glob(head.replace(schema_placeholder, "*"))):
        if not candidate.is_dir():
            continue
        # The schema is the one path segment `<schema>` stood in for: count the
        # segments the template puts before it and read that part of the match.
        depth = len(Path(before.strip("/")).parts) if before.strip("/") else 0
        relative = candidate.relative_to(root).parts
        if depth >= len(relative):
            continue
        schema = relative[depth]
        if _is_placeholder(schema):
            # A directory literally named `<schema>` is the pre-`#170` bug's
            # droppings, the glob matches it, but it is not a schema.
            continue
        targets.append(
            InstallTarget(schema=schema.upper(), root=candidate, suffix=suffix)
        )
    return targets


def _install_groups(
    target: InstallTarget,
    config: dict[str, Any],
    edges: dict[str, list[str]],
) -> dict[str, list[str]]:
    layouts = _object_layouts(config.get("object_types", {}))
    grouped: dict[str, list[str]] = {}
    for group, object_types in _patch_map(config).items():
        files: list[str] = []
        nodes: dict[str, str] = {}
        for object_type in object_types:
            layout = layouts.get(str(object_type).upper())
            if layout is None:
                continue
            folder, extension = layout
            search_root = target.folder_for(folder)
            if not search_root.exists():
                continue
            # TYPE/TYPE BODY share a folder with one extension a SUFFIX of the
            # other, so the shorter type's glob claims the longer type's files
            # by `patch_map` order (ADT #558). Longest extension wins.
            siblings = extensions_for_folder(layouts, folder) - {extension}
            install_name = _settings.install_script_name(config).upper()
            for file_path in sorted(search_root.rglob(f"*{extension}")):
                # The script this run is about to write is never one of its own
                # inputs, whatever the project calls it.
                if not file_path.is_file() or file_path.name.upper() == install_name:
                    continue
                if not owns_file(extension, siblings, file_path):
                    continue
                relative = file_path.relative_to(target.root).as_posix()
                if relative not in files:
                    files.append(relative)
                    nodes[relative] = _dependency_node(file_path.name, extension, object_type)
        if files:
            grouped[str(group)] = _order_by_dependencies(files, nodes, edges)
    return grouped


def _dependency_node(file_name: str, extension: str, object_type: Any) -> str:
    """``TYPE.NAME`` key matching the dependency mirror's node vocabulary."""
    name = file_name[: -len(extension)] if file_name.endswith(extension) else file_name
    return f"{str(object_type).upper()}.{name.upper()}"


def _dependency_edges(root: Path) -> dict[str, list[str]]:
    """Install-ordering graph from ``config/internal/dependencies.db``.

    Two halves, and both are required for a runnable script: ``uses_edges`` for
    the PL/SQL and view dependencies Oracle records in ``USER_DEPENDENCIES``, and
    ``foreign_key_edges`` for the table-to-table edges it does *not*, a table
    graph built from the first half alone can never be reordered, so every FK
    child would be created before its parent (Jan, 2026-07-31).

    An empty result is not the same as no graph: a schema with no dependencies
    at all is legitimately empty. Whether the graph is present and current is
    settled *before* this is called, by the ``patch.staleness`` gate, nothing
    reaches an ordering action on an absent, unreadable, or stale mirror.
    """
    db_path = internal_path(root, "dependencies.db")
    if not db_path.is_file():
        return {}
    try:
        with DependencyStore.open(db_path) as store:
            edges = {node: list(refs) for node, refs in store.uses_edges().items()}
            for node, refs in store.foreign_key_edges().items():
                edges.setdefault(node, []).extend(refs)
            return edges
    except sqlite3.Error:
        return {}


def _order_by_dependencies(
    files: list[str],
    nodes: dict[str, str],
    edges: dict[str, list[str]],
) -> list[str]:
    """Sort a group so every file an install file needs is executed before it.

    ``patch_map`` already orders the groups coarsely; this refines *within* a
    group off the real graph. Name order is the tie-break, so the result is
    stable, and a dependency cycle degrades to name order for the cycle members
    instead of dropping them.
    """
    if not edges:
        return files
    file_by_node: dict[str, str] = {}
    for path in files:
        node = nodes.get(path)
        if node is not None:
            file_by_node.setdefault(node, path)

    blockers: dict[str, set[str]] = {path: set() for path in files}
    blocked: dict[str, set[str]] = {path: set() for path in files}
    for path in files:
        for referenced in edges.get(nodes.get(path, ""), ()):
            required = file_by_node.get(referenced)
            if required is None or required == path:
                continue
            blockers[path].add(required)
            blocked[required].add(path)

    ready = [path for path in files if not blockers[path]]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        path = heapq.heappop(ready)
        ordered.append(path)
        for dependent in sorted(blocked[path]):
            blockers[dependent].discard(path)
            if not blockers[dependent]:
                heapq.heappush(ready, dependent)
    placed = set(ordered)
    ordered.extend(path for path in files if path not in placed)
    return ordered


def _install_overview(grouped: dict[str, list[str]], config: dict[str, Any]) -> dict[str, int]:
    overview: dict[str, int] = {}
    layouts = _object_layouts(config.get("object_types", {}))
    for files in grouped.values():
        for relative in files:
            object_type = _object_type_for_install_file(relative, layouts)
            if object_type:
                overview[object_type] = overview.get(object_type, 0) + 1
    return overview


def _install_payload(
    grouped: dict[str, list[str]],
    overview: dict[str, int],
    config: dict[str, Any],
) -> str:
    payload = ["--"]
    for object_type in sorted(overview):
        payload.append(f"-- {(object_type + ' ').ljust(36, '.')} {overview[object_type]}")
    payload.extend(["--", "", "--", "-- INIT", "--"])
    for group, files in grouped.items():
        payload.extend(["", "--", f"-- {group.upper()}", "--"])
        payload.extend(_install_file_link(file, config) for file in files)
    payload.extend(["", "--", "-- FINISH", "--", ""])
    return "\n".join(payload)


def _install_file_link(path: str, config: dict[str, Any]) -> str:
    template = str(config.get("patch_file_link") or '@"./#FILE#"')
    line = template.replace("#FILE#", path)
    return line if line.rstrip().endswith(";") else f"{line};"


def _object_type_for_install_file(
    relative: str,
    layouts: dict[str, tuple[str, str]],
) -> str | None:
    """Which configured type the overview counts this install file as.

    Through the shared reader, never the first `layouts` entry the path merely
    ends with: that counted every `types/*.body.sql` as `TYPE` (ADT #558).
    """
    path = PurePosixPath(relative)
    parent = path.parent.as_posix()
    return owning_object_type(path.name, "" if parent == "." else parent, layouts)


def _patch_map(config: dict[str, Any]) -> dict[str, list[str]]:
    raw_map = config.get("patch_map", {})
    if not isinstance(raw_map, dict):
        return {}
    return {
        str(group): [str(object_type) for object_type in object_types]
        for group, object_types in raw_map.items()
        if isinstance(object_types, list | tuple)
    }
