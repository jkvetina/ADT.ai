from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from oracledb import Error as OracleError

from adt_ai.export_db import queries

# Reading an object's DDL moved to `ddl_reads.py` with `#923`; the retention
# renderer keeps its old private name here for whatever imported it from here.
from adt_ai.export_db.ddl_reads import read_ddl
from adt_ai.export_db.ddl_reads import render_retention as _render_retention

# The runtime filters moved to their own module when `#740` took this file past
# the 24 KB context cap; re-exported under their old private names so nothing
# that imported one from here has to change.
from adt_ai.export_db.discovery_filters import ObjectFilters as _ObjectFilters
from adt_ai.export_db.discovery_filters import has_exact_name_filter as has_exact_name_filter
from adt_ai.export_db.discovery_filters import includes_object_type as _includes_object_type
from adt_ai.export_db.discovery_filters import (
    is_old_adt_eligible_index as _is_old_adt_eligible_index,
)
from adt_ai.export_db.discovery_filters import normalize_list as _normalize_list
from adt_ai.export_db.discovery_filters import normalize_patterns as _normalize_patterns
from adt_ai.export_db.discovery_filters import query_pattern_list as _query_pattern_list
from adt_ai.export_db.discovery_filters import user_object_types as _user_object_types
from adt_ai.export_db.timeless_types import (
    discover_job_names,
    discover_mview_log_names,
    job_references,
)
from adt_ai.shared.db import QueryGateway

#: The codes that say a dictionary view is absent or closed to this session:
#: `ORA-00942` (table or view does not exist), `ORA-00904` (a column this release
#: does not have) and `ORA-01031` (insufficient privileges). Every gateway puts
#: the code in the message text.
_MISSING_DICTIONARY_OBJECT_CODES = ("ORA-00942", "ORA-00904", "ORA-01031")


def missing_dictionary_object(error: BaseException) -> bool:
    """Whether `error` says the view is missing or closed, rather than that the read failed.

    The four readers of a release-dependent view -- assertions, schema
    privileges, table retention and the Data Security views -- swallow this
    answer and nothing else. They used to catch every exception, so a lost
    connection (`ORA-03113`), a cancelled call (`ORA-01013`) or an internal
    error read as "none exist": the runner built `missing_objects` from the
    truncated list and `auto_delete` removed the committed files of every
    object the read never saw (`#1094`).
    """
    message = str(error)
    return any(code in message for code in _MISSING_DICTIONARY_OBJECT_CODES)


@dataclass(frozen=True)
class DatabaseObject:
    schema      : str
    object_type : str
    name        : str


