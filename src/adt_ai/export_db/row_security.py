"""Row-level security artifacts: a schema's VPD policies and application contexts.

ADT #1098. Found on a client migration: 23 enabled policies and two application
contexts existed only in the database, so a schema copied from the repository
came back with every protected table open and both contexts missing. Neither
type is a `user_objects` row of the schema and neither can be dated, so both
ride the GRANT artifact pass the Deep Data Security files ride (`#1063`):
rebuilt on every run, compared with disk, reported on the overview's GRANT row,
and a dropped policy loses its file on the next full run.

Jan, 2026-10-30, picked the layout: `VPD POLICY` into `vpd_policies/`, one file
per table, and `CONTEXT` into `contexts/`, one file per context.

What the dictionary answers, measured on the live 26ai fixture (23.26.3,
SANDBOX, 2026-10-30), is in `queries/row_security.py`. Two things are added to
what `DBMS_METADATA` writes, both so a file replays onto a database that already
holds the object, which is what `CREATE OR REPLACE` gives every other type:

* each `ADD_GROUPED_POLICY` is preceded by a `DROP_GROUPED_POLICY` that ignores
  `ORA-28102` (policy does not exist), since adding a policy twice fails;
* the owner literal becomes `USER` unless `keep_owner` is on, so the file is
  schema-neutral like every other object file.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from adt_ai.export_db.config import _requested_object_type_matches
from adt_ai.export_db.inventory import (
    DatabaseObject,
    ObjectDiscovery,
    missing_dictionary_object,
)
from adt_ai.export_db.normalizer_identifiers import unquote_simple_identifiers
from adt_ai.export_db.queries.row_security import CONTEXT_SEPARATOR
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared.config import is_enabled
from adt_ai.shared.object_types import ROW_SECURITY_OBJECT_TYPES

if TYPE_CHECKING:
    from adt_ai.export_db.request import ExportDbRequest

CONTEXT_TYPE, VPD_POLICY_TYPE = ROW_SECURITY_OBJECT_TYPES

#: The statement `GET_DEPENDENT_DDL('RLS_POLICY')` writes per policy, its first
#: five arguments the quoted names: object schema, object, group, policy and
#: function schema. Anything else is written as it came.
_GROUPED_POLICY = re.compile(
    r"^BEGIN DBMS_RLS\.ADD_GROUPED_POLICY\("
    r"(?P<owner>'\"[^\"]*\"'),(?P<object>'\"[^\"]*\"'),(?P<group>'\"[^\"]*\"'),"
    r"(?P<policy>'\"[^\"]*\"'),(?P<function_owner>'\"[^\"]*\"'),(?P<rest>.*\);) END;$"
)

_CONTEXT_NAME = re.compile(r'CREATE OR REPLACE CONTEXT "([^"]+)"')

#: `ORA-28102: policy does not exist`, the one error the drop ahead of an add
#: may ignore.
_NO_SUCH_POLICY = -28102


def exported_row_security_types(request: ExportDbRequest) -> list[str]:
    """The row-level security types this request writes: configured and selected."""
    configured = {str(key).upper() for key in request.config.get("object_types", {})}
    return [
        object_type
        for object_type in ROW_SECURITY_OBJECT_TYPES
        if object_type in configured
        and _requested_object_type_matches(object_type, request.object_types)
    ]


def row_security_contents(
    request: ExportDbRequest,
    schema: str,
    discovery: ObjectDiscovery,
) -> Iterable[tuple[DatabaseObject, str]]:
    """Yield the schema's policy files, one per table, then its context files."""
    types = exported_row_security_types(request)
    keep_owner = is_enabled(request.config.get("keep_owner", False))
    if VPD_POLICY_TYPE in types:
        for row in _read_policies(discovery):
            table = str(_value(row, "object_name"))
            yield (
                DatabaseObject(schema, VPD_POLICY_TYPE, table),
                render_vpd_policies(
                    table, str(_value(row, "ddl") or ""), schema=schema, keep_owner=keep_owner
                ),
            )
    if CONTEXT_TYPE in types:
        for name, ddl in split_context_ddl(_read_contexts(discovery)).items():
            yield (
                DatabaseObject(schema, CONTEXT_TYPE, name),
                render_context(ddl, schema=schema, keep_owner=keep_owner),
            )


