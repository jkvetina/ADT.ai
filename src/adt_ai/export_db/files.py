from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from adt_ai.export_db.groups import GroupRules, group_for, object_name_from_file, owns_file
from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.shared import text_files
from adt_ai.shared.config import DEFAULT_PATH_OBJECTS, reject_unresolved_placeholders
from adt_ai.shared.git_files import file_payload_hash
from adt_ai.shared.path_template import (
    object_type_token,
    render_path_template,
    schema_token,
)


class ObjectFileError(Exception):
    """Raised when database object file mapping cannot be resolved."""


@dataclass(frozen=True)
class ObjectTypeLayout:
    folder    : str
    extension : str


@dataclass(frozen=True)
class ObjectWriteRequest:
    object  : DatabaseObject
    content : str
    path    : Path | None = None


@dataclass(frozen=True)
class ObjectWritePlan:
    object  : DatabaseObject
    path    : Path
    action  : Literal["create", "update", "unchanged", "hashed"]
    #: Set only by :meth:`ObjectFileWriter.hash_one` (`#452`): the hash of the
    #: bytes this object WOULD have been written as, so a baseline can be
    #: measured off a live database without touching the working tree.
    content_hash: str | None = None


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
            return existing
        group = group_for(object_type, database_object.name, self.group_rules)
        if group:
            return folder / group / filename
        return folder / filename

    def fix_path_for(self, database_object: DatabaseObject) -> Path:
        path = self.path_for(database_object)
        return path.with_name(f"{path.stem}.fix{path.suffix}")

    def missing_files(self, database_objects: list[DatabaseObject]) -> list[Path]:
        expected = {
            self.path_for(database_object).resolve() for database_object in database_objects
        }
        existing: set[Path] = set()
        schemas = sorted({database_object.schema for database_object in database_objects})
        for object_type, layout in self.object_types.items():
            for search_root in self._search_roots_for(object_type, layout, schemas):
                if not search_root.exists():
                    continue
                existing.update(
                    file_path.resolve()
                    for file_path in search_root.rglob(f"*{layout.extension}")
                    if not file_path.name.endswith(f".fix{layout.extension}")
                )
        return sorted(existing - expected)

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
                    missing.append(
                        DatabaseObject(
                            schema or (schemas[0] if schemas else ""),
                            object_type,
                            object_name_from_file(file_path, layout.extension),
                        )
                    )
        return sorted(missing, key=lambda item: (item.object_type, item.name))

    def delete_missing_files(self, database_objects: list[DatabaseObject]) -> list[Path]:
        missing = self.missing_files(database_objects)
        for file_path in missing:
            file_path.unlink()
        return missing

    def delete_missing_objects(self, database_objects: list[DatabaseObject]) -> list[Path]:
        deleted: list[Path] = []
        for database_object in database_objects:
            file_path = self.path_for(database_object)
            if not file_path.exists():
                continue
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
        for object_type, layout in self.object_types.items():
            if object_type in {"DATA", "GRANT"}:
                continue
            for folder in self._search_roots_for(object_type, layout, schemas):
                if not folder.is_dir():
                    continue
                for file_path in sorted(folder.glob(f"*{layout.extension}")):
                    if not file_path.is_file():
                        continue
                    if file_path.name.endswith(f".fix{layout.extension}"):
                        continue
                    if not self._is_best_layout_for_file(object_type, layout, file_path):
                        continue
                    names_by_type.setdefault(object_type, []).append(
                        object_name_from_file(file_path, layout.extension)
                    )
        return names_by_type

    def _folder_for(self, database_object: DatabaseObject, layout: ObjectTypeLayout) -> Path:
        rendered = render_path_template(
            self.path_objects,
            schema      = database_object.schema,
            object_type = layout.folder,
        )
        if object_type_token(self.path_objects):
            return self.root / Path(rendered.strip("/"))
        return self.root / Path(rendered.strip("/")) / layout.folder

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


class ObjectFileWriter:
    def __init__(self, resolver: ObjectFileResolver, compare_existing: bool = True) -> None:
        self.resolver = resolver
        self.compare_existing = compare_existing

    def write(self, requests: list[ObjectWriteRequest]) -> list[ObjectWritePlan]:
        return [self.write_one(request) for request in requests]

    def differs_from_disk(self, request: ObjectWriteRequest) -> bool:
        """Is the file this request targets absent, or holding other content?

        The comparison :meth:`write_one` makes under ``compare_existing``, asked
        without writing anything. ``compare_existing`` itself is deliberately not
        consulted: that flag says whether an unchanged file may skip its WRITE,
        and this is a question about the content either way.

        `export_db` asks it before printing the `GRANT` overview row, which
        exists to say those artifacts moved and must not claim a run that
        rewrites the same bytes (`#437`).
        """
        return self._plan_one(request, compare_existing=True).action != "unchanged"

    def write_one(self, request: ObjectWriteRequest) -> ObjectWritePlan:
        plan = self._plan_one(request, compare_existing=self.compare_existing)
        if plan.action == "unchanged":
            return plan
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_text(plan.path, request.content)
        return plan

    def hash_one(self, request: ObjectWriteRequest) -> ObjectWritePlan:
        """What this object would hash to, without writing anything (`#452`).

        The path is resolved exactly as a write would resolve it, so a measured
        baseline is keyed the same way the working tree is and the two are
        directly comparable.

        The bytes are the bytes `write_text` would have produced, which is why
        the configured line ending is applied here rather than hashing the raw
        DDL string: `file_payload_hash` canonicalizes line endings (`#454`), so
        this would agree either way, and pinning it to the writer's own output
        keeps that agreement a property of the code rather than a coincidence.

        Jan, 2026-08-21: *"when patch calculate the hash of the file, it must be
        the same as the hash calculated in export_db -baseline mode."*
        """
        path = request.path or self.resolver.path_for(request.object)
        rendered = text_files.normalize(request.content).replace(
            "\n", text_files.configured_newline()
        )
        return ObjectWritePlan(
            object       = request.object,
            path         = path,
            action       = "hashed",
            content_hash = file_payload_hash(rendered.encode("utf-8")),
        )

    def _plan_one(
        self,
        request: ObjectWriteRequest,
        compare_existing: bool,
    ) -> ObjectWritePlan:
        path = request.path or self.resolver.path_for(request.object)
        if not path.exists():
            action: Literal["create", "update", "unchanged"] = "create"
        elif compare_existing and path.read_text(encoding="utf-8") == text_files.normalize(
            request.content
        ):
            # read_text() reads with universal newlines, so the file side is always
            # LF; the DDL side has to be normalized the same way the writer does or
            # every CRLF-carrying object reports as changed on every export.
            action = "unchanged"
        else:
            action = "update"

        return ObjectWritePlan(
            object = request.object,
            path   = path,
            action = action,
        )


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

    return ObjectTypeLayout(folder=folder.strip("/"), extension=extension)


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
