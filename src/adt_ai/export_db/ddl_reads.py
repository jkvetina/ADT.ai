"""The DDL text an object's file is rendered from (split out of `inventory.py`, `#923`).

Three sources answer it, by type: `DBMS_METADATA.GET_DDL` for most of them, one
dictionary view for the two types `GET_DDL` refuses outright but a single row can
describe (`ASSERTION`, `MLE MODULE`), and the statement `dictionary_ddl.py`
assembles for the three spread across several views. Beside them sits the one
clause `GET_DDL` leaves out under ADT's own transform settings, the retention of
an immutable or blockchain table.

An object with no DDL at all is an error here, never an empty answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from adt_ai.export_db import queries
from adt_ai.export_db.dictionary_ddl import ASSEMBLED_OBJECT_TYPES, assemble_ddl, with_owner

if TYPE_CHECKING:
    from adt_ai.export_db.inventory import DatabaseObject
    from adt_ai.shared.db import QueryGateway


class ObjectDroppedError(LookupError):
    """The object was listed, and by the time its DDL was read it was gone.

    Raised rather than answered with "" so the runner records it the way it
    records an object Oracle refuses (`#917`): listed under the same warning,
    nothing written, the export carrying on. The empty answer it replaces
    reached the writer, which turned it into a TABLE file holding `;`, a
    SEQUENCE file holding only its DROP guard, or an empty PACKAGE BODY.
    """

    def __init__(self, database_object: DatabaseObject) -> None:
        super().__init__(
            f"no DDL came back for {database_object.object_type} {database_object.name} "
            f"in {database_object.schema}; it was most likely dropped after the listing"
        )
        self.database_object = database_object


def read_ddl(gateway: QueryGateway, database_object: DatabaseObject) -> str:
    """The object's DDL, or `ObjectDroppedError` when none came back.

    No row and a row carrying no text are the same answer: there is nothing to
    write, and a file written from nothing is worse than no file.
    """
    object_type = database_object.object_type.upper()
    if object_type in ASSEMBLED_OBJECT_TYPES:
        # Three of the four types `DBMS_METADATA` refuses are more than one row
        # of one view, so their statement is assembled rather than selected
        # (`#738`). Everything downstream still sees a plain DDL string.
        ddl = with_owner(
            assemble_ddl(object_type, database_object.name, gateway),
            database_object.schema,
        )
    else:
        query, params = _ddl_query(database_object)
        rows = gateway.fetch_all(query, params)
        ddl = str(rows[0].get("DDL") or rows[0].get("ddl") or "") if rows else ""
    if not ddl.strip():
        raise ObjectDroppedError(database_object)
    return ddl


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


def _retention_value(row: dict[str, Any], column: str) -> Any:
    return row.get(column.upper(), row.get(column.lower()))


def render_retention(row: dict[str, Any]) -> str:
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
