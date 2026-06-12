from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from adt_ai.export_db.inventory import DatabaseObject


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


@dataclass(frozen=True)
class ObjectWritePlan:
    object  : DatabaseObject
    path    : Path
    action  : Literal["create", "update", "unchanged"]
    dry_run : bool


class ObjectFileResolver:
    def __init__(
        self,
        root: Path,
        path_objects: str | Path,
        object_types: dict[str, ObjectTypeLayout],
        path_template: str | None = None,
        schema_folders: dict[str, str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.path_objects = Path(path_objects)
        self.object_types = {key.upper(): value for key, value in object_types.items()}
        self.path_template = path_template
        self.schema_folders = {
            str(schema): str(folder).strip("/")
            for schema, folder in (schema_folders or {}).items()
            if str(folder).strip("/")
        }
        self._existing_case_paths_by_folder: dict[Path, dict[str, Path]] = {}

    @classmethod
    def from_config(cls, root: Path, config: dict[str, Any]) -> ObjectFileResolver:
        raw_types = config.get("object_types", {})
        if not isinstance(raw_types, dict):
            raise ObjectFileError("object_types must be a mapping")

        return cls(
            root          = root,
            path_objects  = config.get("path_objects", "database"),
            path_template = config.get("path_template"),
            schema_folders = _parse_schema_folders(config.get("schema_folders", {})),
            object_types={
                object_type: _parse_layout(object_type, raw_layout)
                for object_type, raw_layout in raw_types.items()
            },
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
        return self._existing_case_path(folder, filename) or folder / filename

    def missing_files(self, database_objects: list[DatabaseObject]) -> list[Path]:
        expected = {self.path_for(database_object).resolve() for database_object in database_objects}
        existing: set[Path] = set()
        for layout in self.object_types.values():
            search_root = (
                self.root
                if self.path_template
                else self.root / self.path_objects / layout.folder
            )
            existing.update(
                file_path.resolve()
                for file_path in search_root.rglob(f"*{layout.extension}")
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
                    if not self._is_best_layout_for_file(object_type, layout, file_path):
                        continue
                    if file_path.resolve() in expected:
                        continue
                    missing.append(
                        DatabaseObject(
                            schema or (schemas[0] if schemas else ""),
                            object_type,
                            _object_name_from_file(file_path, layout),
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

    def _folder_for(self, database_object: DatabaseObject, layout: ObjectTypeLayout) -> Path:
        if not self.path_template:
            return self.root / self.path_objects / layout.folder

        rendered = (
            self.path_template
            .replace("<schema>", self._schema_folder(database_object.schema).lower())
            .replace("<object_type>", layout.folder)
        )
        return self.root / Path(rendered.strip("/"))

    def _schema_folder(self, schema: str) -> str:
        return self.schema_folders.get(schema, schema)

    def _search_roots_for(
        self,
        object_type: str,
        layout: ObjectTypeLayout,
        schemas: list[str],
    ) -> list[Path]:
        if not self.path_template:
            return [self.root / self.path_objects / layout.folder]
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
        if not file_path.name.endswith(layout.extension):
            return False
        for candidate_type, candidate_layout in self.object_types.items():
            if candidate_type == object_type or candidate_layout.folder != layout.folder:
                continue
            if len(candidate_layout.extension) <= len(layout.extension):
                continue
            if file_path.name.endswith(candidate_layout.extension):
                return False
        return True

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

    def plan(self, requests: list[ObjectWriteRequest], dry_run: bool = True) -> list[ObjectWritePlan]:
        return [
            self._plan_one(request, dry_run=dry_run, compare_existing=True)
            for request in requests
        ]

    def write(self, requests: list[ObjectWriteRequest]) -> list[ObjectWritePlan]:
        return [self.write_one(request) for request in requests]

    def write_one(self, request: ObjectWriteRequest) -> ObjectWritePlan:
        plan = self._plan_one(
            request,
            dry_run=False,
            compare_existing=self.compare_existing,
        )
        if plan.action == "unchanged":
            return plan
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        plan.path.write_text(request.content, encoding="utf-8")
        return plan

    def _plan_one(
        self,
        request: ObjectWriteRequest,
        dry_run: bool,
        compare_existing: bool,
    ) -> ObjectWritePlan:
        path = self.resolver.path_for(request.object)
        if not path.exists():
            action: Literal["create", "update", "unchanged"] = "create"
        elif compare_existing and path.read_text(encoding="utf-8") == request.content:
            action = "unchanged"
        else:
            action = "update"

        return ObjectWritePlan(
            object  = request.object,
            path    = path,
            action  = action,
            dry_run = dry_run,
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


def _parse_schema_folders(raw_folders: Any) -> dict[str, str]:
    if not isinstance(raw_folders, dict):
        return {}
    return {
        str(schema): str(folder).strip("/")
        for schema, folder in raw_folders.items()
        if str(folder).strip("/")
    }


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


def _object_name_from_file(file_path: Path, layout: ObjectTypeLayout) -> str:
    name = file_path.name
    if name.endswith(layout.extension):
        name = name[: -len(layout.extension)]
    return name.upper()
