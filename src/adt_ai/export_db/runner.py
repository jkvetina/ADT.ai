from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved helpers.
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_db.config import (
    _audit_config,
    _cached_gateway_factory,
    _configured_object_types,
    _has_runtime_filter,
    _requested_object_type_matches,
    _split_patterns,
    _with_default_layout,
)
from adt_ai.export_db.content import (
    _append_comments,
    _append_job_arguments,
    _comment_query_object_types,
    _has_column_comments,
    _has_comments,
    _ignored_comment_columns,
    _render_directories,
    _render_grants_made,
    _render_grants_received,
    _render_user_privileges,
)
from adt_ai.export_db.files import (
    ObjectFileResolver,
    ObjectFileWriter,
    ObjectWritePlan,
    ObjectWriteRequest,
)
from adt_ai.export_db.groups import (
    GroupRules,
    detect_groups_from_tree,
)
from adt_ai.export_db.inventory import (
    DatabaseObject,
    ObjectDiscovery,
    has_exact_name_filter,
)
from adt_ai.export_db.normalizers import (
    NormalizerRegistry,
    build_table_fix_sql,
    normalize_ddl,
)
from adt_ai.export_db.render import (
    ConsoleExportDbReporter,
    ExportDbReporter,
    _AdtTableLayout,
    _commit_stdout,
    _compute_adt_layout,
    print_adt_header,
    print_adt_pipes,
    print_adt_table,
)
from adt_ai.shared.config import is_enabled
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.recent_state import (
    RecentStore,
    is_bare_recent,
    may_advance,
    read_db_now,
    recent_days,
)
from adt_ai.shared.row_values import row_value

GatewayFactory = Callable[[str], QueryGateway]


@dataclass(frozen=True)
class ExportDbRequest:
    root         : Path
    schemas      : list[str]
    config       : dict[str, Any]
    schema_export: dict[str, dict[str, Any]] | None = None
    object_types : list[str] | None = None
    names        : list[str] | None = None
    prefix       : str | None = None
    ignore       : list[str] | None = None
    # None (full export), an int day window, or BARE_RECENT (since last export).
    recent       : int | object | None = None
    environment  : str | None = None
    clean        : bool = False
    dry_run      : bool = False
    reporter     : ExportDbReporter | None = None
    group_rules  : GroupRules | None = None
    changed_by   : str | None = None
    my_changes   : bool = False
    authors      : list[str] | None = None

    @property
    def recent_days(self) -> int | None:
        """The N-day window, or ``None`` for a full export and for watermark mode."""
        return recent_days(self.recent)


def is_narrowed(request: ExportDbRequest) -> bool:
    """Whether the run selected a subset of the schema rather than covering it.

    A narrowed run must never advance a watermark: a `-name`/`-type`/`-by`/`-my`
    export touches a slice, so stamping it would silently mark everything it did
    not look at as current and hide those objects from every later bare `-recent`.
    """
    return any(
        (
            request.names is not None,
            request.object_types is not None,
            request.authors is not None,
            request.my_changes,
            request.changed_by is not None,
        )
    )