class ObjectDiscovery:
    OBJECTS_QUERY          = queries.OBJECTS_QUERY
    EXACT_OBJECTS_QUERY    = queries.EXACT_OBJECTS_QUERY
    INDEXES_QUERY          = queries.INDEXES_QUERY
    JOBS_QUERY             = queries.JOBS_QUERY
    JOB_ARGUMENTS_QUERY    = queries.JOB_ARGUMENTS_QUERY
    DDL_QUERY              = queries.DDL_QUERY
    MVIEW_LOGS_QUERY       = queries.MVIEW_LOGS_QUERY
    MVIEW_LOG_DDL_QUERY    = queries.MVIEW_LOG_DDL_QUERY
    JOB_DDL_QUERY          = queries.JOB_DDL_QUERY
    SCHEDULE_DDL_QUERY     = queries.SCHEDULE_DDL_QUERY
    DBMS_METADATA_SETUP_QUERY = queries.DBMS_METADATA_SETUP_QUERY
    GRANTS_MADE_QUERY      = queries.GRANTS_MADE_QUERY
    GRANTS_RECEIVED_QUERY  = queries.GRANTS_RECEIVED_QUERY
    USER_PRIVILEGES_QUERY  = queries.USER_PRIVILEGES_QUERY
    SCHEMA_PRIVILEGES_QUERY = queries.SCHEMA_PRIVILEGES_QUERY
    ASSERTIONS_QUERY       = queries.ASSERTIONS_QUERY
    ASSERTION_DDL_QUERY    = queries.ASSERTION_DDL_QUERY
    MLE_MODULE_DDL_QUERY   = queries.MLE_MODULE_DDL_QUERY
    DIRECTORIES_QUERY      = queries.DIRECTORIES_QUERY
    DATA_GRANTS_QUERY      = queries.DATA_GRANTS_QUERY
    DATA_ROLES_QUERY       = queries.DATA_ROLES_QUERY
    VPD_POLICIES_QUERY     = queries.VPD_POLICIES_QUERY
    CONTEXTS_BLOCK         = queries.CONTEXTS_BLOCK
    QUOTAS_QUERY           = queries.QUOTAS_QUERY
    HOST_ACES_QUERY        = queries.HOST_ACES_QUERY
    COMMENTS_QUERY         = queries.COMMENTS_QUERY
    TABLE_RETENTION_QUERY  = queries.TABLE_RETENTION_QUERY
    INTERVAL_LOCAL_INDEXES_QUERY = queries.INTERVAL_LOCAL_INDEXES_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway
        self._retention_by_schema: dict[str, dict[str, str]] = {}
        self._interval_local_indexes_by_schema: dict[str, set[str]] = {}
        self._comments_by_schema: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {}
        self._comment_cache_keys: dict[str, tuple[str, str, str, str]] = {}
        # Per-schema {job name: fresh signature} for the jobs this run selected,
        # so the caller can stamp the baseline once the files are actually written.
        self.last_job_signatures: dict[str, dict[str, str]] = {}
        # Per-schema cross-table Data Grant names the dictionary cannot rebuild,
        # for the warning printed once the schema's export is done (`#1063`).
        self.skipped_data_grants: dict[str, list[str]] = {}
        # Schemas whose ACE read returned no rows, never a closed view (`#1113`).
        self.no_host_aces: set[str] = set()

    def discover(
        self,
        schema: str,
        object_types: Iterable[str] | None = None,
        names: Iterable[str] | None = None,
        prefix: str | None = None,
        ignore: Iterable[str] | None = None,
        recent_days: int | float | None = None,
        changed_since: str | None = None,
        prefer_exact_names: bool = True,
        known_job_signatures: Mapping[str, str] | None = None,
    ) -> list[DatabaseObject]:
        filters = _ObjectFilters(
            object_types = _normalize_list(object_types),
            names        = _normalize_list(names),
            prefix       = _normalize_patterns(prefix),
            ignore       = _normalize_list(ignore) or [],
        )
        exact_names = prefer_exact_names and has_exact_name_filter(filters.names)
        rows = self._object_rows(
            schema, filters, recent_days, changed_since, exact_names=exact_names
        )
        objects = [
            DatabaseObject(
                schema      = schema,
                object_type = str(row["OBJECT_TYPE"]),
                name        = str(row["OBJECT_NAME"]),
            )
            for row in rows
            if str(row["OBJECT_TYPE"]).upper() not in {"INDEX", "JOB"}
            if (
                filters.matches_exact(str(row["OBJECT_TYPE"]), str(row["OBJECT_NAME"]))
                if exact_names
                else filters.matches(str(row["OBJECT_TYPE"]), str(row["OBJECT_NAME"]))
            )
        ]
        if _includes_object_type("INDEX", filters.object_types):
            objects.extend(self._discover_indexes(schema, filters, recent_days, changed_since))
        # A window narrows JOB and MVIEW LOG too, but the two types answer it very
        # differently. An mview log's LOG_TABLE is an ordinary TABLE, so its query
        # binds the window like every other type. A JOB has no change timestamp
        # anywhere, so `JOBS_QUERY` returns a content SIGNATURE and the window is
        # answered by comparing it against the last one exported.
        #
        # ADT #414 first answered this by exporting every job under a window, and
        # Jan rejected that: a `-recent` run on a 2000-job schema then exports 2000
        # jobs, which is worse than skipping them, because `-type JOB` already gives
        # the caller that set on demand. Both shapes were wrong in the same way, and
        # the signature is what lets a window mean "what changed" for this type too.
        if _includes_object_type("JOB", filters.object_types):
            # A job's program and schedule come along whatever the filter says,
            # once each: a full run has already listed them (ADT #993).
            listed = set(objects)
            objects.extend(
                found
                for found in self._discover_jobs(
                    schema,
                    filters,
                    windowed = recent_days is not None or changed_since is not None,
                    known    = known_job_signatures,
                )
                if found not in listed
            )
        if _includes_object_type("MVIEW LOG", filters.object_types):
            objects.extend(
                self._discover_mview_logs(schema, filters, recent_days, changed_since)
            )
        # An assertion answers the window like an ordinary type -- its `UNDEFINED`
        # row in `user_objects` carries the timestamp -- so it needs neither the
        # signature a JOB does nor a widening. What it does need is its own read:
        # `user_objects` never spells its type (`#740`).
        if _includes_object_type("ASSERTION", filters.object_types):
            objects.extend(
                self._discover_assertions(schema, filters, recent_days, changed_since)
            )
        return objects

    def _object_rows(
        self,
        schema: str,
        filters: _ObjectFilters,
        recent_days: int | float | None,
        changed_since: str | None,
        exact_names: bool,
    ) -> list[dict[str, Any]]:
        if exact_names:
            rows: list[dict[str, Any]] = []
            for object_name in filters.names or []:
                rows.extend(
                    self.gateway.fetch_all(
                        self.EXACT_OBJECTS_QUERY,
                        {
                            "schema": schema,
                            "recent_days": recent_days,
                            "changed_since": changed_since,
                            "object_name": object_name,
                        },
                    )
                )
            return rows
        object_types = _user_object_types(filters.object_types)
        if object_types == []:
            return []
        return self.gateway.fetch_all(
            self.OBJECTS_QUERY,
            {
                "schema": schema,
                "recent_days": recent_days,
                "changed_since": changed_since,
                "object_type_filter": _query_pattern_list(object_types, default="%"),
            },
        )

    def _discover_jobs(
        self,
        schema: str,
        filters: _ObjectFilters,
        windowed: bool = False,
        known: Mapping[str, str] | None = None,
    ) -> list[DatabaseObject]:
        """Pick the jobs to export, narrowing by signature only under a window.

        The fresh signatures of every job this run SELECTED are recorded on
        `last_job_signatures` so the caller can persist them once the export has
        actually written the files. Recording them here rather than returning them
        keeps the discovery contract a plain list of objects.

        Each chosen job is followed by the program and schedule it names, since
        its file cannot install without them (ADT #993).
        """
        rows = self.gateway.fetch_all(self.JOBS_QUERY, {"schema": schema})
        names, signatures = discover_job_names(rows, filters.matches, windowed, known)
        self.last_job_signatures[schema] = signatures
        return [DatabaseObject(schema, "JOB", name) for name in names] + [
            DatabaseObject(schema, object_type, name)
            for object_type, name in job_references(rows, names, schema)
        ]

    def _discover_mview_logs(
        self,
        schema: str,
        filters: _ObjectFilters,
        recent_days: int | float | None = None,
        changed_since: str | None = None,
    ) -> list[DatabaseObject]:
        rows = self.gateway.fetch_all(
            self.MVIEW_LOGS_QUERY,
            {
                "schema": schema,
                "recent_days": recent_days,
                "changed_since": changed_since,
            },
        )
        return [
            DatabaseObject(schema, "MVIEW LOG", name)
            for name in discover_mview_log_names(rows, filters.matches)
        ]

    def _discover_assertions(
        self,
        schema: str,
        filters: _ObjectFilters,
        recent_days: int | float | None = None,
        changed_since: str | None = None,
    ) -> list[DatabaseObject]:
        """The schema's 23ai assertions, or none on a dictionary without the view.

        Swallowed exactly as `schema_privileges` and `table_retention` swallow,
        and only that (`missing_dictionary_object`): `user_assertions` arrived
        in 23ai, ADT exports from older databases, and a type that cannot exist
        on that release is an answer rather than an error. Swallowing here
        rather than at the caller keeps a plain `export_db` over an 11g schema
        working, since the shipped config asks for this type on every run.
        """
        try:
            rows = self.gateway.fetch_all(
                self.ASSERTIONS_QUERY,
                {
                    "schema": schema,
                    "recent_days": recent_days,
                    "changed_since": changed_since,
                },
            )
        except Exception as error:
            if not missing_dictionary_object(error):
                raise
            return []
        return [
            DatabaseObject(schema, "ASSERTION", name)
            for row in rows
            if (name := str(row.get("OBJECT_NAME") or row.get("object_name") or ""))
            if filters.matches("ASSERTION", name)
        ]

    def _discover_indexes(
        self,
        schema: str,
        filters: _ObjectFilters,
        recent_days: int | float | None,
        changed_since: str | None = None,
    ) -> list[DatabaseObject]:
        rows = self.gateway.fetch_all(
            self.INDEXES_QUERY,
            {
                "schema": schema,
                "recent_days": recent_days,
                "changed_since": changed_since,
            },
        )
        return [
            DatabaseObject(schema, "INDEX", str(row["OBJECT_NAME"]))
            for row in rows
            if _is_old_adt_eligible_index(row)
            if filters.matches_index(
                index_name = str(row["OBJECT_NAME"]),
                table_name = str(row.get("TABLE_NAME") or ""),
            )
        ]

    def grants_made(
        self,
        schema: str,
        prefix: str | None = None,
        ignore: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(
            self.GRANTS_MADE_QUERY,
            {
                "objects_prefix": _query_pattern_list(_normalize_patterns(prefix), default="%"),
                "objects_ignore": _query_pattern_list(ignore, default=""),
            },
        )

    def grants_received(self, schema: str) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(self.GRANTS_RECEIVED_QUERY)

    def user_privileges(self, schema: str) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(self.USER_PRIVILEGES_QUERY)

    def schema_privileges(self, schema: str) -> list[dict[str, Any]]:
        """The 23ai schema-wide privileges this user holds (`#740`).

        Empty on any database whose dictionary has no `user_schema_privs`: the
        view is 23ai, ADT supports older ones, and a privilege that cannot exist
        needs no row. Swallowed for the same reason `table_retention` swallows,
        it is an answer rather than an error; any other failure is the error
        it looks like (`missing_dictionary_object`).
        """
        try:
            return self.gateway.fetch_all(self.SCHEMA_PRIVILEGES_QUERY)
        except Exception as error:
            if not missing_dictionary_object(error):
                raise
            return []

    def quotas(self, schema: str) -> list[dict[str, Any]]:
        """This user's tablespace quotas (`#1098`), empty where the view is closed."""
        return self._optional_view(self.QUOTAS_QUERY)

    def host_aces(self, schema: str) -> list[dict[str, Any]]:
        """This user's network ACEs (`#1098`).

        Only a successful empty read records `no_host_aces` (`#1113`).
        """
        self.no_host_aces.discard(schema.upper())
        try:
            rows = self.gateway.fetch_all(self.HOST_ACES_QUERY)
        except (OracleError, RuntimeError) as error:
            if not missing_dictionary_object(error):
                raise
            return []
        if not rows:
            self.no_host_aces.add(schema.upper())
        return rows

    def _optional_view(self, query: str) -> list[dict[str, Any]]:
        try:
            return self.gateway.fetch_all(query)
        except Exception as error:
            if not missing_dictionary_object(error):
                raise
            return []

    def directories(self, schema: str) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(self.DIRECTORIES_QUERY)

    def authors_objects(
        self,
        audit: Any,
        authors: Iterable[str],
        recent_days: int | float | None = None,
        changed_since: str | None = None,
    ) -> dict[str, str | None]:
        """Map each object ``authors`` touched to the author who changed it **last**.

        The value is ``None`` when the project configured no ``changed_at`` column:
        an unordered log cannot say who was last, so the caller gets the object set
        and no recency claim. ``recent_days``/``changed_since`` narrow the audit
        source itself, and are bound only when the query accepts them.
        """
        changed_at_column = getattr(audit, "changed_at_column", None)
        query = queries.audit_authors_query(
            audit.source,
            audit.object_name_column,
            audit.changed_by_column,
            changed_at_column,
        )
        params: dict[str, Any] = {
            "authors": _query_pattern_list(_normalize_list(authors), default=""),
        }
        if changed_at_column is not None:
            params["recent_days"] = recent_days
            params["changed_since"] = changed_since
        rows = self.gateway.fetch_all(query, params)
        resolved: dict[str, str | None] = {}
        for row in rows:
            name = str(row.get("OBJECT_NAME") or row.get("object_name") or "").upper()
            if not name:
                continue
            last_changed_by = row.get("LAST_CHANGED_BY") or row.get("last_changed_by")
            resolved[name] = str(last_changed_by).upper() if last_changed_by else None
        return resolved

    def prepare_comments(
        self,
        schema: str,
        object_types: Iterable[str] | None = None,
        names: Iterable[str] | None = None,
        prefix: str | None = None,
        ignore: Iterable[str] | None = None,
    ) -> None:
        params = _comment_query_params(
            schema       = schema,
            object_types = object_types,
            names        = names,
            prefix       = prefix,
            ignore       = ignore,
        )
        cache_key = (
            str(params["object_type"]),
            str(params["object_name"]),
            str(params["objects_prefix"]),
            str(params["objects_ignore"]),
        )
        if self._comment_cache_keys.get(schema) == cache_key:
            return
        rows = self.gateway.fetch_all(self.COMMENTS_QUERY, params)
        indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            indexed.setdefault(
                (_comment_object_type(row), _comment_object_name(row)),
                [],
            ).append(row)
        self._comments_by_schema[schema] = indexed
        self._comment_cache_keys[schema] = cache_key

    def comments(self, database_object: DatabaseObject) -> list[dict[str, Any]]:
        if database_object.schema not in self._comments_by_schema:
            self.prepare_comments(database_object.schema)
        return self._comments_by_schema.get(database_object.schema, {}).get(
            (database_object.object_type.upper(), database_object.name.upper()),
            [],
        )

    def table_retention(self, database_object: DatabaseObject) -> str:
        """The rendered retention clauses of one immutable or blockchain table.

        Empty for every other object, and empty on any database whose dictionary
        has no such views -- they are 21c, ADT supports older ones, and a table
        that cannot exist needs no clause. That is why a missing view is
        swallowed rather than reported: it is an answer, not an error. A read
        that failed for any other reason is raised, since an empty answer here
        exports an immutable table as a mutable one (`#1094`).
        """
        if database_object.object_type.upper() != "TABLE":
            return ""
        schema = database_object.schema
        if schema not in self._retention_by_schema:
            try:
                rows = self.gateway.fetch_all(
                    self.TABLE_RETENTION_QUERY,
                    {"schema": schema},
                )
            except Exception as error:
                if not missing_dictionary_object(error):
                    raise
                rows = []
            self._retention_by_schema[schema] = {
                str(row.get("TABLE_NAME") or row.get("table_name") or "").upper(): clause
                for row in rows
                if (clause := _render_retention(row))
            }
        return self._retention_by_schema[schema].get(database_object.name.upper(), "")

    def interval_local_index(self, database_object: DatabaseObject) -> bool:
        """Whether this is a LOCAL index on an interval-partitioned table (`#1091`).

        One query per schema, the first time an index asks; every other object
        type answers False without one.
        """
        if database_object.object_type.upper() != "INDEX":
            return False
        schema = database_object.schema
        if schema not in self._interval_local_indexes_by_schema:
            rows = self.gateway.fetch_all(
                self.INTERVAL_LOCAL_INDEXES_QUERY,
                {"schema": schema},
            )
            self._interval_local_indexes_by_schema[schema] = {
                str(row.get("INDEX_NAME") or row.get("index_name") or "").upper()
                for row in rows
            }
        return database_object.name.upper() in self._interval_local_indexes_by_schema[schema]

    def ddl(self, database_object: DatabaseObject) -> str:
        """The object's DDL; `ObjectDroppedError` once it is gone (`#923`)."""
        return read_ddl(self.gateway, database_object)

    def setup_dbms_metadata(self) -> None:
        self.gateway.execute(self.DBMS_METADATA_SETUP_QUERY)

    def job_arguments(self, database_object: DatabaseObject) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(
            self.JOB_ARGUMENTS_QUERY,
            {
                "job_name": database_object.name,
            },
        )


def _comment_object_type(row: dict[str, Any]) -> str:
    return str(row.get("OBJECT_TYPE") or row.get("object_type") or "").upper()


def _comment_object_name(row: dict[str, Any]) -> str:
    return str(row.get("OBJECT_NAME") or row.get("object_name") or "").upper()


def _comment_query_params(
    schema: str,
    object_types: Iterable[str] | None,
    names: Iterable[str] | None,
    prefix: str | None,
    ignore: Iterable[str] | None,
) -> dict[str, str]:
    return {
        "schema": schema,
        "object_type": _query_pattern_list(object_types, default="%"),
        "object_name": _query_pattern_list(names, default="%"),
        "objects_prefix": _query_pattern_list(_normalize_patterns(prefix), default="%"),
        "objects_ignore": _query_pattern_list(ignore, default=""),
    }