def render_vpd_policies(
    table: str,
    ddl: str,
    schema: str,
    keep_owner: bool = False,
) -> str:
    """One replayable block per policy `GET_DEPENDENT_DDL` wrote for `table`."""
    blocks = []
    for line in ddl.splitlines():
        statement = line.strip()
        if not statement:
            continue
        match = _GROUPED_POLICY.match(statement)
        if match is None:
            blocks.append(f"{statement}\n/\n")
            continue
        owner = _owner(match["owner"], schema, keep_owner)
        function_owner = _owner(match["function_owner"], schema, keep_owner)
        drop_owner = owner if owner == "USER" else f"'{escape_literal(_name(match['owner']))}'"
        drop_arguments = ", ".join(
            [drop_owner]
            + [f"'{escape_literal(_name(match[part]))}'" for part in ("object", "group", "policy")]
        )
        add = (
            f"DBMS_RLS.ADD_GROUPED_POLICY({owner},{match['object']},{match['group']},"
            f"{match['policy']},{function_owner},{match['rest']}"
        )
        blocks.append(
            "BEGIN\n"
            "    BEGIN\n"
            f"        DBMS_RLS.DROP_GROUPED_POLICY({drop_arguments});\n"
            "    EXCEPTION\n"
            "    WHEN OTHERS THEN\n"
            f"        IF SQLCODE != {_NO_SUCH_POLICY} THEN  -- policy does not exist\n"
            "            RAISE;\n"
            "        END IF;\n"
            "    END;\n"
            f"    {add}\n"
            "END;\n"
            "/\n"
        )
    return "".join(blocks)


def split_context_ddl(clob: str) -> dict[str, str]:
    """`{NAME: statement}` from the block's CLOB, one entry per context."""
    contexts: dict[str, str] = {}
    for chunk in clob.split(CONTEXT_SEPARATOR):
        statement = chunk.strip()
        match = _CONTEXT_NAME.search(statement)
        if match:
            contexts[match.group(1)] = statement
    return dict(sorted(contexts.items()))


def render_context(ddl: str, schema: str, keep_owner: bool = False) -> str:
    """The statement with simple names unquoted, owner dropped unless `keep_owner`."""
    statement = ddl.strip().rstrip(";")
    if not keep_owner:
        statement = statement.replace(f'"{schema.upper()}".', "")
    return f"{unquote_simple_identifiers(statement)};\n"


def _owner(literal: str, schema: str, keep_owner: bool) -> str:
    """`USER` for the exported schema's own literal unless `keep_owner`, else as written."""
    if not keep_owner and _name(literal).upper() == schema.upper():
        return "USER"
    return literal


def _name(literal: str) -> str:
    """`'"NAME"'` -> `NAME`."""
    return literal[2:-2]


def _read_policies(discovery: ObjectDiscovery) -> list[dict[str, Any]]:
    """The schema's policy DDL per table, or none where the view is closed.

    Swallowed exactly as the Data Security reads swallow, and only that
    (`missing_dictionary_object`): a lost connection answered with no rows
    would have `auto_delete` remove every committed policy file (`#1094`).
    """
    try:
        return discovery.gateway.fetch_all(ObjectDiscovery.VPD_POLICIES_QUERY)
    except Exception as error:
        if not missing_dictionary_object(error):
            raise
        return []


def _read_contexts(discovery: ObjectDiscovery) -> str:
    try:
        return discovery.gateway.fetch_clob(ObjectDiscovery.CONTEXTS_BLOCK)
    except Exception as error:
        if not missing_dictionary_object(error):
            raise
        return ""


def _value(row: dict[str, Any], column: str) -> Any:
    """One column of one row, whichever case the driver handed the keys back in."""
    if column.upper() in row:
        return row[column.upper()]
    return row.get(column.lower())
