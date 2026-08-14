"""Catalog reads and typed discovery results for the recompile module."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from adt_ai.recompile import queries
from adt_ai.shared.db import QueryGateway


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
    # how many objects of this type were invalid before the run and are not any
    # more (#186). The catalog cannot answer this, it only ever knows the state
    # it is asked about, so the runner fills it in from the before/after invalid
    # sets and `overview()` always returns 0. Last field so every positional
    # construction of the catalog-sourced shape keeps working.
    validated: int = 0


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
    error: str
    text: str


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
    # whole, stable across the tool's own refresh, unlike last_refresh_type. The
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
class TrailingObject:
    object_type: str
    object_name: str
    # how many of the object's stored source lines carry trailing whitespace.
    trailing_lines: int


@dataclass(frozen=True)
class TrailingView:
    """One in-scope view and its stored defining text (#122).

    Carries the text because, unlike the user_source path, the trailing-whitespace
    test cannot run in SQL against a LONG, so the text comes back with the row and
    the check happens in Python. Clean views arrive here too and are filtered out
    downstream.
    """

    object_name: str
    view_text: str


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
    ERROR_SOURCE_LINES_QUERY = queries.ERROR_SOURCE_LINES_QUERY
    MATERIALIZED_VIEWS_QUERY = queries.MATERIALIZED_VIEWS_QUERY
    SYNONYMS_QUERY = queries.SYNONYMS_QUERY
    DISABLED_OBJECTS_QUERY = queries.DISABLED_OBJECTS_QUERY
    SCHEDULER_JOBS_QUERY = queries.SCHEDULER_JOBS_QUERY
    OBJECTS_MISSING_PLSCOPE_QUERY = queries.OBJECTS_MISSING_PLSCOPE_QUERY
    TRAILING_OBJECTS_QUERY = queries.TRAILING_OBJECTS_QUERY
    TRAILING_VIEWS_QUERY = queries.TRAILING_VIEWS_QUERY
    OBJECT_SOURCE_QUERY = queries.OBJECT_SOURCE_QUERY
    VIEW_TEXT_QUERY = queries.VIEW_TEXT_QUERY
    VIEW_COLUMNS_QUERY = queries.VIEW_COLUMNS_QUERY
    TRIGGER_STATUS_QUERY = queries.TRIGGER_STATUS_QUERY

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
        native: bool = False,
        interpreted: bool = False,
        optimize_level: int | None = None,
        scope: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> list[RecompileObject]:
        binds = self._scope_binds(
            object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
        )
        binds["force"] = "Y" if force else ""
        # The :drift_* binds narrow a modifier-combined -force sweep to objects whose
        # settings drift from the requested target state; bare -force leaves them
        # neutral (drift_only='N'). Always merged so every bind the query names is set.
        binds.update(
            queries.compile_drift_binds(
                force          = force,
                native         = native,
                interpreted    = interpreted,
                optimize_level = optimize_level,
                scope          = scope,
                warnings       = warnings,
            )
        )
        rows = self.gateway.fetch_all(self.OBJECTS_TO_RECOMPILE_QUERY, binds)
        return [RecompileObject(str(row["OBJECT_TYPE"]), str(row["OBJECT_NAME"])) for row in rows]

    def objects_missing_plscope(self) -> list[RecompileObject]:
        """VALID PL/SQL objects whose PL/Scope settings are not fully ALL.

        Whole-schema scan with no binds, the dependencies refresh recompiles
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
                str(row["ERROR"] or ""),
                str(row["TEXT"] or ""),
            )
            for row in rows
        ]

    def error_source_lines(
        self,
        lookups: Sequence[tuple[str, str, int]],
    ) -> dict[tuple[str, str, int], str]:
        """Stored source for the ``(type, name, line)`` triples the ranking asked for.

        Only the errors that name no object reach here (``ORA-00942``), so an
        object whose errors were already self-describing costs nothing. An empty
        list short-circuits: no query, no connection use.
        """
        if not lookups:
            return {}
        names = sorted({name for _type, name, _line in lookups})
        lines = sorted({line for _type, _name, line in lookups})
        rows = self.gateway.fetch_all(
            self.ERROR_SOURCE_LINES_QUERY,
            {
                "object_names": ",".join(names),
                "source_lines": ",".join(str(line) for line in lines),
            },
        )
        return {
            (str(row["OBJECT_TYPE"]), str(row["OBJECT_NAME"]), int(row["LINE"] or 0)):
                str(row["TEXT"] or "").rstrip("\n")
            for row in rows
        }

    def materialized_views(
        self,
        *,
        object_name: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[MaterializedView]:
        # The MV query binds only name/prefix/ignore, object_type is irrelevant
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
        # Like the MV report, the synonyms report binds only name/prefix/ignore,
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
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[DisabledObject]:
        # Alone among the report-only flags, -disabled spans three object types
        # (CONSTRAINT / INDEX / TRIGGER), so it takes the full standard scope: -type
        # picks which of the three to report, -name filters within them.
        rows = self.gateway.fetch_all(
            self.DISABLED_OBJECTS_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            DisabledObject(
                str(row["OWNER"]),
                str(row["OBJECT_TYPE"]),
                str(row["OBJECT_NAME"]),
                _str_or_none(row.get("TABLE_NAME")),
            )
            for row in rows
        ]

    def trailing_objects(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[TrailingObject]:
        # Unlike the other focused reports, -trailing acts on the same compilable
        # objects as the recompile loop itself, so it takes the standard 4-key scope
        # (-type/-name) instead of a flag-local name pattern.
        rows = self.gateway.fetch_all(
            self.TRAILING_OBJECTS_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            TrailingObject(
                str(row["OBJECT_TYPE"]),
                str(row["OBJECT_NAME"]),
                int(row["TRAILING_LINES"] or 0),
            )
            for row in rows
        ]

    def trailing_views(
        self,
        *,
        object_name: str = "%",
        object_type: str = "%",
        prefix: str = "",
        ignore: str = "",
    ) -> list[TrailingView]:
        """In-scope views with their stored defining text (#122).

        Returns every in-scope view, clean or not, user_views.text is a LONG, so
        SQL cannot do the trailing-whitespace test the user_source query does. The
        caller decides what actually needs rewriting via build_trailing_view_ddl.
        """
        rows = self.gateway.fetch_all(
            self.TRAILING_VIEWS_QUERY,
            self._scope_binds(
                object_name=object_name, object_type=object_type, prefix=prefix, ignore=ignore
            ),
        )
        return [
            TrailingView(str(row["OBJECT_NAME"]), str(row["VIEW_TEXT"] or ""))
            for row in rows
        ]

    def view_text(self, object_name: str) -> str:
        """One view's stored defining text, re-read at rewrite time.

        The authoritative read. ``trailing_views`` fetched a copy during detection,
        but the database is live: this is the text that actually gets rewritten, so
        a change made in between is respected rather than reverted.
        """
        rows = self.gateway.fetch_all(
            self.VIEW_TEXT_QUERY,
            {"object_name": object_name},
        )
        if not rows:
            return ""
        return str(rows[0]["VIEW_TEXT"] or "")

    def view_columns(self, object_name: str) -> list[str]:
        """One view's column names in declaration order."""
        rows = self.gateway.fetch_all(
            self.VIEW_COLUMNS_QUERY,
            {"object_name": object_name},
        )
        return [str(row["COLUMN_NAME"]) for row in rows]

    def object_source(self, object_type: str, object_name: str) -> list[str]:
        """One object's stored source lines, in order, terminators included."""
        rows = self.gateway.fetch_all(
            self.OBJECT_SOURCE_QUERY,
            {"object_type": object_type, "object_name": object_name},
        )
        return [str(row["TEXT"] or "") for row in rows]

    def trigger_status(self, object_name: str) -> str | None:
        """A trigger's ENABLED/DISABLED state, or None when the catalog has no row."""
        rows = self.gateway.fetch_all(
            self.TRIGGER_STATUS_QUERY,
            {"object_name": object_name},
        )
        if not rows:
            return None
        return _str_or_none(rows[0].get("STATUS"))

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
