"""Console rendering for the recompile command's own report sections.

Sibling of export_reporters / dependencies_reporters: the command handler in
commands_recompile.py decides what to run, this module decides how the results
read. recompile/render.py keeps the sections shared with the streaming reporters
(mviews, synonyms, disabled, jobs, trailing); the compile-error and objects-overview
tables below are the CLI's alone and moved here when commands_recompile.py hit the
20 KB context cap (ADT #131).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from adt_ai.cli.constants import (
    ObjectOverview,
    print_adt_table,
)
from adt_ai.recompile.inventory import CompileError, ObjectError

_COMPILE_ERROR_CODE_RE = re.compile(r"\b(?:ORA|PLS)-\d+\b")
_MAX_COMPILE_ERROR_LINE_WIDTH = 80


def _print_invalid_object_errors(
    invalid: Sequence[ObjectError],
    error_details: Sequence[CompileError],
) -> None:
    object_ids = {
        (obj.object_type, obj.object_name): index
        for index, obj in enumerate(invalid, start=1)
    }
    details_by_object: dict[tuple[str, str], list[CompileError]] = {
        key: [] for key in object_ids
    }
    for detail in error_details:
        key = (detail.object_type, detail.object_name)
        if key in details_by_object:
            details_by_object[key].append(detail)

    print_adt_table(
        [
            {
                "ID":          object_ids[(obj.object_type, obj.object_name)],
                "OBJECT_TYPE": obj.object_type,
                "OBJECT_NAME": obj.object_name,
                "ERROR":       _last_compile_error_code(details_by_object[
                    (obj.object_type, obj.object_name)
                ], obj.error),
                "ERRORS":      obj.errors,
            }
            for obj in invalid
        ],
        columns=["ID", "OBJECT_TYPE", "OBJECT_NAME", "ERROR", "ERRORS"],
    )
    if error_details:
        print()
        print_adt_table(
            _compile_error_message_rows(error_details, object_ids),
            columns=["ID", "LINE", "POS", "ERROR_MESSAGE"],
            leading_blank=False,
        )


def _last_compile_error_code(details: Sequence[CompileError], fallback: str | None) -> str:
    for detail in sorted(details, key=_compile_error_sort_key, reverse=True):
        code = detail.error or _extract_compile_error_code(detail.text)
        if code:
            return code
    return fallback or ""


def _compile_error_message_rows(
    error_details: Sequence[CompileError],
    object_ids: dict[tuple[str, str], int],
) -> list[dict[str, object]]:
    rows = [
        {
            "ID":            object_ids[(detail.object_type, detail.object_name)],
            "LINE":          detail.line,
            "POS":           detail.position if detail.position is not None else "",
            "ERROR_MESSAGE": detail.text,
        }
        for detail in sorted(error_details, key=lambda item: (
            object_ids.get((item.object_type, item.object_name), 0),
            *_compile_error_sort_key(item),
        ))
        if (detail.object_type, detail.object_name) in object_ids
    ]
    max_message_width = _compile_error_message_width(rows)
    return [
        {
            **row,
            "ERROR_MESSAGE": _truncate_compile_error_message(
                str(row["ERROR_MESSAGE"]),
                max_message_width,
            ),
        }
        for row in rows
    ]


def _compile_error_message_width(rows: Sequence[dict[str, object]]) -> int:
    prefix_width = 2
    for column in ("ID", "LINE", "POS"):
        prefix_width += max(
            len(column),
            *(len(str(row.get(column, ""))) for row in rows),
        ) + 3
    return max(13, _MAX_COMPILE_ERROR_LINE_WIDTH - prefix_width - 3)


def _truncate_compile_error_message(message: str, width: int) -> str:
    if len(message) <= width:
        return message
    if width <= 3:
        return message[:width]
    return f"{message[:width - 3]}..."


def _compile_error_sort_key(detail: CompileError) -> tuple[int, int, int]:
    return (detail.line, detail.position if detail.position is not None else -1, detail.id)


def _extract_compile_error_code(text: str) -> str:
    match = _COMPILE_ERROR_CODE_RE.search(text)
    return match.group(0) if match else ""


def _print_recompile_overview_table(overviews: Sequence[ObjectOverview]) -> None:
    if not overviews:
        return

    object_width = max(
        12,
        *(len(overview.object_type) for overview in overviews),
    )
    # OBJECT TYPE, TOTAL, VALIDATED, INVALID, MISSING IDENTIFIERS, MISSING STATEMENTS.
    # VALIDATED sits immediately before INVALID so the two read as a pair: what the
    # run fixed, and what it could not (#186).
    widths = [object_width, 5, 9, 7, 11, 10]
    separators = ["   ", "   ", "   ", "   ", "    "]

    def display_cell(cell: object) -> object:
        return "" if isinstance(cell, int) and cell == 0 else cell

    def line(cells: Sequence[object], aligns: Sequence[str]) -> str:
        parts = [
            f"{str(display_cell(cell)):{align}{width}}"
            for cell, width, align in zip(cells, widths, aligns, strict=True)
        ]
        return "  " + "".join(
            part + (separators[index] if index < len(separators) else "")
            for index, part in enumerate(parts)
        )

    aligns = ["<", ">", ">", ">", ">", ">"]
    plscope_start = (
        2 + object_width + 3 + widths[1] + 3 + widths[2] + 3 + widths[3] + 3
    )
    print()
    print(
        (" " * plscope_start)
        + f"{'MISSING':>{widths[4]}}"
        + separators[4]
        + f"{'MISSING':>{widths[5]}}"
    )
    print(
        line(
            ["OBJECT TYPE", "TOTAL", "VALIDATED", "INVALID", "IDENTIFIERS", "STATEMENTS"],
            aligns,
        )
    )
    print(line([("-" * width) for width in widths], aligns))
    for overview in overviews:
        print(
            line(
                [
                    overview.object_type,
                    overview.total,
                    overview.validated,
                    overview.invalid,
                    overview.missing_plscope_identifiers,
                    overview.missing_plscope_statements,
                ],
                aligns,
            )
        )
    # Trailing blank so the next header gets two empty lines above it (this
    # blank + the header's own leading blank). print_adt_table sections already
    # close with a blank; this hand-rolled table must match that contract.
    print()
