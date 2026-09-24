from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_db.groups import GroupRules, group_for, object_name_from_file, owns_file
from adt_ai.export_db.inventory import DatabaseObject

# Moved to `writer.py` by `#923`, re-exported for every import from here.
from adt_ai.export_db.writer import ObjectFileWriter as ObjectFileWriter
from adt_ai.export_db.writer import ObjectWritePlan as ObjectWritePlan
from adt_ai.export_db.writer import ObjectWriteRequest as ObjectWriteRequest
from adt_ai.shared.config import DEFAULT_PATH_OBJECTS, reject_unresolved_placeholders
from adt_ai.shared.object_files import object_stem_for_type
from adt_ai.shared.path_template import (
    object_type_token,
    render_path_template,
    schema_token,
)
from adt_ai.shared.safe_paths import (
    UnsafePathError,
    simple_component,
    simple_oracle_identifier,
    simple_relative_path,
    under_root,
)


class ObjectFileError(Exception):
    """Raised when database object file mapping cannot be resolved."""


@dataclass(frozen=True)
class ObjectTypeLayout:
    folder    : str
    extension : str


class ObjectFileResolver:
    def __init__(
        self,
        root: Path,
        path_objects: str | Path,
        object_types: dict[str, ObjectTypeLayout],
        group_rules: GroupRules | None = None,
    ) -> None:
        self.root = Path(root)
        self.group_rules = group_rules
        # path_objects is a path template; it may contain <schema> (or <SCHEMA>,
        # which renders the schema uppercase) and <object_type>. When
        # <object_type> is omitted the per-type folder is appended automatically
        # (legacy 'database/' layout). A token nothing substitutes is rejected at
        # construction, before the export can write a folder named after it.
        self.path_objects = reject_unresolved_placeholders(str(path_objects))
        self.object_types = {key.upper(): value for key, value in object_types.items()}
        self._existing_case_paths_by_folder: dict[Path, dict[str, Path]] = {}
        self._duplicate_paths_by_folder: dict[Path, dict[str, list[Path]]] = {}
        # What `missing_objects` found, the files the delete removes (`#923`).
        self._missing_paths: dict[DatabaseObject, list[Path]] = {}

    @classmethod
    def from_config(
        cls,
        root: Path,
        config: dict[str, Any],
        group_rules: GroupRules | None = None,
    ) -> ObjectFileResolver:
        raw_types = config.get("object_types", {})
        if not isinstance(raw_types, dict):
            raise ObjectFileError("object_types must be a mapping")

        return cls(
            root          = root,
            path_objects  = config.get("path_objects", DEFAULT_PATH_OBJECTS),
            object_types={
                object_type: _parse_layout(object_type, raw_layout)
                for object_type, raw_layout in raw_types.items()
            },
            group_rules = group_rules,
        )

    def path_for(self, database_object: DatabaseObject) -> Path:
        object_type = database_object.object_type.upper()
        layout = self.object_types.get(object_type)
        if layout is None:
            raise ObjectFileError(f"Object type is not configured: {database_object.object_type}")

        try:
            simple_oracle_identifier(database_object.schema, role="schema name")
            if object_type == "GRANT" and "/" in database_object.name:
                category, owner, *extra = database_object.name.split("/")
                if category.lower() != "received" or not owner or extra:
                    raise UnsafePathError(
                        f"Unsupported GRANT artifact name {database_object.name!r}"
                    )
                simple_oracle_identifier(owner, role="grant owner name")
            else:
                simple_oracle_identifier(database_object.name, role="database object name")
        except UnsafePathError as error:
            raise ObjectFileError(str(error)) from error
        folder = self._folder_for(database_object, layout)
        object_name = (
            database_object.name
            if object_type == "GRANT"
            else database_object.name.lower()
        )
        filename = f"{object_name}{layout.extension}"
        # Honour an already-exported file wherever it sits (including a group
        # subfolder a user arranged by hand) before routing a brand-new object.
        existing = self._existing_case_path(folder, filename)
        if existing is not None:
            try:
                return under_root(self.root, existing, role="database object path")
            except UnsafePathError as error:
                raise ObjectFileError(str(error)) from error
        try:
            group = (
                None
                if object_type == "GRANT"
                else group_for(object_type, database_object.name, self.group_rules)
            )
            target = (
                folder / simple_component(group, role="group name") / filename
                if group
                else folder / filename
            )
            return under_root(self.root, target, role="database object path")
        except UnsafePathError as error:
            raise ObjectFileError(str(error)) from error

    def fix_path_for(self, database_object: DatabaseObject) -> Path:
        path = self.path_for(database_object)
        return path.with_name(f"{path.stem}.fix{path.suffix}")

    def checked_path(self, database_object: DatabaseObject, path: Path | None = None) -> Path:
        """`path`, or the object's own file, refused when it leaves the project.

        The check `ObjectFileWriter` makes before every compare, write or hash,
        kept here so a mapping error is always this module's to raise.
        """
        try:
            return under_root(
                self.root,
                path or self.path_for(database_object),
                role = "database object path",
            )
        except UnsafePathError as error:
            raise ObjectFileError(str(error)) from error

    def file_object_name(self, database_object: DatabaseObject) -> str:
        """The object's name spelled the way its own FILE spells it.

        `path_for` already honours a user's rename (`_existing_case_path`), so
        this reads that answer back off the resolved path, and every generated
        spelling of the name INSIDE that file follows it (`#679`). A file that
        does not exist yet resolves to the lowercase default, which is why a
        brand-new object still exports lowercase with no special case here.

        Routed through `object_stem_for_type`, never `Path.stem`: the
        `.spec.sql`/`.sql` pair sharing one folder means `Core_Util.spec.sql`
        has the stem `Core_Util.spec`, and rendering THAT into the DDL would
        rename the object. The shared reader strips the type's own extension.
        """
        object_type = database_object.object_type.upper()
        # `path_for` is what rejects an unconfigured object type, so resolving
        # the path FIRST leaves the layout lookup below unable to miss and this
        # method with one way to fail rather than two.
        path = self.path_for(database_object)
        layout = self.object_types[object_type]
        stem = object_stem_for_type(
            path.name,
            object_type,
            {object_type: (layout.folder, layout.extension)},
        )
        return stem or database_object.name.lower()

    def missing_objects(
        self,
        database_objects: list[DatabaseObject],
        schema: str | None = None,
    ) -> list[DatabaseObject]:
        expected = {
            self.path_for(database_object).resolve()
            for database_object in database_objects
        }
        missing: list[DatabaseObject] = []
        schemas = sorted({database_object.schema for database_object in database_objects})
        if not schemas and schema:
            schemas = [schema]
        for object_type, layout in self.object_types.items():
            if object_type in {"DATA", "GRANT"}:
                continue
            for search_root in self._search_roots_for(object_type, layout, schemas):
                if not search_root.exists():
                    continue
                for file_path in search_root.rglob(f"*{layout.extension}"):
                    if file_path.name.endswith(f".fix{layout.extension}"):
                        continue
                    if not self._is_best_layout_for_file(object_type, layout, file_path):
                        continue
                    if file_path.resolve() in expected:
                        continue
                    found = DatabaseObject(
                        schema or (schemas[0] if schemas else ""),
                        object_type,
                        object_name_from_file(file_path, layout.extension),
                    )
                    self._missing_paths.setdefault(found, []).append(file_path)
                    missing.append(found)
        return sorted(missing, key=lambda item: (item.object_type, item.name))

    def delete_missing_objects(self, database_objects: list[DatabaseObject]) -> list[Path]:
        """Unlink the files `missing_objects` found, never the name resolved again.

        Resolving it again answered the LIVE file whenever a stale clone shared its
        filename: the clone `tables/old/orders.sql` is what is missing, and
        `path_for(ORDERS)` is `tables/orders.sql`, so the sweep deleted the file
        the export was about to write and a refused ORDERS (`#917`) stayed deleted.
        """
        deleted: list[Path] = []
        for database_object in database_objects:
            for file_path in self._missing_paths.pop(database_object, []):
                if file_path.exists():
                    file_path.unlink()
                    deleted.append(file_path)
        return deleted

    def delete_configured_object_files(self, schema: str) -> list[Path]:
        deleted: list[Path] = []
        for object_type, layout in self.object_types.items():
            if object_type == "DATA":
                continue
            for search_root in self._search_roots_for(object_type, layout, [schema]):
                if not search_root.exists():
                    continue
                for file_path in sorted(search_root.glob(f"*{layout.extension}")):
                    if not file_path.is_file():
                        continue
                    file_path.unlink()
                    deleted.append(file_path)
        return deleted

    def iter_type_roots(
        self, schemas: list[str]
    ) -> list[tuple[str, Path, str]]:
        """List `(object_type, folder, extension)` per exportable object type.

        Used by group detection to scan existing files. DATA and GRANT are skipped
        since they do not participate in prefix grouping.
        """
        roots: list[tuple[str, Path, str]] = []
        seen: set[tuple[str, Path]] = set()
        for object_type, layout in self.object_types.items():
            if object_type in {"DATA", "GRANT"}:
                continue
            for folder in self._search_roots_for(object_type, layout, schemas):
                key = (object_type, folder)
                if key in seen:
                    continue
                seen.add(key)
                roots.append((object_type, folder, layout.extension))
        return roots

    def duplicate_locations(self, database_object: DatabaseObject) -> list[Path]:
        """Every existing file sharing this object's filename inside its type subtree.

        Empty unless the same filename really does sit in more than one place,
        the stale-clone case. ``_existing_case_path`` silently keeps the first
        match and exports there, so the other copies drift; the export surfaces
        them inline with a ``[DUPE]`` marker rather than aborting the run.
        The scan is per schema subtree, so the same object name exported from
        two schemas is not a collision.
        """
        object_type = database_object.object_type.upper()
        if object_type in {"DATA", "GRANT"}:
            return []
        layout = self.object_types.get(object_type)
        if layout is None:
            return []
        folder = self._folder_for(database_object, layout)
        filename = f"{database_object.name.lower()}{layout.extension}"
        return self._duplicate_paths(folder, layout.extension).get(filename, [])

    def display_path(self, path: Path) -> str:
        """Root-relative path with the leading ``database/`` segment dropped."""
        try:
            relative = Path(path).resolve().relative_to(Path(self.root).resolve())
        except (OSError, ValueError):
            return Path(path).as_posix()
        parts = relative.parts
        if parts and parts[0].lower() == "database":
            parts = parts[1:]
        return "/".join(parts)

    def flat_object_names(self, schemas: list[str]) -> dict[str, list[str]]:
        """Object names of files sitting directly in each type folder (not grouped)."""
        names_by_type: dict[str, list[str]] = {}
        for object_type, file_path in self.object_files(schemas, grouped=False):
            names_by_type.setdefault(object_type, []).append(
                object_name_from_file(file_path, self.object_types[object_type].extension)
            )
        return names_by_type

    def object_files(
        self, schemas: list[str], *, grouped: bool = True
    ) -> list[tuple[str, Path]]:
        """Every exported object file of ``schemas``, as ``(object_type, path)``.

        Each type folder, and with ``grouped`` the `-groups` sub-folders one
        level under it. `.fix` sidecars, DATA and GRANT are skipped, and a
        shared folder answers by the longest extension. `search TERM` reads the
        DB layer through this (ADT #904).
        """
        found: list[tuple[str, Path]] = []
        type_roots = self.iter_type_roots(schemas)
        type_folders = {folder for _object_type, folder, _extension in type_roots}
        for object_type, folder, extension in type_roots:
            if not folder.is_dir():
                continue
            layout = self.object_types[object_type]
            candidates = sorted(folder.glob(f"*{extension}"))
            if grouped:
                candidates += sorted(
                    file_path
                    for group in sorted(folder.iterdir())
                    if group.is_dir() and group not in type_folders
                    for file_path in group.glob(f"*{extension}")
                )
            found.extend(
                (object_type, file_path)
                for file_path in candidates
                if file_path.is_file()
                and not file_path.name.endswith(f".fix{extension}")
                and self._is_best_layout_for_file(object_type, layout, file_path)
            )
        return found

    def _folder_for(self, database_object: DatabaseObject, layout: ObjectTypeLayout) -> Path:
        rendered = render_path_template(
            self.path_objects,
            schema      = database_object.schema,
            object_type = layout.folder,
        )
        try:
            relative = simple_relative_path(rendered, role="path_objects")
            if not object_type_token(self.path_objects):
                relative /= simple_relative_path(layout.folder, role="object type folder")
            return under_root(self.root, self.root / relative, role="object type folder")
        except UnsafePathError as error:
            raise ObjectFileError(str(error)) from error

    def _search_roots_for(
        self,
        object_type: str,
        layout: ObjectTypeLayout,
        schemas: list[str],
    ) -> list[Path]:
        if schema_token(self.path_objects) is None:
            return [self._folder_for(DatabaseObject("", object_type, "scan"), layout)]
        return [
            self._folder_for(DatabaseObject(schema, object_type, "scan"), layout)
            for schema in schemas
        ] or [self.root]

    def _is_best_layout_for_file(
        self,
        object_type: str,
        layout: ObjectTypeLayout,
        file_path: Path,
    ) -> bool:
        """Does this file belong to `object_type`, or to another type on that folder?

        The rule itself lives in `groups.owns_file` because the `-groups` move action
        needs the same answer and cannot import this module (`files` imports `groups`,
        never the other way). One rule, one spelling.
        """
        return owns_file(
            layout.extension,
            [
                candidate_layout.extension
                for candidate_type, candidate_layout in self.object_types.items()
                if candidate_type != object_type and candidate_layout.folder == layout.folder
            ],
            file_path,
        )

    def _duplicate_paths(self, folder: Path, extension: str) -> dict[str, list[Path]]:
        cache = self._duplicate_paths_by_folder.get(folder)
        if cache is None:
            cache = _duplicate_case_paths(folder, extension)
            self._duplicate_paths_by_folder[folder] = cache
        return cache

    def _existing_case_path(self, folder: Path, filename: str) -> Path | None:
        cache = self._existing_case_paths_by_folder.get(folder)
        if cache is None:
            cache = _existing_case_paths(folder)
            self._existing_case_paths_by_folder[folder] = cache
        filename_lower = filename.lower()
        path = cache.get(filename_lower)
        if path is not None:
            return path
        cache[filename_lower] = folder / filename
        return None


