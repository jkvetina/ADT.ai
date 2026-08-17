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
from adt_ai.export_db.grants import exports_grants
from adt_ai.export_db.grants import grant_contents as _grants_for
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
from adt_ai.export_db.progress import widest_object_type
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
from adt_ai.export_db.timers import SegmentTimer, estimate_for
from adt_ai.export_db.watermarks import advance_watermark, is_narrowed, stored_watermark
from adt_ai.shared.config import is_enabled
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
    recent       : int | float | object | None = None
    environment  : str | None = None
    clean        : bool = False
    dry_run      : bool = False
    reporter     : ExportDbReporter | None = None
    group_rules  : GroupRules | None = None
    changed_by   : str | None = None
    my_changes   : bool = False
    authors      : list[str] | None = None

    @property
    def recent_days(self) -> int | float | None:
        """The day window, or ``None`` for a full export and for watermark mode.

        A float when the window is shorter than a day (`-recent 1/24`).
        """
        return recent_days(self.recent)



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
        # Read by `_contents` while each schema's export section is still open,
        # written here once that schema's objects are out. See the call site.
        grant_contents: list[tuple[DatabaseObject, str]] = []
        object_contents = self._contents(
            request,
            resolver        = resolver,
            gateway_factory = gateway_factory,
            grant_contents  = grant_contents,
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
        grant_contents: list[tuple[DatabaseObject, str]],
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
            # the sweep is about to drop, and a dry run sweeps too: nothing about
            # "do not write files" says "leave the schema dirty". No row of its
            # own, because a backstop that finds nothing is not news (`#372`).
            #
            # **It runs under the overview header and reports under the overview
            # table.** Its own `DROPPING DIFF TABLES:` section can only print
            # once the read says there is something to drop, so running it above
            # the header left that read on a blank screen (`#372`).
            dropped_diff_tables = drop_diff_tables(gateway)
            if is_bare_recent(request.recent) and stored is None:
                reporter.recent_note(
                    f"{request.environment or '?'}/{schema}, exporting all objects"
                )
            # Clock BEFORE the listing, so an object changed mid-run stays at or
            # after the candidate and is re-selected next time. A sub-day run
            # already has it from above, so the round trip still happens once.
            candidate = (
                (header_now if sub_day else read_db_now(gateway))
                if not request.dry_run and not narrowed
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
            reporter.diff_tables_dropped(dropped_diff_tables)
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
            # Before, not after, the DBMS_METADATA setup and the comment
            # pre-read: neither prints a row, so the header is all there is.
            # `-compact`'s countdown is priced here rather than in the reporter:
            # the estimate is stored state, and reading it is the runner's job
            # (`#377`).
            reporter.start_export(
                schema,
                len(database_objects),
                estimate_for(request.root, request.environment, schema, database_objects),
                widest_object_type(database_objects),
                grants = grants,
            )
            # The DBMS_METADATA setup and the comment pre-read are elapsed the
            # bar counts and no per-object rate can explain, so they are booked
            # on their own (`#377`).
            timer = SegmentTimer()
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
            timer.setup_done()
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
                failed = True
                try:
                    raw_ddl = discovery.ddl(database_object)
                    failed = False
                finally:
                    # The row opened above closes here, whether the pull came
                    # back or raised: an unterminated row would otherwise be
                    # welded to the error banner's first line (`#232`). Which of
                    # the two happened is passed on because `-compact`'s row is a
                    # bar: a failure completes it with `FAILED` rather than
                    # advancing it over an object that was never written.
                    reporter.finish_object(failed=failed)
                if not failed:
                    timer.record(database_object.object_type)
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
            # Read here rather than from `run()` after the loop: under `-compact`
            # the line below closes the bar, and these four reads behind that
            # closing blank are the naked wait this card is about. `run()` still
            # writes the files, so only the moment of the read has moved (`#372`).
            if grants:
                reporter.start_grants(schema)
            grant_contents.extend(
                _grants_for(request, schema, discovery, _split_patterns)
            )
            # Every object of this schema is written, so `-compact`'s bar has
            # nothing left to count: close it at 100% before the segment's TIMER.
            # One bar per schema, never a grand total across them, the same split
            # the shared per-schema section helper applies to every other output.
            reporter.finish_export(schema)
            # What this segment cost, folded into the rates the next run prices
            # itself from. Reached only on a completed segment, for the same
            # reason the watermark below is: a run that raised half way through
            # measured half a schema and would teach the store a rate no later
            # run can reproduce.
            timer.store(request.root, request.environment, schema)
            # Reached only when every object of this schema was written, so a
            # schema that raised mid-export keeps its old watermark while the
            # schemas that finished keep theirs (per-schema isolation).
            advance_watermark(request, schema, candidate, stored, narrowed=narrowed)


__all__ = [name for name in globals() if not name.startswith("__")]
