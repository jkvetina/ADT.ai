from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved helpers.
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.export_db.config import (
    ObjectTypeFilterError,
    _audit_config,
    _cached_gateway_factory,
    _configured_empty_lines,
    _configured_object_types,
    _has_runtime_filter,
    _requested_object_type_matches,
    _split_patterns,
    _with_default_layout,
    unexportable_object_types,
    unexportable_object_types_message,
)
from adt_ai.export_db.content import (
    _append_comments,
    _append_job_arguments,
    _comment_query_object_types,
    _has_column_comments,
    _has_comments,
    _ignored_comment_columns,
    _job_arguments_dropped,
    _render_directories,
    _render_grants_made,
    _render_grants_received,
    _render_user_privileges,
)
from adt_ai.export_db.failures import ExportObjectsFailedError, ObjectExportFailure
from adt_ai.export_db.files import (
    ObjectFileResolver,
    ObjectFileWriter,
    ObjectWritePlan,
    ObjectWriteRequest,
)
from adt_ai.export_db.grants import exports_grants, grant_artifacts
from adt_ai.export_db.groups import (
    GroupRules,
    detect_groups_from_tree,
    resolve_group_rules,
)
from adt_ai.export_db.inventory import (
    DatabaseObject,
    ObjectDiscovery,
    has_exact_name_filter,
)
from adt_ai.export_db.job_signatures import advance_job_signatures, job_baseline
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
    print_adt_table,
)
from adt_ai.export_db.request import ExportDbRequest
from adt_ai.export_db.schema_objects import export_objects, stamp_schema
from adt_ai.export_db.timers import SegmentTimer, estimate_for
from adt_ai.export_db.watermarks import advance_watermark, is_narrowed, stored_watermark
from adt_ai.shared.config import is_enabled
from adt_ai.shared.connection_errors import ConnectFailedError
from adt_ai.shared.dates import is_sub_day_window
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.diff_tables import drop_diff_tables
from adt_ai.shared.recent_state import (
    is_bare_recent,
    read_db_now,
    recent_days,
)
from adt_ai.shared.row_values import row_value

GatewayFactory = Callable[[str], QueryGateway]


