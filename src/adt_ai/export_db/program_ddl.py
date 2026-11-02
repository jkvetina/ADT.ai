"""A scheduler program's file, rendered from the dictionary (ADT #1100).

`DBMS_METADATA.GET_DDL('PROCOBJ', ...)` answers half a program: measured on the
local container, an enabled program with one argument came back as
`create_program(..., 1, FALSE, ...)` with no `DEFINE_PROGRAM_ARGUMENT` and no
`ENABLE`. A replay left the program disabled with its argument undefined, so a
job leaning on it could not run, and with no drop guard a second replay failed
with ORA-27477. `user_scheduler_programs` and `user_scheduler_program_args`
hold all of it, so the file is written from them, in the job file's own shape.

Existing programs are updated in place (ADT #1112). DISABLE with force leaves
referencing jobs enabled, unlike DROP_PROGRAM, and preserves their grants and
run counters. Arguments are replaced while the program is disabled; the exported
program state is restored afterwards. No dependent-job inventory is needed.
"""

from __future__ import annotations

from collections.abc import Mapping
from textwrap import indent
from typing import Any, Protocol

from adt_ai.export_db import queries
from adt_ai.shared.row_values import row_value
from adt_ai.shared.sql_identifiers import sql_name


class _Gateway(Protocol):
    def fetch_all(self, sql: str, binds: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


def program_ddl(object_name: str, gateway: _Gateway) -> str:
    """The whole file for one program, or "" when it is gone since the listing."""
    binds = {"object_name": object_name}
    programs = gateway.fetch_all(queries.PROGRAM_QUERY, binds)
    if not programs:
        return ""
    program = programs[0]
    arguments = gateway.fetch_all(queries.PROGRAM_ARGUMENTS_QUERY, binds)
    create = _call("CREATE_PROGRAM", [
        ("program_name", "in_program_name"),
        ("program_type", _text(row_value(program, "PROGRAM_TYPE"))),
        ("program_action", _text(row_value(program, "PROGRAM_ACTION"))),
        ("number_of_arguments", str(int(row_value(program, "NUMBER_OF_ARGUMENTS") or 0))),
        ("enabled", "FALSE"),
        ("comments", _text(row_value(program, "COMMENTS"))),
    ])
    attributes = []
    for attribute in ("program_type", "program_action", "number_of_arguments", "comments"):
        value = row_value(program, attribute.upper())
        parameters = [("name", "in_program_name"), ("attribute", _text(attribute))]
        if value is not None and value != "":
            attributes.append(_call("SET_ATTRIBUTE", [*parameters, ("value", _text(value))]))
        else:
            attributes.append(_call("SET_ATTRIBUTE_NULL", parameters))
    enabled = str(row_value(program, "ENABLED") or "").upper() == "TRUE"
    name = sql_name(object_name, role="program name")
    return queries.PROGRAM_FILE.format(
        name=name,
        key=_text(object_name),
        create=indent(create, "    "),
        attributes=indent("".join(attributes), "    "),
        blocks="    --\n".join(_argument(argument) for argument in arguments),
        enable="    DBMS_SCHEDULER.ENABLE(in_program_name);\n" if enabled else "",
    )


def _argument(row: Mapping[str, Any]) -> str:
    """One argument's definition, a metadata one through its own call."""
    position = ("argument_position", str(int(row_value(row, "ARGUMENT_POSITION"))))
    name = ("argument_name", _text(row_value(row, "ARGUMENT_NAME")))
    metadata = row_value(row, "METADATA_ATTRIBUTE")
    if metadata:
        return _call("DEFINE_METADATA_ARGUMENT", [
            ("program_name", "in_program_name"),
            ("metadata_attribute", _text(metadata)),
            position,
            name,
        ])
    out = str(row_value(row, "OUT_ARGUMENT") or "").upper() == "TRUE"
    return _call("DEFINE_PROGRAM_ARGUMENT", [
        ("program_name", "in_program_name"),
        position,
        name,
        ("argument_type", _text(row_value(row, "ARGUMENT_TYPE"))),
        ("default_value", _text(row_value(row, "DEFAULT_VALUE"))),
        ("out_argument", "TRUE" if out else ""),
    ])


def _call(procedure: str, parameters: list[tuple[str, str]]) -> str:
    """A named-notation call, leaving out every parameter that has no value.

    Leaving one out is how DBMS_SCHEDULER is asked for its default, and for
    `default_value` it is also what picks the overload without one.
    """
    lines = ",\n".join(
        f"        {name:<20}=> {value}" for name, value in parameters if value
    )
    return f"    DBMS_SCHEDULER.{procedure} (\n{lines}\n    );\n"


def _text(value: Any) -> str:
    """A SQL text literal, or "" for a NULL so `_call` leaves the parameter out."""
    if value is None:
        return ""
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"
