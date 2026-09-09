"""Assemble the DDL of the 26ai types `DBMS_METADATA` refuses to write (`#738`).

Three of the four types this covers cannot be read as one row: an MLE environment
is its own row plus its imports, a domain is its row plus its columns, constraints
and annotations, and a property graph is five views deep. So the statement is
assembled here from the reads in `queries/types_26ai.py`, rather than concatenated
inside a single SELECT: a `LISTAGG` over a graph's property lists overflows
`ORA-01489` on a real schema, and the `XMLAGG` workaround entity-escapes the very
`>` and `<` a computed property expression is made of.

The output is deliberately shaped the way `GET_DDL` shapes its own: owner-qualified,
identifiers double-quoted and uppercase. That is what lets the common normalizer
pass lowercase them and strip the owner for a schema-neutral file, so none of these
four types needs a spelling rule that the other twenty do not have.

`MLE MODULE` is absent here because it needs none of this: its whole statement is
one row of `user_mle_modules`, so `MLE_MODULE_DDL_QUERY` renders it in SQL like
every other single-row type.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Protocol

from adt_ai.export_db.queries import types_26ai as q

#: Types assembled here rather than fetched as one row.
ASSEMBLED_OBJECT_TYPES = frozenset({"DOMAIN", "MLE ENVIRONMENT", "PROPERTY GRAPH"})

#: Character types whose declared length is written into the type name. A domain
#: column reports both the byte length and the declared one, and they differ only
#: under CHAR semantics -- which is what makes the comparison the unit test.
_SIZED_CHARACTER_TYPES = frozenset({"CHAR", "NCHAR", "NVARCHAR2", "RAW", "VARCHAR2"})


class _Gateway(Protocol):
    def fetch_all(self, sql: str, binds: dict[str, Any] | None = None) -> list[dict[str, Any]]: ...


def assemble_ddl(object_type: str, object_name: str, gateway: _Gateway) -> str:
    """The complete `CREATE` statement for one assembled type, or "" when it is gone.

    An empty string is the same answer `ObjectDiscovery.ddl` gives for any other
    type whose DDL query returned no row: the object was dropped between discovery
    and the read, and the caller already handles that.
    """
    builders = {
        "DOMAIN": _domain_ddl,
        "MLE ENVIRONMENT": _mle_env_ddl,
        "PROPERTY GRAPH": _property_graph_ddl,
    }
    builder = builders.get(object_type.upper())
    if builder is None:
        return ""
    return builder(object_name, gateway)


def _mle_env_ddl(object_name: str, gateway: _Gateway) -> str:
    """`CREATE OR REPLACE MLE ENV`, with the imports that give it its purpose.

    The keyword is `MLE ENV` while `user_objects` types the row `MLE ENVIRONMENT`;
    this renders the keyword, and the vocabulary carries both spellings so `-type`
    accepts either.
    """
    rows = gateway.fetch_all(q.MLE_ENV_QUERY, {"object_name": object_name})
    if not rows:
        return ""
    imports = gateway.fetch_all(q.MLE_ENV_IMPORTS_QUERY, {"object_name": object_name})
    lines = [f"CREATE OR REPLACE MLE ENV {_owned(object_name)}"]
    if imports:
        rendered = ", ".join(
            f"'{_value(row, 'import_name')}' MODULE {_quoted(_value(row, 'module_name'))}"
            for row in imports
        )
        lines.append(f"    IMPORTS ({rendered})")
    options = _value(rows[0], "language_options")
    if options:
        lines.append(f"    LANGUAGE OPTIONS '{options}'")
    return "\n".join(lines)


def _domain_ddl(object_name: str, gateway: _Gateway) -> str:
    """`CREATE DOMAIN`, which has no `OR REPLACE` form on 26ai.

    Measured on the live fixture: `CREATE OR REPLACE DOMAIN` answers `ORA-00922:
    missing or invalid option`, while `CREATE DOMAIN IF NOT EXISTS` is accepted. So
    the normalizer wraps this in the drop-before-create guard a materialized view
    and an assertion already take, rather than relying on a replace that does not
    exist.
    """
    rows = gateway.fetch_all(q.DOMAIN_QUERY, {"object_name": object_name})
    if not rows:
        return ""
    columns = gateway.fetch_all(q.DOMAIN_COLUMNS_QUERY, {"object_name": object_name})
    lines = [f"CREATE DOMAIN {_owned(object_name)} AS {_domain_datatype(columns)}"]
    default = _value(columns[0], "data_default") if columns else None
    if len(columns) == 1 and default:
        lines.append(f"    DEFAULT {str(default).strip()}")
    for row in gateway.fetch_all(q.DOMAIN_CONSTRAINTS_QUERY, {"object_name": object_name}):
        condition = _value(row, "search_condition")
        if not condition:
            continue
        lines.append(f"    CONSTRAINT {_quoted(_value(row, 'name'))} CHECK ({condition})")
    for clause, column in (("DISPLAY", "data_display"), ("ORDER", "data_order")):
        expression = _value(rows[0], column)
        if expression:
            lines.append(f"    {clause} {str(expression).strip()}")
    annotations = gateway.fetch_all(q.DOMAIN_ANNOTATIONS_QUERY, {"object_name": object_name})
    if annotations:
        lines.append(f"    ANNOTATIONS ({_annotations(annotations)})")
    return "\n".join(lines)


def _domain_datatype(columns: Sequence[dict[str, Any]]) -> str:
    """One column's rendered type, or the parenthesised list a composite domain takes."""
    if not columns:
        return "VARCHAR2(4000)"
    if len(columns) == 1:
        return _column_datatype(columns[0])
    rendered = ", ".join(
        f"{_quoted(_value(column, 'column_name'))} {_column_datatype(column)}"
        for column in columns
    )
    return f"({rendered})"