def _parse_layout(object_type: str, raw_layout: Any) -> ObjectTypeLayout:
    if isinstance(raw_layout, dict):
        folder = raw_layout.get("folder")
        extension = raw_layout.get("extension")
    elif isinstance(raw_layout, list | tuple) and len(raw_layout) == 2:
        folder, extension = raw_layout
    else:
        raise ObjectFileError(f"Invalid file layout for object type: {object_type}")

    if not isinstance(folder, str) or not isinstance(extension, str):
        raise ObjectFileError(f"Invalid file layout for object type: {object_type}")

    try:
        safe_folder = simple_relative_path(folder, role=f"{object_type} folder").as_posix()
    except UnsafePathError as error:
        raise ObjectFileError(str(error)) from error
    if not re.fullmatch(r"(?:\.[A-Za-z0-9_-]+)+", extension):
        raise ObjectFileError(
            f"Invalid file extension for object type {object_type}: {extension!r}"
        )
    return ObjectTypeLayout(folder=safe_folder, extension=extension)


def _duplicate_case_paths(folder: Path, extension: str) -> dict[str, list[Path]]:
    """Filenames present more than once under `folder`, case-insensitively.

    Keyed by lowercased filename, so a clone that differs only in casing still
    counts. `.fix` sidecars sit beside their object file by design and are not
    duplicates.
    """
    found: dict[str, list[Path]] = {}
    for path in sorted(folder.rglob(f"*{extension}")) if folder.exists() else []:
        if not path.is_file() or path.name.endswith(f".fix{extension}"):
            continue
        found.setdefault(path.name.lower(), []).append(path)
    return {name: paths for name, paths in found.items() if len(paths) > 1}


def _existing_case_paths(folder: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if not folder.exists():
        return paths
    for existing in folder.iterdir():
        if existing.is_file():
            paths.setdefault(existing.name.lower(), existing)
    for existing in sorted(folder.rglob("*")):
        if existing.is_file():
            paths.setdefault(existing.name.lower(), existing)
    return paths
