from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _extract_definition_owner,
    _matching_parenthesis_index,
    _normalize_definition_line_only,
    _split_top_level_commas,
    qualified,
)
from adt_ai.export_db.object_normalizers.table_folds import _collect_index_backed_constraints
from adt_ai.export_db.object_normalizers.table_items import (
    _constraint_item_rank,
    _formatted_table_items_reordered,
)
from adt_ai.export_db.object_normalizers.table_suffix import _format_table_suffix

#: The words between CREATE and TABLE that change what the table IS. Dropping one
#: does not produce a different-looking table, it produces a different table:
#: replaying a file that lost IMMUTABLE creates a mutable one. ADT #736.
_TABLE_KIND = re.compile(
    r"CREATE\s+(?P<kind>GLOBAL\s+TEMPORARY|IMMUTABLE|BLOCKCHAIN|JSON\s+COLLECTION)\s+TABLE\b",
    flags=re.IGNORECASE,
)


def _create_header(payload: str, context: NormalizationContext) -> str:
    match = _TABLE_KIND.search(payload)
    kind = re.sub(r"\s+", " ", match.group("kind")).upper() if match else ""
    if kind == "GLOBAL TEMPORARY":
        # A global temporary table has never carried IF NOT EXISTS here; leave it.
        return "CREATE GLOBAL TEMPORARY TABLE"
    header = f"CREATE {kind} TABLE" if kind else "CREATE TABLE"
    if context.add_if_not_exists:
        return f"{header} IF NOT EXISTS"
    return header


def _terminate_column_less_table(payload: str, context: NormalizationContext) -> list[str]:
    """A table whose DDL carries no column list, like a JSON collection table.

    Until ADT #737 the normalizer bailed here and the caller returned the RAW
    DDL lines: schema-qualified, quoted, and with no terminator, so the `--`
    written below it commented out nothing and the following `COMMENT ON TABLE`
    was parsed as part of the CREATE. The file could not run at all.

    The name is already normalized by the caller's definition-line pass, so this
    only has to add the two things that pass cannot: the IF NOT EXISTS the rest
    of the export uses, and the terminator.
    """
    text = payload.strip().rstrip(";")
    if context.add_if_not_exists and not re.search(
        r"\bIF\s+NOT\s+EXISTS\b", text, flags=re.IGNORECASE
    ):
        text = re.sub(r"\bTABLE\b", "TABLE IF NOT EXISTS", text, count=1, flags=re.IGNORECASE)
    return [f"{text};"]


def normalize_table(lines: list[str], context: NormalizationContext) -> list[str]:
    payload = "\n".join(_normalize_definition_line_only(lines, context))
    table_name = qualified(context.display_name, context)
    open_index = payload.find("(")
    if open_index < 0:
        return _terminate_column_less_table(payload, context)

    close_index = _matching_parenthesis_index(payload, open_index)
    if close_index is None:
        return lines

    body = payload[open_index + 1:close_index]
    suffix = payload[close_index + 1:]
    items = _split_top_level_commas(body)
    folds, suffix = _collect_index_backed_constraints(suffix, context)
    formatted_items = _formatted_table_items_reordered(items, folds, context)

    create_header = _create_header(payload, context)
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


def build_table_fix_sql(
    payload: str,
    object_name: str,
    object_display_name: str | None = None,
) -> str | None:
    normalized_payload = payload.replace("\t", "    ").strip()
    context = NormalizationContext(
        object_type         = "TABLE",
        object_name         = object_name,
        object_owner        = _extract_definition_owner(normalized_payload, "TABLE"),
        object_display_name = object_display_name,
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
    table = context.display_name
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