def _column_datatype(column: dict[str, Any]) -> str:
    """Spell one domain column's type the way its declaration spelled it.

    Only the two families whose declaration carries a size are reconstructed. Every
    other type -- `DATE`, `CLOB`, `JSON`, `VECTOR`, a timestamp -- is written bare,
    because its dictionary row carries no length that belongs in the statement and
    inventing one would export a narrower column than the schema has.
    """
    data_type = str(_value(column, "data_type") or "").upper()
    if data_type in _SIZED_CHARACTER_TYPES:
        declared = _value(column, "char_col_decl_length") or _value(column, "data_length")
        if declared is None:
            return data_type
        # The byte length and the declared length agree under BYTE semantics and
        # part ways under CHAR, which is the only signal the domain views carry.
        unit = " CHAR" if _value(column, "data_length") != declared else ""
        return f"{data_type}({int(declared)}{unit})"
    if data_type in {"NUMBER", "FLOAT"}:
        precision = _value(column, "data_precision")
        scale = _value(column, "data_scale")
        if precision is None:
            return data_type
        if scale in (None, 0):
            return f"{data_type}({int(precision)})"
        return f"{data_type}({int(precision)},{int(scale)})"
    return data_type


def _annotations(rows: Iterable[dict[str, Any]]) -> str:
    rendered = []
    for row in rows:
        name = _quoted(_value(row, "annotation_name"))
        value = _value(row, "annotation_value")
        rendered.append(f"{name} '{value}'" if value else name)
    return ", ".join(rendered)


