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
    """Raised when an APEX application owner cannot be resolved to config.

    Carries the facts rather than a finished sentence (`#861`): the caller prints
    them under `WARNING - APP NOT FOUND:` (``owner`` is None) or `WARNING - SCHEMA
    NOT CONFIGURED:`, the headers `export_apex` already names these two cases
    with. The bare sentences this used to hold reached the screen as they were.
    """

    def __init__(self, app_id: int, owner: str | None) -> None:
        self.app_id = app_id
        self.owner = owner
        if owner is None:
            message = f"APP {app_id} is not in any configured APEX schema"
        else:
            message = f"APP {app_id} is owned by {owner}, which is not configured"
        super().__init__(message)


def resolve_configured_apex_owner_schema(
    discovery: ApexDiscovery,
    *,
    app_id: int,
    configured_schemas: list[str],
) -> ApexOwnerResolution:
    owner = discovery.application_owner(app_id)
    if owner is None:
        raise ApexOwnerResolutionError(app_id, None)
    schema_lookup = {schema.upper(): schema for schema in configured_schemas}
    schema = schema_lookup.get(owner.upper())
    if schema is None:
        raise ApexOwnerResolutionError(app_id, owner)
    return ApexOwnerResolution(app_id=app_id, owner=owner, schema=schema)
