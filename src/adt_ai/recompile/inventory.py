"""Catalog reads and typed discovery results for the recompile module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adt_ai.db import QueryGateway
from adt_ai.recompile import queries


def _str_or_none(value: Any) -> str | None:
    """Coerce a catalog cell to text, preserving SQL NULLs as ``None``."""
    return None if value is None else str(value)


@dataclass(frozen=True)
class RecompileObject:
    object_type: str
    object_name: str


@dataclass(frozen=True)
class ObjectOverview:
    object_type: str
    total: int
    invalid: int
    missing_plscope_identifiers: int = 0
    missing_plscope_statements: int = 0


@dataclass(frozen=True)
class ObjectError:
    object_type: str
    object_name: str
    errors: int
    error: str | None


@dataclass(frozen=True)
class CompileError:
    id: int
    object_type: str
    object_name: str
    line: int
    position: int | None
    text: str


@dataclass(frozen=True)
class LockedObject:
    object_type: str
    object_name: str
    session_id: int | None
    serial: int | None
    oracle_user: str | None
    os_user: str | None
    machine: str | None
    program: str | None
    lock_mode: str | None


@dataclass(frozen=True)
class MaterializedView:
    object_name: str
    staleness: str | None
    compile_state: str | None
    # when the last refresh *finished* (TO_CHAR of last_refresh_end_time).
    last_refreshed_at: str | None
    # how long that refresh took, in seconds, as Oracle recorded it:
    # ROUND(86400 * (last_refresh_end_time - last_refresh_date)).
    last_timer: int | None
    # the MV's *configured* refresh method (FAST/COMPLETE/FORCE/NEVER), carried
    # whole — stable across the tool's own refresh, unlike last_refresh_type. The
    # report resolves it to F/C; the runner maps it to a DBMS_MVIEW.REFRESH code.
    refresh_method: str | None
    # whether a usable MV log backs a FAST/FORCE refresh (the LOG column, and what
    # resolves a FORCE method to F vs C in the report).
    has_log: bool
    indexes: str | None


@dataclass(frozen=True)
class DisabledObject:
    owner: str
    object_type: str
    object_name: str
    table_name: str | None


@dataclass(frozen=True)
class SchedulerJobRun:
    owner: str
    job_name: str
    last_start_date: str | None
    status: str | None
    run_duration: str | None
    cpu_used: str | None
    count: int
    error: str | None


@dataclass(frozen=True)
class SynonymInfo:
    synonym_name: str
    # the target object's type, taken from the received privilege (g.type); NULL
    # when no privilege is recorded (e.g. a synonym onto an own/public object).
    object_type: str | None
    owner: str | None
    object_name: str | None
    # the privileges this schema holds on the target, collapsed to ALL when the
    # full set is present; NULL when none are recorded.
    privileges: str | None
    # whether those privileges are grantable onward (g.grantable = 'YES').
    is_grantable: bool
    # the target object's validity: VALID / INVALID, or UNKNOWN when all_objects
    # has no matching row.
    status: str | None


class RecompileDiscovery:
    OVERVIEW_QUERY = queries.OVERVIEW_QUERY
    OBJECTS_TO_RECOMPILE_QUERY = queries.OBJECTS_TO_RECOMPILE_QUERY
    ERRORS_SUMMARY_QUERY = queries.ERRORS_SUMMARY_QUERY
    ERRORS_DETAIL_QUERY = queries.ERRORS_DETAIL_QUERY
    LOCKED_OBJECTS_QUERY = queries.LOCKED_OBJECTS_QUERY
    MATERIALIZED_VIEWS_QUERY = queries.MATERIALIZED_VIEWS_QUERY
    SYNONYMS_QUERY = queries.SYNONYMS_QUERY
    DISABLED_OBJECTS_QUERY = queries.DISABLED_OBJECTS_QUERY
    SCHEDULER_JOBS_QUERY = queries.SCHEDULER_JOBS_QUERY
    OBJECTS_MISSING_PLSCOPE_QUERY = queries.OBJECTS_MISSING_PLSCOPE_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway

    def _scope_binds(
        self,
        *,
        object_name: str,
        object_type: str,
        prefix: str,
        ignore: str,
    ) -> dict[str, Any]:
        return {
            "object_name"    : object_name,
            "object_type"    : object_type,
            "objects_prefix" : prefix,
            "objects_ignore" : ignore,
        }

    def overview(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[ObjectOverview]:
        rows = self.gateway.fetch_all(
            self.OVERVIEW_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            ObjectOverview(
                str(row["OBJECT_TYPE"]),
                int(row["TOTAL"] or 0),
                int(row["INVALID"] or 0),
                int(row.get("MISSING_PLSCOPE_IDENTIFIERS") or 0),
                int(row.get("MISSING_PLSCOPE_STATEMENTS") or 0),
            )
            for row in rows
        ]

    def objects_to_recompile(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
        force: bool = False,
    ) -> list[RecompileObject]:
        binds = self._scope_binds(
            object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
        )
        binds["force"] = "Y" if force else ""
        rows = self.gateway.fetch_all(self.OBJECTS_TO_RECOMPILE_QUERY, binds)
        return [RecompileObject(str(row["OBJECT_TYPE"]), str(row["OBJECT_NAME"])) for row in rows]

    def objects_missing_plscope(self) -> list[RecompileObject]:
        """VALID PL/SQL objects whose PL/Scope settings are not fully ALL.

        Whole-schema scan with no binds — the dependencies refresh recompiles
        each returned object with ``scope=["ALL"]`` before pulling the
        identifier / statement mirror tables. Reuses the recompile module's
        catalog read; does not touch ``RecompileRunner`` or open a connection.
        """
        rows = self.gateway.fetch_all(self.OBJECTS_MISSING_PLSCOPE_QUERY, {})
        return [RecompileObject(str(row["OBJECT_TYPE"]), str(row["OBJECT_NAME"])) for row in rows]

    def errors_summary(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[ObjectError]:
        rows = self.gateway.fetch_all(
            self.ERRORS_SUMMARY_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            ObjectError(
                str(row["OBJECT_TYPE"]),
                str(row["OBJECT_NAME"]),
                int(row["ERRORS"] or 0),
                (str(row["ERROR"]) if row.get("ERROR") is not None else None),
            )
            for row in rows
        ]

    def errors_detail(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[CompileError]:
        rows = self.gateway.fetch_all(
            self.ERRORS_DETAIL_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            CompileError(
                int(row["ID"] or 0),
                str(row["OBJECT_TYPE"]),
                str(row["OBJECT_NAME"]),
                int(row["LINE"] or 0),
                (int(row["POSITION"]) if row.get("POSITION") is not None else None),
                str(row["TEXT"] or ""),
            )
            for row in rows
        ]

    def locked_objects(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[LockedObject]:
        rows = self.gateway.fetch_all(
            self.LOCKED_OBJECTS_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            LockedObject(
                str(row["OBJECT_TYPE"]),
                str(row["OBJECT_NAME"]),
                (int(row["SID"]) if row.get("SID") is not None else None),
                (int(row["SERIAL#"]) if row.get("SERIAL#") is not None else None),
                _str_or_none(row.get("ORACLE_USER")),
                _str_or_none(row.get("OS_USER")),
                _str_or_none(row.get("MACHINE")),
                _str_or_none(row.get("PROGRAM")),
                _str_or_none(row.get("LOCK_MODE")),
            )
            for row in rows
        ]

    def materialized_views(
        self,
        *,
        object_name: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[MaterializedView]:
        # The MV query binds only name/prefix/ignore — object_type is irrelevant
        # because -mviews opts materialized views in explicitly.
        binds = {
            "object_name"    : object_name,
            "objects_prefix" : prefix,
            "objects_ignore" : ignore,
        }
        rows = self.gateway.fetch_all(self.MATERIALIZED_VIEWS_QUERY, binds)
        return [
            MaterializedView(
                str(row["OBJECT_NAME"]),
                _str_or_none(row.get("STALENESS")),
                _str_or_none(row.get("COMPILE_STATE")),
                _str_or_none(row.get("LAST_REFRESHED_AT")),
                (int(row["LAST_TIMER"]) if row.get("LAST_TIMER") is not None else None),
                _str_or_none(row.get("REFRESH_METHOD")),
                (str(row.get("HAS_LOG")).strip().upper() == "Y"),
                (str(row["INDEXES"]) if row.get("INDEXES") else None),
            )
            for row in rows
        ]

    def synonyms(
        self,
        *,
        object_name: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[SynonymInfo]:
        # Like the MV report, the synonyms report binds only name/prefix/ignore —
        # object_type is irrelevant because -synonyms opts synonyms in explicitly.
        binds = {
            "object_name"    : object_name,
            "objects_prefix" : prefix,
            "objects_ignore" : ignore,
        }
        rows = self.gateway.fetch_all(self.SYNONYMS_QUERY, binds)
        return [
            SynonymInfo(
                str(row["SYNONYM_NAME"]),
                _str_or_none(row.get("OBJECT_TYPE")),
                _str_or_none(row.get("OWNER")),
                _str_or_none(row.get("OBJECT_NAME")),
                _str_or_none(row.get("PRIVILEGES")),
                (str(row.get("IS_GRANTABLE")).strip().upper() == "Y"),
                _str_or_none(row.get("STATUS")),
            )
            for row in rows
        ]

    def disabled_objects(
        self,
        *,
        object_name: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[DisabledObject]:
        # Like the MV and synonym reports, the disabled-object report binds only
        # name/prefix/ignore: the flag opts constraints, indexes, and triggers in
        # explicitly.
        binds = {
            "object_name"    : object_name,
            "objects_prefix" : prefix,
            "objects_ignore" : ignore,
        }
        rows = self.gateway.fetch_all(self.DISABLED_OBJECTS_QUERY, binds)
        return [
            DisabledObject(
                str(row["OWNER"]),
                str(row["OBJECT_TYPE"]),
                str(row["OBJECT_NAME"]),
                _str_or_none(row.get("TABLE_NAME")),
            )
            for row in rows
        ]

    def scheduler_jobs(
        self,
        *,
        object_name: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[SchedulerJobRun]:
        # Like the other focused reports, the scheduler-job report binds only
        # name/prefix/ignore: the flag opts scheduler jobs in explicitly.
        binds = {
            "object_name"    : object_name,
            "objects_prefix" : prefix,
            "objects_ignore" : ignore,
        }
        rows = self.gateway.fetch_all(self.SCHEDULER_JOBS_QUERY, binds)
        return [
            SchedulerJobRun(
                str(row["OWNER"]),
                str(row["JOB_NAME"]),
                _str_or_none(row.get("LAST_START_DATE")),
                _str_or_none(row.get("STATUS")),
                _str_or_none(row.get("RUN_DURATION")),
                _str_or_none(row.get("CPU_USED")),
                int(row.get("COUNT", row.get("COUNT_", 0)) or 0),
                _str_or_none(row.get("ERROR", row.get("ERROR_"))),
            )
            for row in rows
        ]
