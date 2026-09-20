"""`diff -restore`: the target's version of what differs, where the source keeps it (#893).

Jan, 2026-09-19: *"Add a new flag ... for all types (db objects, data, rest,
apex) and if this is passed, you will resurrect these target versions into
their location in current/requested branch, so user can see all the changes
there!"* The project checkout tracks the SOURCE, so a pulled file lands at the
path the source's own export writes it to, and `git diff` then reads as the
move from the source's version to the target's.

**Nothing here writes a format of its own.** Each mode hands the differing
items to the exporter that owns them, run against the TARGET connection:
`export_db` for objects and grants, `export_data` for table rows,
`export_apex`'s REST writer for modules, and `export_apex` itself for
applications (`diff/pull_apex`). What they write is byte for byte what the
matching export command would commit, so a pulled file and an exported one
never disagree about a comma.

* `CHANGED` and `EXTRA` write the target's version; `MISSING`, which only the
  source has, deletes the file at the source's location.
* A target schema spelled differently from the source is still written under
  the SOURCE's name: `_SchemaAlias` answers each question the exporter asks of
  the source schema from the target's. `export_db` strips a DDL's own owner
  (`owner_qualifier_stripper`), so the target's name never reaches a file
  through it.
* `-restore` writes everything the screen reports, whatever `-limit` cut from the
  listings; `-type`, `-name` and `-page` narrow it, as they narrow the screen.
"""
from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.diff.data import DataDiff
from adt_ai.diff.inventory import MISSING
from adt_ai.diff.rest import REST_MODULE_TYPE
from adt_ai.diff.summary import DiffChange, DiffSummary
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.rest import RestExport
from adt_ai.export_data.groups import resolve_data_group_rules
from adt_ai.export_data.runner import (
    ExportDataRequest,
    ExportDataRunner,
    _data_folder,
    _data_path,
)
from adt_ai.export_db.config import _configured_object_types, _with_default_layout
from adt_ai.export_db.files import ObjectFileError, ObjectFileResolver
from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.export_db.request import ExportDbRequest
from adt_ai.export_db.runner import ExportDbRunner
from adt_ai.shared import text_files
from adt_ai.shared.apex_paths import REST_SCHEMA_DEFINITION
from adt_ai.shared.connections import Connection
from adt_ai.shared.db import QueryGateway

#: The comparison's type where `export_db` files the object under another. SQLcl
#: names a package's specification `PACKAGE SPEC`; `export_db` calls it `PACKAGE`.
_EXPORT_TYPES = {"PACKAGE SPEC": "PACKAGE", "TYPE SPEC": "TYPE"}


@dataclass(frozen=True)
class PullSides:
    """Where a pull writes and whom it asks: the checkout, both sides, the target's session."""

    root    : Path
    config  : Mapping[str, object]
    source  : Connection
    target  : Connection
    gateway : QueryGateway

    @property
    def target_gateway(self) -> QueryGateway:
        """The target's session, asked about the source schema by the source's name."""
        if self.source.schema.upper() == self.target.schema.upper():
            return self.gateway
        return _SchemaAlias(self.gateway, self.source.schema, self.target.schema)


