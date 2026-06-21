from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _constraint_column_names,
    _matching_parenthesis_index,
    _normalize_sql_identifier,
    _replace_outside_sql_strings,
)
from adt_ai.export_db.object_normalizers.table_folds import _FoldedConstraint


def _is_constraint_item(item_type: str) -> bool:
    return bool(
        item_type.startswith("CONSTRAINT ")
        or re.match(
            r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK)\b",
            item_type,
            flags=re.IGNORECASE,
        )
    )

def _constraint_item_rank(item_type: str) -> int:
    body = re.sub(
        r"^CONSTRAINT\s+\S+\s+", "", item_type.strip(), flags=re.IGNORECASE
    ).upper()
    if body.startswith("PRIMARY KEY"):
        return 0
    if body.startswith("UNIQUE"):
        return 1
    if body.startswith("FOREIGN KEY"):
        return 2
    return 3

def _constraint_item_name(item_type: str) -> str:
    match = re.match(
        r'^CONSTRAINT\s+("(?P<quoted>[^"]+)"|(?P<bare>[A-Za-z0-9_$#]+))',
        item_type.strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return (match.group("quoted") or match.group("bare")).casefold()

def _formatted_table_items_reordered(
    items: list[str],
    folds: list[_FoldedConstraint],
    context: NormalizationContext,
) -> list[list[str]]:
    columns: list[list[str]] = []
    constraints: list[tuple[int, str, int, list[str]]] = []
    for order_index, item in enumerate(items):
        item_type = re.sub(r"\s+", " ", item.strip())
        formatted = _format_table_item(item, context)
        if formatted is None:
            continue
        if _is_constraint_item(item_type):
            constraints.append(
                (
                    _constraint_item_rank(item_type),
                    _constraint_item_name(item_type),
                    order_index,
                    formatted,
                )
            )
        else:
            columns.append(formatted)

    base = len(items)
    for offset, fold in enumerate(folds):
        constraints.append(
            (
                _constraint_item_rank(fold.source_item),
                _constraint_item_name(fold.source_item),
                base + offset,
                _format_table_constraint(fold.source_item, context),
            )
        )

    constraints.sort(key=lambda entry: (entry[0], entry[1], entry[2]))
    return columns + [entry[3] for entry in constraints]

def _format_table_item(item: str, context: NormalizationContext) -> list[str] | None:
    if not item.strip():
        return None
    item_type = re.sub(r"\s+", " ", item.strip())
    if item_type.startswith("CONSTRAINT ") or re.match(
        r"^(PRIMARY KEY|FOREIGN KEY|UNIQUE|CHECK)\b",
        item_type,
        flags=re.IGNORECASE,
    ):
        return _format_table_constraint(item_type, context)

    item = _cleanup_table_item(item)
    if not item:
        return None
    return _format_table_column(item)

def _cleanup_table_item(item: str) -> str:
    item = re.sub(r"\s+", " ", item.replace("\n", " ")).strip()
    item = re.sub(
        r"\s+COLLATE\s+\"?USING_NLS_COMP\"?",
        "",
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(r"\s+ENABLE\b", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+USING\s+INDEX\b.*", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+TABLESPACE\s+\S+", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+MAXVALUE\s+9{10,}", "", item, flags=re.IGNORECASE)
    for token in (
        " MINVALUE 1",
        " INCREMENT BY 1",
        " CACHE 20",
        " NOORDER",
        " NOCYCLE",
        " NOKEEP",
        " NOSCALE",
    ):
        item = item.replace(token, "")
    item = item.replace("NUMBER(*,0)", "INTEGER")
    item = re.sub(r"\bNUMBER\(\*,0\)", "INTEGER", item, flags=re.IGNORECASE)
    item = re.sub(r"TIMESTAMP\s+\((\d+)\)", r"TIMESTAMP(\1)", item)
    item = re.sub(
        r"INTERVAL\s+DAY\s+\((\d+)\)\s+TO\s+SECOND\s+\((\d+)\)",
        r"INTERVAL DAY(\1) TO SECOND(\2)",
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(
        r"INTERVAL\s+YEAR\s+\((\d+)\)\s+TO\s+MONTH",
        r"INTERVAL YEAR(\1) TO MONTH",
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(
        r'(?<![A-Za-z0-9_$#])(?:"?SYS"?\.)?"?XMLTYPE"?(?![A-Za-z0-9_$#])',
        "XMLTYPE",
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(r"\s+START WITH 1\b", "", item)
    item = _strip_sequence_nextval(item)
    return re.sub(r"\s+", " ", item).strip()

_SEQUENCE_IDENT = r'(?:"[A-Za-z0-9_$#]+"|[A-Za-z0-9_$#]+)'

def _strip_sequence_nextval(item: str) -> str:
    """Normalize sequence defaults to bare ``sequence.nextval`` like old ADT.

    DBMS_METADATA emits column defaults as a fully qualified, double-quoted
    reference (``"SCHEMA"."SEQ"."NEXTVAL"``). Old ADT dropped the schema and the
    quotes and lowercased the identifier so the default reads ``seq.nextval``.
    Handle the 3-part (schema-qualified) and 2-part forms.
    """

    def _repl(match: re.Match[str]) -> str:
        return f"{match.group(1).strip(chr(34)).lower()}.nextval"

    item = re.sub(
        rf"{_SEQUENCE_IDENT}\.({_SEQUENCE_IDENT})\.(?:\"NEXTVAL\"|NEXTVAL\b)",
        _repl,
        item,
        flags=re.IGNORECASE,
    )
    item = re.sub(
        rf"({_SEQUENCE_IDENT})\.(?:\"NEXTVAL\"|NEXTVAL\b)",
        _repl,
        item,
        flags=re.IGNORECASE,
    )
    return item

def _format_table_column(item: str) -> list[str]:
    match = re.match(r"(?P<name>\S+)\s+(?P<body>.*)", item, flags=re.IGNORECASE)
    if not match:
        return [f"    {item}"]

    name = _normalize_sql_identifier(match.group("name"))
    data_type, extras = _split_column_data_type_and_extras(match.group("body"))
    data_type = data_type.upper()
    interval_qualifier = _interval_qualifier(data_type)
    if interval_qualifier is not None:
        data_type = "INTERVAL"
        extras = f"{interval_qualifier} {extras}".strip()
    if extras:
        return [f"    {name:<30}  {data_type:<21} {extras},".rstrip().rstrip(",")]
    return [f"    {name:<30}  {data_type}".rstrip()]

def _split_column_data_type_and_extras(body: str) -> tuple[str, str]:
    match = re.search(
        r"\s+(?=(?:DEFAULT|GENERATED|CONSTRAINT|NOT\s+NULL|NULL\b|PRIMARY\s+KEY|UNIQUE\b|REFERENCES\b|CHECK\b))",
        body,
        flags=re.IGNORECASE,
    )
    if not match:
        return body.strip(), ""
    return body[: match.start()].strip(), body[match.end():].strip()

def _interval_qualifier(data_type: str) -> str | None:
    match = re.fullmatch(r"INTERVAL (DAY\(\d+\) TO SECOND\(\d+\)|YEAR\(\d+\) TO MONTH)", data_type)
    if match:
        return match.group(1)
    return None

def _format_table_constraint(item: str, context: NormalizationContext) -> list[str]:
    item = _cleanup_constraint_item(item)
    named = re.match(r"CONSTRAINT\s+(?P<name>\S+)\s+(?P<body>.*)", item, flags=re.IGNORECASE)
    if named:
        name = _normalize_sql_identifier(named.group("name"), context)
        return _format_constraint_body(named.group("body"), name=name, context=context)
    return _format_constraint_body(item, name=None, context=context)

def _cleanup_constraint_item(item: str) -> str:
    item = re.sub(r"\s+", " ", item.replace("\n", " ")).strip()
    item = re.sub(r"\s+ENABLE\b", "", item, flags=re.IGNORECASE)
    item = re.sub(r"\s+USING\s+INDEX\b.*", "", item, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", item).strip()

def _format_constraint_body(
    body: str,
    name: str | None,
    context: NormalizationContext,
) -> list[str]:
    prefix = ["    --"]
    if name:
        prefix.append(f"    CONSTRAINT {name}")

    check = _extract_parenthesized_clause(body, "CHECK")
    if check is not None:
        expression, suffix = check
        lines = prefix + _format_check_constraint(expression, named=bool(name))
        lines.extend(_format_constraint_suffix(suffix, context))
        return lines

    for constraint_type in ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE"):
        parsed = _extract_parenthesized_clause(body, constraint_type)
        if parsed is None:
            continue
        columns, suffix = parsed
        lines = prefix
        lines.extend(_format_constraint_columns(constraint_type, columns, named=bool(name)))
        lines.extend(_format_constraint_suffix(suffix, context))
        return lines

    return prefix + [f"    {body}"]

def _extract_parenthesized_clause(body: str, keyword: str) -> tuple[str, str] | None:
    keyword_pattern = r"\s+".join(keyword.split())
    match = re.match(rf"{keyword_pattern}\s*\(", body, flags=re.IGNORECASE)
    if not match:
        return None
    open_index = body.find("(", match.start())
    close_index = _matching_parenthesis_index(body, open_index)
    if close_index is None:
        return None
    return body[open_index + 1 : close_index], body[close_index + 1 :].strip()

def _format_constraint_suffix(suffix: str, context: NormalizationContext) -> list[str]:
    if not suffix:
        return []
    reference = _format_references_clause(suffix, context)
    if reference is not None:
        return reference
    return [f"        {suffix}"]

def _format_references_clause(
    suffix: str,
    context: NormalizationContext,
) -> list[str] | None:
    match = re.match(
        r"REFERENCES\s+(?P<table>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)|\"[^\"]+\"|[A-Za-z0-9_$#]+)\s*\(",
        suffix,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    open_index = suffix.find("(", match.end("table"))
    close_index = _matching_parenthesis_index(suffix, open_index)
    if close_index is None:
        return None

    table_name = _normalize_sql_identifier(match.group("table"), context)
    columns = _constraint_column_names(suffix[open_index + 1 : close_index])
    tail = suffix[close_index + 1 :].strip()
    if len(columns) == 1:
        return [f"        REFERENCES {table_name} ({columns[0]}){f' {tail}' if tail else ''}"]
    return [
        f"        REFERENCES {table_name} (",
        *[
            f"            {column}{',' if index < len(columns) - 1 else ''}"
            for index, column in enumerate(columns)
        ],
        f"        ){f' {tail}' if tail else ''}",
    ]

def _format_check_constraint(expression: str, named: bool) -> list[str]:
    expression_line = _normalize_constraint_expression(expression)
    return [
        "        CHECK (" if named else "    CHECK (",
        f"            {expression_line}" if named else f"        {expression_line}",
        "        )" if named else "    )",
    ]

def _normalize_constraint_expression(expression: str) -> str:
    return _replace_outside_sql_strings(
        expression.strip(),
        lambda chunk: re.sub(r'"([A-Z][A-Z0-9_$#]*)"', r"\1", chunk),
    )

def _format_constraint_columns(
    constraint_type: str,
    columns: str,
    named: bool,
) -> list[str]:
    column_names = _constraint_column_names(columns)
    indent = "        " if named else "    "
    if len(column_names) == 1:
        return [f"{indent}{constraint_type} ({column_names[0]})"]
    return [
        f"{indent}{constraint_type} (",
        *[
            f"{indent}    {column}{',' if index < len(column_names) - 1 else ''}"
            for index, column in enumerate(column_names)
        ],
        f"{indent})",
    ]
