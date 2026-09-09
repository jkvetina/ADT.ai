from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from adt_ai.export_db import queries
from adt_ai.export_db.dictionary_ddl import ASSEMBLED_OBJECT_TYPES, assemble_ddl, with_owner

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
from adt_ai.export_db.timeless_types import discover_job_names, discover_mview_log_names
from adt_ai.shared.db import QueryGateway


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
    COMMENTS_QUERY         = queries.COMMENTS_QUERY
    TABLE_RETENTION_QUERY  = queries.TABLE_RETENTION_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway
        self._retention_by_schema: dict[str, dict[str, str]] = {}
        self._comments_by_schema: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {}
        self._comment_cache_keys: dict[str, tuple[str, str, str, str]] = {}
        # Per-schema {job name: fresh signature} for the jobs this run selected,
        # so the caller can stamp the baseline once the files are actually written.
        self.last_job_signatures: dict[str, dict[str, str]] = {}

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
            objects.extend(
                self._discover_jobs(
                    schema,
                    filters,
                    windowed = recent_days is not None or changed_since is not None,
                    known    = known_job_signatures,
                )
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
        """
        rows = self.gateway.fetch_all(self.JOBS_QUERY, {"schema": schema})
        names, signatures = discover_job_names(rows, filters.matches, windowed, known)
        self.last_job_signatures[schema] = signatures
        return [DatabaseObject(schema, "JOB", name) for name in names]

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

        Swallowed exactly as `schema_privileges` and `table_retention` swallow:
        `user_assertions` arrived in 23ai, ADT exports from older databases, and a
        type that cannot exist on that release is an answer rather than an error.
        Swallowing here rather than at the caller keeps a plain `export_db` over an
        11g schema working, since the shipped config now asks for this type on
        every run.
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
        except Exception:
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
        it is an answer rather than an error.
        """
        try:
            return self.gateway.fetch_all(self.SCHEMA_PRIVILEGES_QUERY)
        except Exception:
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
        that cannot exist needs no clause. That is why the failure is swallowed
        rather than reported: it is an answer, not an error.
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
            except Exception:
                rows = []
            self._retention_by_schema[schema] = {
                str(row.get("TABLE_NAME") or row.get("table_name") or "").upper(): clause
                for row in rows
                if (clause := _render_retention(row))
            }
        return self._retention_by_schema[schema].get(database_object.name.upper(), "")

    def ddl(self, database_object: DatabaseObject) -> str:
        object_type = database_object.object_type.upper()
        if object_type in ASSEMBLED_OBJECT_TYPES:
            # Three of the four types `DBMS_METADATA` refuses are more than one row
            # of one view, so their statement is assembled rather than selected
            # (`#738`). Everything downstream still sees a plain DDL string.
            return with_owner(
                assemble_ddl(object_type, database_object.name, self.gateway),
                database_object.schema,
            )
        query, params = _ddl_query(database_object)
        rows = self.gateway.fetch_all(
            query,
            params,
        )
        if not rows:
            return ""
        return str(rows[0].get("DDL") or rows[0].get("ddl") or "")

    def setup_dbms_metadata(self) -> None:
        self.gateway.execute(self.DBMS_METADATA_SETUP_QUERY)

    def job_arguments(self, database_object: DatabaseObject) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(
            self.JOB_ARGUMENTS_QUERY,
            {
                "job_name": database_object.name,
            },
        )


def _retention_value(row: dict[str, Any], column: str) -> Any:
    return row.get(column.upper(), row.get(column.lower()))


def _render_retention(row: dict[str, Any]) -> str:
    """Spell the dictionary's four retention columns the way Oracle spells them.

    Measured against `DBMS_METADATA.GET_DDL` with `SEGMENT_ATTRIBUTES` on, on
    Oracle AI Database 23.26.3.0.0, so the exported file is byte-for-byte the
    clause the database itself would write:

        NO DROP UNTIL 0 DAYS IDLE NO DELETE UNTIL 16 DAYS AFTER INSERT LOCKED
        VERSION "V1"

    A NULL `ROW_RETENTION` is an unlimited one, which Oracle writes as a bare
    `NO DELETE`; `LOCKED` reflects `ROW_RETENTION_LOCKED` and means the
    retention can never be shortened, so losing it would export a weaker table.

    A blockchain table spells its version as part of the hashing clause --
    `HASHING USING "SHA2_512" VERSION "V1"` -- and rejects the bare `VERSION`
    an immutable table takes, with `ORA-02000: missing HASHING keyword`. The
    `HASH_ALGORITHM` column exists only on the blockchain view, so its presence
    is what tells the two apart here.
    """
    parts: list[str] = []
    inactivity = _retention_value(row, "table_inactivity_retention")
    if inactivity is not None:
        parts.append(f"NO DROP UNTIL {int(inactivity)} DAYS IDLE")
    retention = _retention_value(row, "row_retention")
    delete = (
        "NO DELETE"
        if retention is None
        else f"NO DELETE UNTIL {int(retention)} DAYS AFTER INSERT"
    )
    if str(_retention_value(row, "row_retention_locked") or "").upper() == "YES":
        delete += " LOCKED"
    parts.append(delete)
    algorithm = str(_retention_value(row, "hash_algorithm") or "").strip()
    if algorithm:
        parts.append(f'HASHING USING "{algorithm}"')
    version = str(_retention_value(row, "table_version") or "").strip()
    if version:
        parts.append(f'VERSION "{version}"')
    return " ".join(parts)


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


def _ddl_query(database_object: DatabaseObject) -> tuple[str, dict[str, str]]:
    object_type = database_object.object_type.upper()
    if object_type == "JOB":
        return (
            queries.JOB_DDL_QUERY,
            {"object_name": database_object.name},
        )
    if object_type == "MVIEW LOG":
        return (
            queries.MVIEW_LOG_DDL_QUERY,
            {"object_name": database_object.name},
        )
    if object_type == "SCHEDULE":
        return (
            queries.SCHEDULE_DDL_QUERY,
            {"object_name": database_object.name},
        )
    if object_type == "ASSERTION":
        # `DBMS_METADATA` has no handler for the type at all (`ORA-31600`), so the
        # dictionary's own `DEFINITION_SQL` is the source rather than a fallback.
        return (
            queries.ASSERTION_DDL_QUERY,
            {"object_name": database_object.name},
        )
    if object_type == "MLE MODULE":
        # Same `ORA-31600` refusal, and the same answer: `user_mle_modules` carries
        # the language, the version and the source, so one row is the whole
        # statement and no assembly is needed (`#738`).
        return (
            queries.MLE_MODULE_DDL_QUERY,
            {"object_name": database_object.name},
        )
    return (
        queries.DDL_QUERY,
        {
            "object_type": object_type,
            "object_name": database_object.name,
        },
    )
