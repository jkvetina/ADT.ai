from __future__ import annotations

from dataclasses import dataclass

from adt_ai.export_apex.inventory import ApexDiscovery

# Moved from `shared/apex_owner.py` (#670): this module resolves an APEX
# application owner against `ApexDiscovery`, an `export_apex` type, so it
# belongs in `export_apex` rather than `shared` -- `shared/` never imports a
# command package (`shared/file_list.py`).


@dataclass(frozen=True)
class ApexOwnerResolution:
    app_id: int
    owner: str
    schema: str


class ApexOwnerResolutionError(Exception):
    """Raised when an APEX application owner cannot be resolved to config."""


def resolve_configured_apex_owner_schema(
    discovery: ApexDiscovery,
    *,
    app_id: int,
    configured_schemas: list[str],
) -> ApexOwnerResolution:
    owner = discovery.application_owner(app_id)
    if owner is None:
        raise ApexOwnerResolutionError(
            f"APP {app_id} was not found in any configured APEX schema."
        )
    schema_lookup = {schema.upper(): schema for schema in configured_schemas}
    schema = schema_lookup.get(owner.upper())
    if schema is None:
        raise ApexOwnerResolutionError(
            f"APP {app_id} is owned by schema {owner}, "
            "which is not configured for this environment."
        )
    return ApexOwnerResolution(app_id=app_id, owner=owner, schema=schema)