class ExportDbRunner:
    def __init__(
        self,
        gateway_factory: GatewayFactory,
        normalizer_registry: NormalizerRegistry | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        self.normalizer_registry = normalizer_registry or NormalizerRegistry.builtin()

    def run(self, request: ExportDbRequest) -> list[ObjectWritePlan]:
        gateway_factory = _cached_gateway_factory(self.gateway_factory)
        resolver = ObjectFileResolver.from_config(
            root   = request.root,
            config = _with_default_layout(request.config),
        )
        resolver.group_rules = self._resolve_group_rules(request, resolver)
        writer = ObjectFileWriter(resolver, compare_existing=False)
        object_contents = self._contents(
            request,
            resolver        = resolver,
            gateway_factory = gateway_factory,
        )
        grant_contents = self._grant_contents(
            request,
            gateway_factory = gateway_factory,
        )
        if request.dry_run:
            requests: list[ObjectWriteRequest] = []
            for database_object, content, fix_content in object_contents:
                requests.append(ObjectWriteRequest(database_object, content))
                if fix_content is not None:
                    requests.append(
                        ObjectWriteRequest(
                            database_object,
                            fix_content,
                            path = resolver.fix_path_for(database_object),
                        )
                    )
            requests.extend(
                ObjectWriteRequest(database_object, content)
                for database_object, content in grant_contents
            )
            return writer.plan(requests, dry_run=True)
        plans: list[ObjectWritePlan] = []
        for database_object, content, fix_content in object_contents:
            plans.append(writer.write_one(ObjectWriteRequest(database_object, content)))
            fix_path = resolver.fix_path_for(database_object)
            if fix_content is not None:
                plans.append(
                    writer.write_one(
                        ObjectWriteRequest(database_object, fix_content, path=fix_path)
                    )
                )
            elif fix_path.exists():
                fix_path.unlink()
        for database_object, content in grant_contents:
            plans.append(writer.write_one(ObjectWriteRequest(database_object, content)))
        return plans

    def _resolve_group_rules(
        self,
        request: ExportDbRequest,
        resolver: ObjectFileResolver,
    ) -> GroupRules:
        # Seed with explicit/persisted rules, then always learn from how files were
        # arranged into <type>/<group>/ subfolders by the move action (or by hand).
        seed = request.group_rules or GroupRules.empty()
        type_roots = resolver.iter_type_roots(request.schemas)
        return seed.merged(detect_groups_from_tree(type_roots))

    def _contents(
        self,
        request: ExportDbRequest,
        resolver: ObjectFileResolver,
        gateway_factory: GatewayFactory,
    ) -> Iterable[tuple[DatabaseObject, str, str | None]]:
        reporter = request.reporter or ExportDbReporter()
        narrowed = is_narrowed(request)
        for schema in request.schemas:
            gateway = gateway_factory(schema)
            discovery = ObjectDiscovery(gateway)
            schema_export = (request.schema_export or {}).get(schema, {})
            # Read the database clock BEFORE the listing so an object changed
            # mid-run stays at or after the candidate and is re-selected next
            # time, and resolve this schema's own cutoff from its own watermark.
            candidate = (
                read_db_now(gateway)
                if not request.dry_run and not narrowed
                else None
            )
            stored = self._stored_watermark(request, schema)
            changed_since = stored if is_bare_recent(request.recent) else None
            if is_bare_recent(request.recent) and stored is None:
                reporter.recent_note(
                    f"{request.environment or '?'}/{schema}, exporting all objects"
                )
            database_objects = discovery.discover(
                schema       = schema,
                object_types = request.object_types or _configured_object_types(request.config),
                names        = request.names,
                prefix       = request.prefix or schema_export.get("prefix"),
                ignore       = request.ignore or _split_patterns(schema_export.get("ignore")),
                recent_days  = request.recent_days,
                changed_since = changed_since,
                prefer_exact_names = True,
            )
            # Objects the requested authors touched but somebody else changed last.
            # They stay in the export, dropping them would silently lose work the
            # author really did, and are marked with the later author instead.
            overtaken_by: dict[str, str] = {}
            if request.authors is not None:
                audit = _audit_config(request.config)
                author_objects = discovery.authors_objects(
                    audit,
                    request.authors,
                    recent_days   = request.recent_days,
                    changed_since = changed_since,
                )
                database_objects = [
                    database_object
                    for database_object in database_objects
                    if database_object.name.upper() in author_objects
                ]
                requested_authors = {author.upper() for author in request.authors}
                overtaken_by = {
                    name: last_changed_by
                    for name, last_changed_by in author_objects.items()
                    if last_changed_by and last_changed_by not in requested_authors
                }
            if not has_exact_name_filter(request.names):
                reporter.overview(
                    schema,
                    database_objects,
                    names         = request.names,
                    recent_days   = request.recent_days,
                    authors       = request.authors,
                    changed_since = changed_since,
                )
            if not _has_runtime_filter(request):
                missing_objects = resolver.missing_objects(database_objects, schema=schema)
                reporter.deleted_objects(
                    schema,
                    missing_objects,
                )
                if is_enabled(request.config.get("auto_delete")) and not request.dry_run:
                    resolver.delete_missing_objects(missing_objects)
            if request.clean and not request.dry_run:
                resolver.delete_configured_object_files(schema)
            if database_objects:
                discovery.setup_dbms_metadata()
            comment_types = _comment_query_object_types(request, request.config)
            if comment_types:
                discovery.prepare_comments(
                    schema       = schema,
                    object_types = comment_types,
                    names        = request.names,
                    prefix       = request.prefix or schema_export.get("prefix"),
                    ignore       = request.ignore or _split_patterns(schema_export.get("ignore")),
                )
            reporter.start_export(schema, len(database_objects))
            reports_objects = reporter.reports_objects
            add_if_not_exists = is_enabled(request.config.get("add_if_not_exists", True))
            for index, database_object in enumerate(database_objects):
                if reports_objects:
                    # A filename sitting in more than one place under the type
                    # subtree is reported on the object's own row rather than
                    # aborting the export: the run still finishes, and the user
                    # sees which objects carry stale clones to clean up by hand.
                    reporter.export_object(
                        database_object,
                        duplicates = [
                            resolver.display_path(location)
                            for location in resolver.duplicate_locations(database_object)
                        ],
                        changed_by = overtaken_by.get(database_object.name.upper()),
                    )
                raw_ddl = discovery.ddl(database_object)
                content = normalize_ddl(
                    raw_ddl,
                    object_type       = database_object.object_type,
                    object_name       = database_object.name,
                    registry          = self.normalizer_registry,
                    add_if_not_exists = add_if_not_exists,
                )
                fix_content = (
                    build_table_fix_sql(raw_ddl, database_object.name)
                    if database_object.object_type == "TABLE"
                    else None
                )
                if database_object.object_type == "JOB":
                    content = _append_job_arguments(
                        content,
                        discovery.job_arguments(database_object),
                        database_object.name,
                    )
                content = _append_comments(
                    content,
                    database_object,
                    discovery.comments(database_object)
                    if _has_comments(database_object.object_type, request.config)
                    else [],
                    include_columns = _has_column_comments(
                        database_object.object_type,
                        request.config,
                    ),
                    ignored_columns = _ignored_comment_columns(request.config),
                )
                yield database_object, content, fix_content
                next_object = (
                    database_objects[index + 1]
                    if index + 1 < len(database_objects)
                    else None
                )
                if (
                    reports_objects
                    and (
                        next_object is None
                        or next_object.object_type != database_object.object_type
                    )
                ):
                    reporter.finish_type(schema, database_object.object_type)
            # Reached only when every object of this schema was written, so a
            # schema that raised mid-export keeps its old watermark while the
            # schemas that finished keep theirs (per-schema isolation).
            self._advance_watermark(request, schema, candidate, stored, narrowed=narrowed)

    def _stored_watermark(self, request: ExportDbRequest, schema: str) -> str | None:
        if request.environment is None:
            return None
        return RecentStore.load(request.root).get("export_db", [request.environment, schema])

    def _advance_watermark(
        self,
        request: ExportDbRequest,
        schema: str,
        candidate: str | None,
        stored: str | None,
        narrowed: bool,
    ) -> None:
        """Stamp this schema's pass, saving immediately so later failures cannot undo it."""
        if candidate is None or request.environment is None:
            return
        if not may_advance(
            recent   = request.recent,
            stored   = stored,
            db_now   = candidate,
            narrowed = narrowed,
            dry_run  = request.dry_run,
        ):
            return
        store = RecentStore.load(request.root)
        store.set("export_db", [request.environment, schema], candidate)
        store.save()

    def _grant_contents(
        self,
        request: ExportDbRequest,
        gateway_factory: GatewayFactory,
    ) -> Iterable[tuple[DatabaseObject, str]]:
        if "GRANT" not in request.config.get("object_types", {}):
            return
        if not _requested_object_type_matches("GRANT", request.object_types):
            return
        for schema in request.schemas:
            discovery = ObjectDiscovery(gateway_factory(schema))
            schema_export = (request.schema_export or {}).get(schema, {})
            prefix = request.prefix or schema_export.get("prefix")
            ignore = request.ignore or _split_patterns(schema_export.get("ignore"))

            yield DatabaseObject(schema, "GRANT", schema), _render_grants_made(
                discovery.grants_made(schema, prefix=prefix, ignore=ignore),
                prefix = prefix,
                ignore = ignore,
            )
            for owner, content in _render_grants_received(
                discovery.grants_received(schema),
                schema = schema,
            ).items():
                yield DatabaseObject(schema, "GRANT", f"received/{owner.upper()}"), content
            yield (
                DatabaseObject(schema, "GRANT", f"{schema.upper()}_schema"),
                _render_user_privileges(
                    discovery.user_privileges(schema),
                    schema = schema,
                ),
            )
            yield (
                DatabaseObject(schema, "GRANT", f"{schema.upper()}_directories"),
                _render_directories(
                    discovery.directories(schema),
                    schema = schema,
                ),
            )


__all__ = [name for name in globals() if not name.startswith("__")]
