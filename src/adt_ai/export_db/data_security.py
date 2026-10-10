"""Deep Data Security artifacts: a schema's Data Grants and the Data Roles they name.

ADT #1063. Neither type is a `user_objects` row and neither can be dated, so they
ride the GRANT artifact pass rather than discovery (Jan, 2026-10-09): rebuilt on
every run, compared with disk, reported on the overview's GRANT row, and a dropped
grant loses its file on the next full run.

The statement is rebuilt here because nothing else writes one: `DBMS_METADATA`
answers `ORA-31600` for both types and SQLcl 26.3's `ddl` answers `Object not
found`. What the dictionary keeps decides what can be rebuilt, measured on the
live 26ai fixture (23.26.3.0.0, SANDBOX, 2026-10-09):

* a grant with no `WHERE` stores the predicate `1 = 1`, so that predicate is the
  absence of one;
* `ALL COLUMNS EXCEPT` comes back as one row per granted column per excepted
  column, so the excepted set is what gets written back;
* a cross-table grant stores its join predicate and nothing of the
  `WHEN ... GRANTED ON <parent>` clause it was created with, so it cannot be
  rebuilt and is skipped with a warning instead;
* a role's IAM mapping and default state are in `dba_data_roles` only. When that
  view is readable the role is written in full; when it is not, a stub that
  creates the role by name and changes nothing about an existing one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from adt_ai.export_db.config import _requested_object_type_matches
from adt_ai.export_db.inventory import DatabaseObject, ObjectDiscovery
from adt_ai.shared.config import is_enabled
from adt_ai.shared.object_types import DATA_SECURITY_OBJECT_TYPES

if TYPE_CHECKING:
    from adt_ai.export_db.request import ExportDbRequest

DATA_ROLE_TYPE, DATA_GRANT_TYPE = DATA_SECURITY_OBJECT_TYPES

#: The order Oracle's own grammar lists them in, so a rebuilt list never depends
#: on the order the view returned its rows in.
_PRIVILEGE_ORDER = ("SELECT", "INSERT", "UPDATE", "DELETE")

#: What the dictionary stores for a grant created without a `WHERE`.
_NO_PREDICATE = "1 = 1"

STUB_COMMENT = "-- DBA_DATA_ROLES is not readable: mapping and default state not exported"


def exported_data_security_types(request: ExportDbRequest) -> list[str]:
    """The Deep Data Security types this request writes: configured and selected."""
    configured = {str(key).upper() for key in request.config.get("object_types", {})}
    return [
        object_type
        for object_type in DATA_SECURITY_OBJECT_TYPES
        if object_type in configured
        and _requested_object_type_matches(object_type, request.object_types)
    ]


def data_security_contents(
    request: ExportDbRequest,
    schema: str,
    discovery: ObjectDiscovery,
) -> Iterable[tuple[DatabaseObject, str]]:
    """Yield the schema's Data Grant and Data Role files, recording what was skipped."""
    types = exported_data_security_types(request)
    if not types:
        return
    keep_owner = is_enabled(request.config.get("keep_owner", False))
    grants = _grouped(_read(discovery, ObjectDiscovery.DATA_GRANTS_QUERY) or [])
    skipped = sorted(name for name, rows in grants.items() if _is_cross_table(rows))
    discovery.skipped_data_grants[schema] = skipped
    rebuilt = {name: rows for name, rows in grants.items() if name not in skipped}
    if DATA_GRANT_TYPE in types:
        for name, rows in rebuilt.items():
            yield (
                DatabaseObject(schema, DATA_GRANT_TYPE, name),
                render_data_grant(name, rows, schema=schema, keep_owner=keep_owner),
            )
    if DATA_ROLE_TYPE not in types:
        return
    role_names = sorted(
        {
            str(_value(row, "grantee"))
            for rows in rebuilt.values()
            for row in rows
            if str(_value(row, "grantee_type") or "").upper() == DATA_ROLE_TYPE
        }
    )
    if not role_names:
        return
    role_rows = _read(discovery, ObjectDiscovery.DATA_ROLES_QUERY)
    described = {str(_value(row, "data_role")): row for row in role_rows or []}
    for name in role_names:
        yield (
            DatabaseObject(schema, DATA_ROLE_TYPE, name),
            render_data_role(name, described.get(name) if role_rows is not None else None),
        )


