from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from adt_ai.db import QueryGateway
from adt_ai.export_apex import queries
from adt_ai.row_values import row_value


@dataclass(frozen=True)
class ApexWorkspace:
    workspace   : str
    workspace_id: int | None
    owners      : int | None
    applications: int | None
    developers  : int | None


@dataclass(frozen=True)
class ApexOwnerCount:
    owner       : str
    applications: int


@dataclass(frozen=True)
class ApexApplication:
    owner       : str
    workspace   : str
    workspace_id: int | None
    app_group   : str
    app_id      : int
    app_alias   : str
    app_name    : str
    pages       : int | None
    updated_at  : str


class ApexDiscovery:
    APPLICATIONS_QUERY      = queries.APPLICATIONS_QUERY
    APPLICATION_OWNER_QUERY = queries.APPLICATION_OWNER_QUERY
    OWNER_APP_COUNTS_QUERY  = queries.OWNER_APP_COUNTS_QUERY
    WORKSPACES_QUERY        = queries.WORKSPACES_QUERY

    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway

    def owner_app_counts(
        self,
        owners: Iterable[str] | None = None,
        max_app_id: int | None = None,
    ) -> list[ApexOwnerCount]:
        rows = self.gateway.fetch_all(
            self.OWNER_APP_COUNTS_QUERY,
            {
                "owners": _pipe_list(owners),
                "max_app_id": max_app_id,
            },
        )
        return [_owner_count_from_row(row) for row in rows]

    def application_owner(self, app_id: str | int) -> str | None:
        rows = self.gateway.fetch_all(
            self.APPLICATION_OWNER_QUERY,
            {
                "app_id": app_id,
            },
        )
        if not rows:
            return None
        owner = row_value(rows[0], "OWNER")
        return str(owner) if owner else None

    def workspaces(
        self,
        workspace: str | None = None,
        schemas: Iterable[str] | None = None,
        max_app_id: int | None = None,
    ) -> list[ApexWorkspace]:
        rows = self.gateway.fetch_all(
            self.WORKSPACES_QUERY,
            {
                "workspace": workspace,
                "schemas": _pipe_list(schemas),
                "max_app_id": max_app_id,
            },
        )
        return [_workspace_from_row(row) for row in rows]

    def applications(
        self,
        owner: str,
        workspace: str | None = None,
        group: str | None = None,
        app_ids: Iterable[str | int] | None = None,
        recent_days: int | None = None,
        max_app_id: int | None = None,
    ) -> list[ApexApplication]:
        rows = self.gateway.fetch_all(
            self.APPLICATIONS_QUERY,
            {
                "owner": owner,
                "workspace": workspace,
                "group_id": group,
                "app_id": _pipe_list(app_ids),
                "recent": recent_days,
                "max_app_id": max_app_id,
            },
        )
        return [_application_from_row(row) for row in rows]


def _workspace_from_row(row: dict[str, Any]) -> ApexWorkspace:
    return ApexWorkspace(
        workspace    = str(row_value(row, "WORKSPACE") or ""),
        workspace_id = _int_or_none(row_value(row, "WORKSPACE_ID")),
        owners       = _int_or_none(row_value(row, "OWNERS")),
        applications = _int_or_none(row_value(row, "APPLICATIONS")),
        developers   = _int_or_none(row_value(row, "DEVELOPERS")),
    )


def _owner_count_from_row(row: dict[str, Any]) -> ApexOwnerCount:
    return ApexOwnerCount(
        owner        = str(row_value(row, "OWNER") or ""),
        applications = int(row_value(row, "APP_COUNT") or 0),
    )


def _application_from_row(row: dict[str, Any]) -> ApexApplication:
    return ApexApplication(
        owner        = str(row_value(row, "OWNER") or ""),
        workspace    = str(row_value(row, "WORKSPACE") or ""),
        workspace_id = _int_or_none(row_value(row, "WORKSPACE_ID")),
        app_group    = str(row_value(row, "APP_GROUP") or ""),
        app_id       = int(row_value(row, "APP_ID") or 0),
        app_alias    = str(row_value(row, "APP_ALIAS") or ""),
        app_name     = str(row_value(row, "APP_NAME") or ""),
        pages        = _int_or_none(row_value(row, "PAGES")),
        updated_at   = str(row_value(row, "UPDATED_AT") or ""),
    )


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _pipe_list(values: Iterable[str | int] | None) -> str | None:
    if not values:
        return None
    normalized = [
        str(value).strip()
        for value in values
        if str(value).strip()
    ]
    return "|".join(normalized) or None