class _SchemaAlias:
    """A gateway that reads the target schema wherever a bind names the source one.

    The exporters bind the schema they write for as `:schema`, and that name is
    also the one their paths and file names are built from. Answering it from
    the target is what lets `export_db` write the target's objects under the
    source's folders without a second code path.
    """

    def __init__(self, gateway: QueryGateway, source: str, target: str) -> None:
        self._gateway = gateway
        self._source  = source.upper()
        self._target  = target

    def _params(self, params: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        if params is None or str(params.get("schema", "")).upper() != self._source:
            return params
        return {**params, "schema": self._target}

    def fetch_all(
        self, sql: str, params: Mapping[str, Any] | None = None, exact_numbers: bool = False
    ) -> list[dict[str, Any]]:
        return self._gateway.fetch_all(sql, self._params(params), exact_numbers)

    def read_only_fetch_all(
        self, sql: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        return self._gateway.read_only_fetch_all(sql, self._params(params))

    def execute(self, sql: str, params: Mapping[str, Any] | None = None) -> None:
        self._gateway.execute(sql, self._params(params))

    def fetch_clob(self, sql: str, params: Mapping[str, Any] | None = None) -> str:
        return self._gateway.fetch_clob(sql, self._params(params))

    def sqlcl_request(
        self,
        request: str,
        root: Path,
        timeout_seconds: float | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> str:
        return self._gateway.sqlcl_request(request, root, timeout_seconds, on_line)

    def close(self) -> None:
        self._gateway.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._gateway, name)


def pull_objects(sides: PullSides, summary: DiffSummary) -> None:
    """The target's DDL for each differing object, and its grants when they differ.

    One `export_db` run narrowed to the exact names, so it writes those objects
    and nothing else; a narrowed run deletes nothing and advances no watermark.
    Grants are whole files per schema, so they are rewritten only when a grant
    differs: rewriting them otherwise would move the target's grant files onto
    a pull about one package.
    """
    config = _with_default_layout(dict(sides.config))
    names = sorted({change.object_name for change in summary.changes if change.status != MISSING})
    grants = ["GRANT"] if summary.grants else []
    if names or grants:
        gateway = sides.target_gateway
        ExportDbRunner(lambda _schema: gateway).run(
            ExportDbRequest(
                root          = sides.root,
                schemas       = [sides.source.schema],
                config        = config,
                schema_export = {sides.source.schema: sides.target.export},
                object_types  = [*(_configured_object_types(config) if names else []), *grants],
                names         = names or None,
                environment   = sides.target.environment,
            )
        )
    resolver = ObjectFileResolver.from_config(root=sides.root, config=config)
    for change in summary.changes:
        if change.status == MISSING:
            _delete_object(resolver, sides.source.schema, change)


def _delete_object(resolver: ObjectFileResolver, schema: str, change: DiffChange) -> None:
    """The source's file for an object the target lacks, and a table's `.fix` beside it.

    A type `export_db` keeps inside another object's file (a comment, a
    constraint) has no file of its own to delete, and neither has a type the
    project does not export.
    """
    database_object = DatabaseObject(
        schema, _EXPORT_TYPES.get(change.object_type, change.object_type), change.object_name
    )
    try:
        paths = [resolver.path_for(database_object), resolver.fix_path_for(database_object)]
    except ObjectFileError:
        return
    for path in paths:
        path.unlink(missing_ok=True)


def pull_data(sides: PullSides, result: DataDiff) -> None:
    """Each table with a differing row, exported from the target whole.

    A table the target does not have is not written by the export, so its
    source files, the CSV, its merge script and its LOB folder, are deleted.
    """
    names = [table.name for table in result.changed_tables]
    if not names:
        return
    config = dict(sides.config)
    schema = sides.source.schema
    gateway = sides.target_gateway
    written = set(
        ExportDataRunner(lambda _schema: gateway).run(
            ExportDataRequest(
                root          = sides.root,
                schemas       = [schema],
                config        = config,
                schema_export = {schema: sides.target.export},
                names         = names,
            )
        )
    )
    rules = resolve_data_group_rules(_data_folder(sides.root, config, schema), None)
    for name in names:
        path = _data_path(sides.root, config, name, schema, rules)
        if path in written:
            continue
        path.unlink(missing_ok=True)
        path.with_suffix(".sql").unlink(missing_ok=True)
        shutil.rmtree(path.with_suffix(""), ignore_errors=True)


def pull_rest(sides: PullSides, summary: DiffSummary, export: RestExport) -> None:
    """The target's text for each differing module, and its schema definition.

    `export` is the target's own `rest export`, the one the comparison already
    ran, written through `export_apex -rest`'s paths. A privilege or role lives
    in the schema definition file, so either one differing rewrites it.
    """
    resolver = ApexFileResolver.from_config(sides.root, dict(sides.config))
    resolver = resolver.for_schema(sides.source.schema)
    definition = False
    for change in summary.changes:
        if change.object_type == REST_MODULE_TYPE:
            _write(resolver.rest_export(change.object_name), export.modules.get(change.object_name))
        else:
            definition = True
    if definition:
        _write(resolver.rest_export(REST_SCHEMA_DEFINITION), export.schema_definition)


def _write(path: Path, text: str | None) -> None:
    """`text` at `path`, or no file at all when the target has none."""
    if text is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    text_files.write_text(path, text)


__all__ = [name for name in globals() if not name.startswith("_")]
