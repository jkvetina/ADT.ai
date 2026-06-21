from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _extract_definition_owner,
    _matching_parenthesis_index,
    _normalize_definition_line_only,
    _split_top_level_commas,
)
from adt_ai.export_db.object_normalizers.table_folds import _collect_index_backed_constraints
from adt_ai.export_db.object_normalizers.table_items import (
    _constraint_item_rank,
    _formatted_table_items_reordered,
)
from adt_ai.export_db.object_normalizers.table_suffix import _format_table_suffix


def normalize_table(lines: list[str], context: NormalizationContext) -> list[str]:
    payload = "\n".join(_normalize_definition_line_only(lines, context))
    table_name = context.object_name.lower()
    is_global_temporary = bool(
        re.search(r"CREATE\s+GLOBAL\s+TEMPORARY\s+TABLE\b", payload, flags=re.IGNORECASE)
    )
    open_index = payload.find("(")
    if open_index < 0:
        return lines

    close_index = _matching_parenthesis_index(payload, open_index)
    if close_index is None:
        return lines

    body = payload[open_index + 1:close_index]
    suffix = payload[close_index + 1:]
    items = _split_top_level_commas(body)
    folds, suffix = _collect_index_backed_constraints(suffix, context)
    formatted_items = _formatted_table_items_reordered(items, folds, context)

    if is_global_temporary:
        create_header = "CREATE GLOBAL TEMPORARY TABLE"
    elif context.add_if_not_exists:
        create_header = "CREATE TABLE IF NOT EXISTS"
    else:
        create_header = "CREATE TABLE"
    result = [f"{create_header} {table_name} ("]
    for index, item_lines in enumerate(formatted_items):
        is_last = index == len(formatted_items) - 1
        lines_to_add = list(item_lines)
        if not is_last:
            lines_to_add[-1] += ","
        result.extend(lines_to_add)
    result.append(")")
    partition_lines = _format_table_suffix(suffix, context)
    if partition_lines and partition_lines[0] == ";":
        result[-1] += ";"
        result.extend(partition_lines[1:])
    elif partition_lines and partition_lines[0].startswith(") "):
        result[-1] = partition_lines[0]
        result.extend(partition_lines[1:])
    else:
        result.extend(partition_lines)
    return result


def build_table_fix_sql(payload: str, object_name: str) -> str | None:
    normalized_payload = payload.replace("\t", "    ").strip()
    context = NormalizationContext(
        object_type  = "TABLE",
        object_name  = object_name,
        object_owner = _extract_definition_owner(normalized_payload, "TABLE"),
    )
    open_index = normalized_payload.find("(")
    if open_index < 0:
        return None
    close_index = _matching_parenthesis_index(normalized_payload, open_index)
    if close_index is None:
        return None

    suffix = normalized_payload[close_index + 1:]
    folds, _ = _collect_index_backed_constraints(suffix, context)
    if not folds:
        return None

    ordered = sorted(
        folds,
        key=lambda fold: (
            _constraint_item_rank(fold.source_item),
            fold.constraint_name.casefold(),
        ),
    )
    table = object_name.lower()
    blocks = [
        "\n".join(
            [
                f"ALTER TABLE {table} DROP CONSTRAINT {fold.constraint_name};",
                "--",
                f"DROP INDEX {fold.index_name};",
                "--",
                f"ALTER TABLE {table}",
                f"    ADD CONSTRAINT {fold.constraint_name} "
                f"{fold.constraint_type} ({', '.join(fold.columns)});",
            ]
        )
        for fold in ordered
    ]
    return "\n--\n".join(blocks) + "\n"
