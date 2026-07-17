from __future__ import annotations

import fnmatch
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from adt_ai.export_db import queries
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.sql_like import matches_sql_like


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
    DBMS_METADATA_SETUP_QUERY = queries.DBMS_METADATA_SETUP_QUERY
    GRANTS_MADE_QUERY      = queries.GRANTS_MADE_QUERY
    GRANTS_RECEIVED_QUERY  = queries.GRANTS_RECEIVED_QUERY
    USER_PRIVILEGES_QUERY  = queries.USER_PRIVILEGES_QUERY
    DIRECTORIES_QUERY      = queries.DIRECTORIES_QUERY
    COMMENTS_QUERY         = queries.COMMENTS_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway
        self._comments_by_schema: dict[str, dict[tuple[str, str], list[dict[str, Any]]]] = {}
        self._comment_cache_keys: dict[str, tuple[str, str, str, str]] = {}

    def discover(
        self,
        schema: str,
        object_types: Iterable[str] | None = None,
        names: Iterable[str] | None = None,
        prefix: str | None = None,
        ignore: Iterable[str] | None = None,
        recent_days: int | None = None,
        prefer_exact_names: bool = True,
    ) -> list[DatabaseObject]:
        filters = _ObjectFilters(
            object_types = _normalize_list(object_types),
            names        = _normalize_list(names),
            prefix       = _normalize_patterns(prefix),
            ignore       = _normalize_list(ignore) or [],
        )
        exact_names = prefer_exact_names and has_exact_name_filter(filters.names)
        rows = self._object_rows(schema, filters, recent_days, exact_names=exact_names)
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
            objects.extend(self._discover_indexes(schema, filters, recent_days))
        if _includes_object_type("JOB", filters.object_types) and recent_days is None:
            objects.extend(self._discover_jobs(schema, filters))
        if _includes_object_type("MVIEW LOG", filters.object_types) and recent_days is None:
            objects.extend(self._discover_mview_logs(schema, filters))
        return objects

    def _object_rows(
        self,
        schema: str,
        filters: _ObjectFilters,
        recent_days: int | None,
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
                "object_type_filter": _query_pattern_list(object_types, default="%"),
            },
        )

    def _discover_jobs(
        self,
        schema: str,
        filters: _ObjectFilters,
    ) -> list[DatabaseObject]:
        rows = self.gateway.fetch_all(
            self.JOBS_QUERY,
            {
                "schema": schema,
            },
        )
        return [
            DatabaseObject(schema, "JOB", str(row["OBJECT_NAME"]))
            for row in rows
            if str(row.get("SCHEDULE_TYPE") or "").upper() != "IMMEDIATE"
            if filters.matches("JOB", str(row["OBJECT_NAME"]))
        ]

    def _discover_mview_logs(
        self,
        schema: str,
        filters: _ObjectFilters,
    ) -> list[DatabaseObject]:
        rows = self.gateway.fetch_all(
            self.MVIEW_LOGS_QUERY,
            {
                "schema": schema,
            },
        )
        return [
            DatabaseObject(schema, "MVIEW LOG", str(row["OBJECT_NAME"]))
            for row in rows
            if filters.matches("MVIEW LOG", str(row["OBJECT_NAME"]))
        ]

    def _discover_indexes(
        self,
        schema: str,
        filters: _ObjectFilters,
        recent_days: int | None,
    ) -> list[DatabaseObject]:
        rows = self.gateway.fetch_all(
            self.INDEXES_QUERY,
            {
                "schema": schema,
                "recent_days": recent_days,
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

    def directories(self, schema: str) -> list[dict[str, Any]]:
        return self.gateway.fetch_all(self.DIRECTORIES_QUERY)

    def authors_objects(self, audit: Any, authors: Iterable[str]) -> set[str]:
        """Return the uppercased object names attributed to ``authors`` in the audit source."""
        query = queries.audit_authors_query(
            audit.source,
            audit.object_name_column,
            audit.changed_by_column,
        )
        rows = self.gateway.fetch_all(
            query,
            {"authors": _query_pattern_list(_normalize_list(authors), default="")},
        )
        return {
            str(row.get("OBJECT_NAME") or row.get("object_name") or "").upper()
            for row in rows
        }

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

    def ddl(self, database_object: DatabaseObject) -> str:
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


def _query_pattern_list(values: Iterable[str] | None, default: str) -> str:
    if values is None:
        return default
    normalized = [str(value).upper() for value in values if str(value).strip()]
    return ",".join(normalized) if normalized else default


@dataclass(frozen=True)
class _ObjectFilters:
    object_types : list[str] | None = None
    names        : list[str] | None = None
    prefix       : list[str] | None = None
    ignore       : list[str] | None = None

    def matches(self, object_type: str, object_name: str) -> bool:
        if _is_old_adt_system_generated_object(object_name):
            return False
        if self.object_types and not _matches_any(object_type, self.object_types):
            return False
        if self.names and not _matches_any(object_name, self.names):
            return False
        if self.prefix and not _matches_any(object_name, self.prefix):
            return False
        return not (self.ignore and _matches_any(object_name, self.ignore))

    def matches_exact(self, object_type: str, object_name: str) -> bool:
        if _is_old_adt_system_generated_object(object_name):
            return False
        if self.object_types and not _matches_any(object_type, self.object_types):
            return False
        if self.names and object_name.upper() not in set(self.names):
            return False
        if self.prefix and not _matches_any(object_name, self.prefix):
            return False
        return not (self.ignore and _matches_any(object_name, self.ignore))

    def matches_index(self, index_name: str, table_name: str) -> bool:
        if (
            _is_old_adt_system_generated_object(index_name)
            or _is_old_adt_system_generated_index(index_name)
        ):
            return False
        if self.object_types and not _matches_any("INDEX", self.object_types):
            return False
        if self.names and not _matches_any_of([index_name, table_name], self.names):
            return False
        if self.prefix and not _matches_any_of([index_name, table_name], self.prefix):
            return False
        return not (self.ignore and _matches_any_of([index_name, table_name], self.ignore))


def _normalize_list(values: Iterable[str] | None) -> list[str] | None:
    if values is None:
        return None
    return [value.upper() for value in values]


def _normalize_patterns(values: Iterable[str] | str | None) -> list[str] | None:
    if values is None:
        return None
    if isinstance(values, str):
        return [value.strip().upper() for value in values.split(",") if value.strip()]
    return [value.upper() for value in values]


def has_exact_name_filter(names: Iterable[str] | None) -> bool:
    normalized = _normalize_list(names)
    return bool(normalized) and all(not _has_wildcard(name) for name in normalized)


def _has_wildcard(pattern: str) -> bool:
    return any(character in pattern for character in "%*?")


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    return any(_matches_like(value, pattern) for pattern in patterns)


def _matches_any_of(values: Iterable[str], patterns: Iterable[str]) -> bool:
    return any(_matches_any(value, patterns) for value in values if value)


def _matches_like(value: str, pattern: str) -> bool:
    return matches_sql_like(value, pattern)


def _includes_object_type(object_type: str, object_types: Iterable[str] | None) -> bool:
    if object_types is None:
        return True
    return _matches_any(object_type, object_types)


def _user_object_types(object_types: Iterable[str] | None) -> list[str] | None:
    if object_types is None:
        return None
    return [
        object_type
        for object_type in object_types
        if _matches_like(object_type, "%")
        and object_type.upper() not in {"INDEX", "JOB", "MVIEW LOG"}
    ]


def _is_old_adt_eligible_index(row: dict[str, Any]) -> bool:
    return (
        str(row.get("GENERATED") or "").upper() == "N"
        and str(row.get("CONSTRAINT_INDEX") or "").upper() == "NO"
        and not row.get("CONSTRAINT_NAME")
    )


def _is_old_adt_system_generated_object(object_name: str) -> bool:
    name = object_name.upper()
    return (
        name.startswith("SYS_")
        or name.startswith("ISEQ$$_")
        or name.startswith("BIN$")
        or (name.startswith("ST") and name.endswith("="))
    )


def _is_old_adt_system_generated_index(object_name: str) -> bool:
    return fnmatch.fnmatchcase(object_name.upper(), "SYS*$$")


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
    return (
        queries.DDL_QUERY,
        {
            "object_type": object_type,
            "object_name": database_object.name,
        },
    )