class ExportDbRunner:
    def __init__(
        self,
        gateway_factory: GatewayFactory,
        normalizer_registry: NormalizerRegistry | None = None,
        fatal_error: Callable[[Exception], bool] | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        self.normalizer_registry = normalizer_registry or NormalizerRegistry.builtin()
        # What stops the run instead of being recorded (`#917`): a lost
        # connection, which every remaining object would repeat.
        self.fatal_error = fatal_error or (
            lambda error: isinstance(error, ConnectFailedError)
        )

    def run(self, request: ExportDbRequest) -> list[ObjectWritePlan]:
        gateway_factory = _cached_gateway_factory(self.gateway_factory)
        resolver = ObjectFileResolver.from_config(
            root   = request.root,
            config = _with_default_layout(request.config),
        )
        resolver.group_rules = self._resolve_group_rules(request, resolver)
        writer = ObjectFileWriter(
            resolver,
            empty_lines = _configured_empty_lines(request.config),
        )
        # Read by `_contents` under each schema's own overview table, written
        # here once that schema's objects are out. See the call site.
        grant_contents: list[tuple[DatabaseObject, str]] = []
        failures: list[ObjectExportFailure] = []
        object_contents = self._contents(
            request,
            resolver        = resolver,
            gateway_factory = gateway_factory,
            writer          = writer,
            grant_contents  = grant_contents,
            failures        = failures,
        )
        # The whole mode is this one seam: `-baseline` hashes where a normal run
        # writes, and everything above is identical, which is what makes a
        # measurement comparable to an export rather than merely similar (`#452`).
        emit = writer.hash_one if request.baseline else writer.write_one
        plans: list[ObjectWritePlan] = []
        for database_object, content, fix_content in object_contents:
            plans.append(emit(ObjectWriteRequest(database_object, content)))
            fix_path = resolver.fix_path_for(database_object)
            if fix_content is not None:
                plans.append(
                    emit(ObjectWriteRequest(database_object, fix_content, path=fix_path))
                )
            elif fix_path.exists() and not request.baseline:
                # A measured run deletes nothing, a stale sidecar included.
                fix_path.unlink()
        for database_object, content in grant_contents:
            plans.append(emit(ObjectWriteRequest(database_object, content)))
        if failures:
            # After every write, chained to the first refusal for `-debug`.
            raise ExportObjectsFailedError(failures, plans) from failures[0].error
        return plans

    def _resolve_group_rules(
        self,
        request: ExportDbRequest,
        resolver: ObjectFileResolver,
    ) -> GroupRules:
        return resolve_group_rules(request, resolver)

    def _contents(
        self,
        request: ExportDbRequest,
        resolver: ObjectFileResolver,
        gateway_factory: GatewayFactory,
        writer: ObjectFileWriter,
        grant_contents: list[tuple[DatabaseObject, str]],
        failures: list[ObjectExportFailure],
    ) -> Iterable[tuple[DatabaseObject, str, str | None]]:
        reporter = request.reporter or ExportDbReporter()
        narrowed = is_narrowed(request)
        for schema in request.schemas:
            gateway = gateway_factory(schema)
            discovery = ObjectDiscovery(gateway)
            schema_export = (request.schema_export or {}).get(schema, {})
            stored = stored_watermark(request, schema)
            changed_since = stored if is_bare_recent(request.recent) else None
            # A sub-day window is the one overview title carrying an instant,
            # and it has to be the database's own (`#340`), so that single
            # `SELECT ... FROM dual` is the one read that must come before the
            # header rather than under it. Every other shape knows its title
            # from the request alone.
            sub_day = is_sub_day_window(request.recent_days)
            header_now = read_db_now(gateway) if sub_day else None
            # **The overview header goes up before the schema's first read**
            # (`#372`). The diff-table sweep, the clock and the object listing
            # all print nothing, so without this they ran behind the connection
            # block's closing blank, which is the shape Jan reported: *"Most of
            # the time you stop on the last line of previous block, for example
            # when you connect to database."* The table below is the same table,
            # filling in behind a header already on screen.
            if not has_exact_name_filter(request.names):
                reporter.begin_overview(
                    names         = request.names,
                    recent_days   = request.recent_days,
                    authors       = request.authors,
                    changed_since = changed_since,
                    db_now        = header_now,
                )
            # The backstop sweep for SQLcl DIFF leftovers (ADT #356). The `diff`
            # run that made them clears them itself; this catches the run that
            # could not, because the connection died with the tables still there.
            # It happens BEFORE the listing so this export cannot read a table
            # the sweep is about to drop. No row of its own, because a backstop
            # that finds nothing is not news (`#372`).
            #
            # **It runs under the overview header and reports under the overview
            # table.** Its own `DROPPING DIFF TABLES:` section can only print
            # once the read says there is something to drop, so running it above
            # the header left that read on a blank screen (`#372`).
            dropped_diff_tables = drop_diff_tables(gateway)
            # Clock BEFORE the listing, so an object changed mid-run stays at or
            # after the candidate and is re-selected next time. A sub-day run
            # already has it from above, so the round trip still happens once.
            candidate = (
                (header_now if sub_day else read_db_now(gateway))
                if not narrowed
                else None
            )
            database_objects = discovery.discover(
                schema       = schema,
                object_types = (
                    request.object_types or _configured_object_types(request.config)
                ),
                names        = request.names,
                prefix       = request.prefix or schema_export.get("prefix"),
                ignore       = (
                    request.ignore or _split_patterns(schema_export.get("ignore"))
                ),
                recent_days  = request.recent_days,
                changed_since = changed_since,
                prefer_exact_names = True,
                known_job_signatures = job_baseline(request, schema),
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
            grants = exports_grants(request)
            if not has_exact_name_filter(request.names):
                reporter.overview(
                    schema,
                    database_objects,
                    names         = request.names,
                    recent_days   = request.recent_days,
                    authors       = request.authors,
                    changed_since = changed_since,
                    db_now        = header_now,
                    grants        = grants,
                )
            if grants:
                # **The five privilege reads run HERE, under the overview table
                # the call above left open** (`#437`), rather than after the
                # object loop where `#382` put them. Why that is the right place
                # for both the reads and the row is in `grants.grant_artifacts`.
                schema_grants, grants_changed = grant_artifacts(
                    request, schema, discovery, _split_patterns, writer
                )
                grant_contents.extend(schema_grants)
                # This schema's artifacts, not the accumulated list above: the
                # overview is rendered once per schema segment, so a second
                # schema's row would otherwise count the first one's files too.
                reporter.overview_grants(grants_changed, len(schema_grants))
            reporter.diff_tables_dropped(dropped_diff_tables)
            # The wildcard route into `#739`. `-type %` matches every exported
            # type, so nothing about it can be refused from the config alone,
            # and on a 26ai schema discovery still comes back holding a domain,
            # whose DDL pull dies inside DBMS_METADATA. Raised HERE, once the
            # overview section has closed and before the export section opens:
            # the table above is what was found, and a refusal one call earlier
            # would land under an `OBJECTS OVERVIEW:` header whose table is
            # still waiting on the grants reads to render (`#442`).
            unexportable = unexportable_object_types(
                (database_object.object_type for database_object in database_objects),
                request.config,
            )
            if unexportable:
                raise ObjectTypeFilterError(
                    unexportable_object_types_message(unexportable)
                )
            if not _has_runtime_filter(request) and not request.baseline:
                # A measured run reports no deletions and makes none: a file the
                # target lacks is a difference for `patch -hash` to decide about.
                missing_objects = resolver.missing_objects(database_objects, schema=schema)
                reporter.deleted_objects(
                    schema,
                    missing_objects,
                )
                if is_enabled(request.config.get("auto_delete")):
                    resolver.delete_missing_objects(missing_objects)
            if request.clean:
                resolver.delete_configured_object_files(schema)
            # Before, not after, the DBMS_METADATA setup and the comment
            # pre-read: neither prints a row, so the header is all there is.
            # `-compact`'s countdown is priced here rather than in the reporter:
            # the estimate is stored state, and reading it is the runner's job
            # (`#377`).
            reporter.start_export(
                schema,
                len(database_objects),
                estimate_for(request.root, request.environment, schema, database_objects),
            )
            # The DBMS_METADATA setup and the comment pre-read are elapsed the
            # bar counts and no per-object rate can explain, so they are booked
            # on their own (`#377`).
            timer = SegmentTimer()
            if database_objects:
                discovery.setup_dbms_metadata()
            # Gated on objects, as the setup above already is: the pre-read
            # fills a cache only the object loop reads, so a zero run fetched
            # comments nothing would ask for (`#442`).
            comment_types = (
                _comment_query_object_types(request, request.config)
                if database_objects
                else []
            )
            if comment_types:
                discovery.prepare_comments(
                    schema       = schema,
                    object_types = comment_types,
                    names        = request.names,
                    prefix       = request.prefix or schema_export.get("prefix"),
                    ignore       = request.ignore or _split_patterns(schema_export.get("ignore")),
                )
            timer.setup_done()
            # The object loop and the stamps after it live in `schema_objects`
            # (`#923`); what it hands back is this schema's refusals.
            refused = yield from export_objects(
                request,
                schema,
                database_objects,
                discovery    = discovery,
                resolver     = resolver,
                reporter     = reporter,
                registry     = self.normalizer_registry,
                fatal_error  = self.fatal_error,
                timer        = timer,
                overtaken_by = overtaken_by,
                failures     = failures,
            )
            # What this segment cost, folded into the rates the next run prices
            # itself from. Reached only on a completed segment, for the same
            # reason the watermark below is: a run that raised half way through
            # measured half a schema and would teach the store a rate no later
            # run can reproduce.
            timer.store(request.root, request.environment, schema)
            stamp_schema(
                request,
                schema,
                database_objects,
                refused,
                candidate      = candidate,
                stored         = stored,
                narrowed       = narrowed,
                job_signatures = discovery.last_job_signatures.get(schema),
            )


__all__ = [
    "Any",
    "Callable",
    "ConsoleExportDbReporter",
    "DatabaseObject",
    "ExportDbReporter",
    "ExportDbRequest",
    "ExportDbRunner",
    "GatewayFactory",
    "GroupRules",
    "Iterable",
    "NormalizerRegistry",
    "ObjectDiscovery",
    "ObjectFileResolver",
    "ObjectFileWriter",
    "ObjectTypeFilterError",
    "ObjectWritePlan",
    "ObjectWriteRequest",
    "Path",
    "QueryGateway",
    "SegmentTimer",
    "_AdtTableLayout",
    "_append_comments",
    "_append_job_arguments",
    "_audit_config",
    "_cached_gateway_factory",
    "_comment_query_object_types",
    "_commit_stdout",
    "_compute_adt_layout",
    "_configured_object_types",
    "_has_column_comments",
    "_has_comments",
    "_has_runtime_filter",
    "_ignored_comment_columns",
    "_render_directories",
    "_render_grants_made",
    "_render_grants_received",
    "_render_user_privileges",
    "_requested_object_type_matches",
    "_split_patterns",
    "_with_default_layout",
    "advance_job_signatures",
    "advance_watermark",
    "annotations",
    "build_table_fix_sql",
    "dataclass",
    "detect_groups_from_tree",
    "drop_diff_tables",
    "estimate_for",
    "exports_grants",
    "grant_artifacts",
    "has_exact_name_filter",
    "is_bare_recent",
    "is_enabled",
    "is_narrowed",
    "is_sub_day_window",
    "job_baseline",
    "normalize_ddl",
    "print_adt_header",
    "print_adt_table",
    "read_db_now",
    "recent_days",
    "resolve_group_rules",
    "row_value",
    "stored_watermark",
    "unexportable_object_types",
    "unexportable_object_types_message",
]