def _property_graph_ddl(object_name: str, gateway: _Gateway) -> str:
    """`CREATE OR REPLACE PROPERTY GRAPH`, vertex tables then edge tables.

    `CREATE OR REPLACE` is accepted for this type on 26ai (measured), so the graph
    needs no drop guard. An edge table's `SOURCE`/`DESTINATION` clauses come from
    `user_pg_edge_relationships`, which is also what tells a vertex element from an
    edge one in the file even when the dictionary listed them together.
    """
    binds = {"object_name": object_name}
    elements = gateway.fetch_all(q.PROPERTY_GRAPH_ELEMENTS_QUERY, binds)
    if not elements:
        return ""
    keys = _grouped(gateway.fetch_all(q.PROPERTY_GRAPH_KEYS_QUERY, binds), "element_name")
    labels = _grouped(gateway.fetch_all(q.PROPERTY_GRAPH_LABELS_QUERY, binds), "element_name")
    properties = _grouped(
        gateway.fetch_all(q.PROPERTY_GRAPH_PROPERTIES_QUERY, binds), "element_name"
    )
    edges = _grouped(gateway.fetch_all(q.PROPERTY_GRAPH_EDGES_QUERY, binds), "edge_tab_name")
    vertices: list[list[str]] = []
    edge_elements: list[list[str]] = []
    for element in elements:
        name = _value(element, "element_name")
        rendered = _graph_element(
            element,
            keys.get(name, []),
            labels.get(name, []),
            properties.get(name, []),
            edges.get(_value(element, "object_name"), []),
        )
        target = (
            edge_elements
            if str(_value(element, "element_kind") or "").upper() == "EDGE"
            else vertices
        )
        target.append(rendered)
    lines = [f"CREATE OR REPLACE PROPERTY GRAPH {_owned(object_name)}"]
    for clause, group in (("VERTEX TABLES", vertices), ("EDGE TABLES", edge_elements)):
        if not group:
            continue
        lines.append(f"    {clause} (")
        for index, element_lines in enumerate(group):
            lines.extend(element_lines)
            lines[-1] += "" if index == len(group) - 1 else ","
        lines.append("    )")
    return "\n".join(lines)


def _graph_element(
    element: dict[str, Any],
    keys: Sequence[dict[str, Any]],
    labels: Sequence[dict[str, Any]],
    properties: Sequence[dict[str, Any]],
    edges: Sequence[dict[str, Any]],
) -> list[str]:
    lines = [f"        {_quoted(_value(element, 'object_name'))}"]
    if keys:
        lines[-1] += f" KEY ({_column_list(keys, 'column_name')})"
    for edge_end, clause in (("SOURCE", "SOURCE"), ("DESTINATION", "DESTINATION")):
        ends = [row for row in edges if str(_value(row, "edge_end") or "").upper() == edge_end]
        if not ends:
            continue
        vertex = _quoted(_value(ends[0], "vertex_tab_name"))
        lines.append(
            f"            {clause} KEY ({_column_list(ends, 'edge_col_name')})"
            f" REFERENCES {vertex} ({_column_list(ends, 'vertex_col_name')})"
        )
    for label in labels:
        rendered = f"            LABEL {_quoted(_value(label, 'label_name'))}"
        if properties:
            rendered += f" PROPERTIES ({_properties(properties)})"
        lines.append(rendered)
    return lines


def _properties(rows: Sequence[dict[str, Any]]) -> str:
    rendered = []
    for row in rows:
        name = _value(row, "property_name")
        expression = _value(row, "column_expr")
        column = _value(row, "column_name")
        if expression:
            rendered.append(f"{expression} AS {_quoted(name)}")
        elif column and str(column).upper() != str(name).upper():
            rendered.append(f"{_quoted(column)} AS {_quoted(name)}")
        else:
            rendered.append(_quoted(name))
    return ", ".join(rendered)


def _column_list(rows: Iterable[dict[str, Any]], column: str) -> str:
    seen: list[str] = []
    for row in rows:
        value = _quoted(_value(row, column))
        if value and value not in seen:
            seen.append(value)
    return ", ".join(seen)


def _grouped(rows: Iterable[dict[str, Any]], column: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(_value(row, column) or ""), []).append(row)
    return grouped


def _value(row: dict[str, Any], column: str) -> Any:
    """One column of one row, whichever case the driver handed the keys back in."""
    if column.upper() in row:
        return row[column.upper()]
    return row.get(column.lower())


def _quoted(name: Any) -> str:
    return f'"{name}"' if name else ""


def _owned(object_name: str) -> str:
    """The object spelled the way `GET_DDL` spells it, so the common pass can strip it.

    The owner placeholder is the connected schema, which is the only schema any of
    these reads can see: every source view is a `user_` one.
    """
    return f'"{_OWNER_PLACEHOLDER}"."{object_name}"'


#: Substituted by `ObjectDiscovery` with the schema actually being exported. Written
#: as a placeholder rather than read from the dictionary because the assembled
#: statement is built before the connection's `USER` is worth a round trip, and the
#: schema is already on the `DatabaseObject`.
_OWNER_PLACEHOLDER = "__ADT_OWNER__"


def with_owner(ddl: str, schema: str) -> str:
    return ddl.replace(_OWNER_PLACEHOLDER, schema.upper())