def render_data_grant(
    name: str,
    rows: list[dict[str, Any]],
    schema: str,
    keep_owner: bool = False,
) -> str:
    """One `CREATE OR REPLACE DATA GRANT` statement from its dictionary rows."""
    first = rows[0]
    owner = str(_value(first, "object_owner") or schema)
    on_object = str(_value(first, "object_name")).lower()
    if keep_owner or owner.upper() != schema.upper():
        on_object = f"{owner.lower()}.{on_object}"
    grant_name = f"{schema.lower()}.{name.lower()}" if keep_owner else name.lower()
    lines = [
        f"CREATE OR REPLACE DATA GRANT {grant_name}",
        f"    AS {_privileges(rows)}",
        f"    ON {on_object}",
    ]
    predicate = str(_value(first, "predicate") or "").strip()
    if predicate and predicate != _NO_PREDICATE:
        lines.append(f"    WHERE {predicate}")
    grantees = sorted({str(_value(row, "grantee")).lower() for row in rows})
    lines.append(f"    TO {', '.join(grantees)}")
    for clause, column in (("START TIME", "start_time"), ("END TIME", "end_time")):
        value = _value(first, column)
        if value:
            lines.append(f"    {clause} TIMESTAMP '{value}'")
    return "\n".join(lines) + ";\n"


def render_data_role(name: str, row: dict[str, Any] | None) -> str:
    """The role in full from `dba_data_roles`, or a stub when that view is closed."""
    role = name.lower()
    if row is None:
        return f"{STUB_COMMENT}\nCREATE DATA ROLE IF NOT EXISTS {role};\n"
    mapped_to = _value(row, "mapped_to")
    if mapped_to:
        return f"CREATE OR REPLACE DATA ROLE {role} MAPPED TO '{mapped_to}';\n"
    disabled = str(_value(row, "enabled") or "Y").upper() == "N"
    return f"CREATE OR REPLACE DATA ROLE {role}{' DISABLED' if disabled else ''};\n"


def _privileges(rows: list[dict[str, Any]]) -> str:
    """`SELECT, UPDATE (a, b)`, each column list in the table's own column order."""
    columns: dict[str, dict[str, int]] = {}
    excepted: dict[str, dict[str, int]] = {}
    for row in rows:
        privilege = str(_value(row, "privilege")).upper()
        columns.setdefault(privilege, {})
        excepted.setdefault(privilege, {})
        if _value(row, "except_column"):
            excepted[privilege][str(_value(row, "except_column")).lower()] = _id(
                row, "except_column_id"
            )
        elif _value(row, "column_name"):
            columns[privilege][str(_value(row, "column_name")).lower()] = _id(row, "column_id")
    rendered = []
    for privilege in sorted(columns, key=_privilege_key):
        if excepted[privilege]:
            names = _in_column_order(excepted[privilege])
            rendered.append(f"{privilege} (ALL COLUMNS EXCEPT {names})")
        elif columns[privilege]:
            rendered.append(f"{privilege} ({_in_column_order(columns[privilege])})")
        else:
            rendered.append(privilege)
    return ", ".join(rendered)


def _in_column_order(columns: dict[str, int]) -> str:
    return ", ".join(sorted(columns, key=lambda name: (columns[name], name)))


def _privilege_key(privilege: str) -> tuple[int, str]:
    known = privilege in _PRIVILEGE_ORDER
    return (_PRIVILEGE_ORDER.index(privilege) if known else len(_PRIVILEGE_ORDER), privilege)


def _id(row: dict[str, Any], column: str) -> int:
    value = _value(row, column)
    return int(value) if value is not None else 0


def _is_cross_table(rows: list[dict[str, Any]]) -> bool:
    return any(str(_value(row, "cross_table") or "").upper() == "Y" for row in rows)


def _grouped(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(_value(row, "grant_name")), []).append(row)
    return grouped


def _read(discovery: ObjectDiscovery, query: str) -> list[dict[str, Any]] | None:
    """The view's rows, or None where the database has no such view or denies it.

    Swallowed as `schema_privileges` swallows: the grant view is 26ai, ADT exports
    from older releases, and `dba_data_roles` is closed to most schemas. Each is an
    answer about this database rather than an error in the export.
    """
    try:
        return discovery.gateway.fetch_all(query)
    except Exception:
        return None


def _value(row: dict[str, Any], column: str) -> Any:
    """One column of one row, whichever case the driver handed the keys back in."""
    if column.upper() in row:
        return row[column.upper()]
    return row.get(column.lower())
