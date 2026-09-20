"""The policy functions' source for ``recompile -vpd``, read from the export_db files.

``export_db`` already writes every package body and function into the repo, so
the report reads that file rather than the dictionary a second time (ADT #884).
A file is used only when it is current: it exists and was written no earlier than
the object's ``LAST_DDL_TIME``. Anything else is exported first, through
``export_db`` itself and for just those objects, then read.

An exported file whose bytes did not change keeps its old modified time, and a
recompile moves ``LAST_DDL_TIME`` without changing the source, so the file is
stamped with the later of now and that time once exported. Otherwise the same
unchanged object would count as old and be exported again on every run.

A file that is still not current after that, an owner with no configured
connection or an object export_db could not write, names no columns.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from adt_ai.cli.constants import ExportDbRequest, ExportDbRunner, GatewayFactory, QueryGateway
from adt_ai.cli.context import StartupContext
from adt_ai.cli.gateways import build_gateway, debug_wrapped
from adt_ai.export_db.config import _with_default_layout
from adt_ai.export_db.files import ObjectFileError, ObjectFileResolver
from adt_ai.export_db.groups import resolve_group_inputs
from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.recompile.vpd import SourceProvider, VpdSource
from adt_ai.shared.connection_errors import ConnectionNotFoundError


def _path(resolver: ObjectFileResolver, source: VpdSource) -> Path | None:
    try:
        return resolver.path_for(DatabaseObject(source.owner, source.object_type, source.name))
    except ObjectFileError:
        return None


def _changed_at(source: VpdSource) -> int | None:
    # Imported here, not at module scope (ADT #895): `commands_recompile.py`
    # imports this module at module scope, so a release shipping `recompile`
    # without `patch` failed every command on `patch`'s clock reader.
    from adt_ai.patch.clocks import ddl_seconds

    return ddl_seconds(source.ddl_time, source.db_offset) if source.ddl_time else None


def is_current(path: Path | None, source: VpdSource) -> bool:
    """The file exists and was written no earlier than the object last changed."""
    if path is None or not path.is_file():
        return False
    changed_at = _changed_at(source)
    return changed_at is not None and path.stat().st_mtime >= changed_at


def vpd_source_provider(
    startup: StartupContext,
    environment: str,
    gateway_factory: GatewayFactory | None = None,
    *,
    debug: bool = False,
) -> SourceProvider:
    config = _with_default_layout(startup.config)
    group_rules = resolve_group_inputs(startup.config_search_paths)

    def resolver() -> ObjectFileResolver:
        # A fresh one per pass: export can create a file the last one never saw.
        return ObjectFileResolver.from_config(startup.root, config, group_rules)

    def export(owner: str, sources: list[VpdSource]) -> None:
        try:
            connection = startup.connections.resolve(environment=environment, schema=owner)
        except ConnectionNotFoundError:
            return

        def owner_gateway(schema: str) -> QueryGateway:
            gateway = gateway_factory(schema) if gateway_factory else build_gateway(
                startup, connection
            )
            return debug_wrapped(gateway, debug=debug)

        plans = ExportDbRunner(owner_gateway).run(ExportDbRequest(
            root          = startup.root,
            schemas       = [owner],
            config        = config,
            schema_export = {owner: connection.export},
            object_types  = sorted({source.object_type for source in sources}),
            names         = sorted({source.name for source in sources}),
            environment   = environment,
            group_rules   = group_rules,
        ))
        # Only what this export wrote is stamped: an object it skipped, a prefix
        # or ignore rule say, keeps its old file and stays not current.
        written = {
            (plan.object.object_type.upper(), plan.object.name.upper()): plan.path
            for plan in plans
        }
        for source in sources:
            path = written.get((source.object_type, source.name.upper()))
            if path is not None and path.is_file():
                stamp = max(time.time(), float(_changed_at(source) or 0))
                os.utime(path, (stamp, stamp))

    def provide(wanted: list[VpdSource]) -> dict[tuple[str, str], str]:
        paths = resolver()
        # A type the config gives no folder has no file to read or to export into.
        old = [
            source for source in wanted
            if _path(paths, source) is not None and not is_current(_path(paths, source), source)
        ]
        for owner in sorted({source.owner for source in old}):
            export(owner, [source for source in old if source.owner == owner])
        paths = resolver()
        texts: dict[tuple[str, str], str] = {}
        for source in wanted:
            path = _path(paths, source)
            if path is not None and is_current(path, source):
                texts[(source.owner, source.name)] = path.read_text(encoding="utf-8")
        return texts

    return provide
