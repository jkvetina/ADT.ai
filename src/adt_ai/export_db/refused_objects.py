"""The objects a windowed export offers again because the database refused them (`#981`).

`#917` lets an export carry on past an object the database refuses and still
stamp the schema's `-recent` watermark, so one refusal cannot cost an hour of
written objects. The refused object's `LAST_DDL_TIME` sits before that stamp,
though, so the next bare `-recent` never listed it again, and its file stayed
missing or stale until somebody happened to edit it. `#923` gave a JOB its retry
through the signature baseline (`job_signatures.py`); this is the same retry for
every other type.

A refusal is remembered per environment and schema in
`config/internal/refused_objects.yaml`, beside `recent.yaml`. Each windowed run
looks the remembered objects up by name, outside its window, and exports them
with the rest; a run that writes one forgets it. An object that no longer exists
is forgotten when the lookup misses it. An unwindowed run lists every object
anyway, so it only records.

`request` is untyped at runtime for the reason `watermarks.py` gives.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from adt_ai.export_db.discovery_filters import (
    ObjectFilters,
    normalize_list,
    normalize_patterns,
)
from adt_ai.export_db.failures import ObjectExportFailure
from adt_ai.export_db.inventory import DatabaseObject, ObjectDiscovery
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping

if TYPE_CHECKING:
    from adt_ai.export_db.request import ExportDbRequest

MODULE = "export_db"

#: `(OBJECT_TYPE, OBJECT_NAME)`, both as the dictionary spells them.
ObjectKey = tuple[str, str]

#: A JOB is retried by its signature baseline instead (`#923`), which also
#: knows when its file is current; holding it here as well would export it twice.
_OWN_RETRY = frozenset({"JOB"})


def refused_objects_path(root: Path) -> Path:
    """`config/internal/refused_objects.yaml`, gitignored beside `recent.yaml`."""
    return internal_path(root, "refused_objects.yaml")


class RefusedObjectStore:
    """Read/modify/write access to the per-(environment, schema) refusal map."""

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        self._data: dict[str, Any] = data or {}

    @classmethod
    def load(cls, root: Path) -> RefusedObjectStore:
        path = refused_objects_path(root)
        data = load_yaml_mapping(path)
        return cls(path, dict(data) if isinstance(data, dict) else {})

    def get(self, environment: str, schema: str) -> list[ObjectKey]:
        """The schema's remembered refusals, sorted; empty when there are none."""
        node: Any = self._data.get(MODULE, {})
        for key in (environment, schema):
            node = node.get(str(key), {}) if isinstance(node, dict) else {}
        if not isinstance(node, dict):
            return []
        return sorted(
            (str(object_type).upper(), str(name))
            for object_type, names in node.items()
            if isinstance(names, list)
            for name in names
        )

    def set(self, environment: str, schema: str, keys: Iterable[ObjectKey]) -> None:
        by_type: dict[str, list[str]] = {}
        for object_type, name in sorted(set(keys)):
            by_type.setdefault(object_type, []).append(name)
        env_node = self._data.setdefault(MODULE, {}).setdefault(str(environment), {})
        if by_type:
            env_node[str(schema)] = by_type
        else:
            env_node.pop(str(schema), None)

    def save(self) -> None:
        store_yaml_mapping(self.path, self._data)


def _key(database_object: DatabaseObject) -> ObjectKey:
    return database_object.object_type.upper(), database_object.name


def retried_objects(
    request: ExportDbRequest,
    schema: str,
    listed: list[DatabaseObject],
    discovery: ObjectDiscovery,
    *,
    object_types: Iterable[str] | None,
    prefix: str | Iterable[str] | None,
    ignore: Iterable[str] | None,
) -> list[DatabaseObject]:
    """The remembered refusals this windowed run adds to its listing.

    Only the ones the run's own filters still select are looked up, so a run
    narrowed to another type neither retries an object nor forgets it. Of those,
    one the lookup no longer finds has been dropped and is forgotten here, since
    there is nothing left to retry; a measured run forgets nothing.
    """
    # `-recent 0` is a window too, the narrowest one, so `None` alone means none.
    if request.recent is None or request.environment is None:
        return []
    store = RefusedObjectStore.load(request.root)
    pending = store.get(request.environment, schema)
    if not pending:
        return []
    filters = ObjectFilters(
        object_types = normalize_list(object_types),
        names        = normalize_list(request.names),
        prefix       = normalize_patterns(prefix),
        ignore       = normalize_list(ignore) or [],
    )
    already = {_key(database_object) for database_object in listed}
    wanted = [
        key for key in pending if key not in already and filters.matches(*key)
    ]
    if not wanted:
        return []
    found = [
        database_object
        for database_object in discovery.discover(
            schema             = schema,
            object_types       = sorted({object_type for object_type, _ in wanted}),
            names              = sorted({name for _, name in wanted}),
            prefer_exact_names = True,
        )
        if _key(database_object) in set(wanted)
    ]
    gone = set(wanted) - {_key(database_object) for database_object in found}
    if gone and not request.baseline:
        store.set(request.environment, schema, set(pending) - gone)
        store.save()
    return found


def record_refusals(
    request: ExportDbRequest,
    schema: str,
    database_objects: list[DatabaseObject],
    refused: list[ObjectExportFailure],
) -> None:
    """Remember what this run was refused and forget what it wrote.

    Called once the schema's files are all written, beside the watermark stamp,
    and never for a measured run, which writes nothing and so was refused
    nothing a later run could still write.
    """
    if request.environment is None:
        return
    refused_keys = {
        _key(failure.database_object)
        for failure in refused
        if failure.database_object.object_type.upper() not in _OWN_RETRY
    }
    written = {_key(database_object) for database_object in database_objects} - refused_keys
    store = RefusedObjectStore.load(request.root)
    pending = set(store.get(request.environment, schema))
    remembered = (pending - written) | refused_keys
    if remembered == pending:
        return
    store.set(request.environment, schema, remembered)
    store.save()
