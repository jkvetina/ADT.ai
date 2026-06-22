from __future__ import annotations

import fnmatch
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from adt_ai.db import QueryGateway

if TYPE_CHECKING:
    from adt_ai.export_db.runner import ExportDbRequest

GatewayFactory = Callable[[str], QueryGateway]


def _with_default_layout(config: dict[str, Any]) -> dict[str, Any]:
    if "path_objects" in config:
        return config
    return {**config, "path_objects": "database/<schema>/<object_type>"}

def _cached_gateway_factory(gateway_factory: GatewayFactory) -> GatewayFactory:
    gateways: dict[str, QueryGateway] = {}

    def for_schema(schema: str) -> QueryGateway:
        if schema not in gateways:
            gateways[schema] = gateway_factory(schema)
        return gateways[schema]

    return for_schema

def _configured_object_types(config: dict[str, Any]) -> list[str]:
    raw_types = config.get("object_types", {})
    if not isinstance(raw_types, dict):
        return []
    return [
        object_type
        for object_type in raw_types
        if object_type not in {"DATA", "GRANT"}
    ]

def _requested_object_type_matches(
    object_type: str,
    requested_types: list[str] | None,
) -> bool:
    if requested_types is None:
        return True
    normalized_type = object_type.upper()
    return any(
        fnmatch.fnmatchcase(
            normalized_type,
            requested_type.upper().replace("%", "*").replace("_", "?"),
        )
        for requested_type in requested_types
    )

def _has_runtime_filter(request: ExportDbRequest) -> bool:
    return any(
        (
            request.object_types is not None,
            request.names is not None,
            request.recent_days is not None,
        )
    )

def _split_patterns(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]

def _is_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().upper() in {"1", "TRUE", "Y", "YES", "ON"}
