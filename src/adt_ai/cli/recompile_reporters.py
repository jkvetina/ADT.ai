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
import textwrap
from collections.abc import Sequence

from adt_ai.cli.constants import (
    ObjectOverview,
    print_adt_table,
)
from adt_ai.recompile.inventory import CompileError, ObjectError
from adt_ai.recompile.root_causes import RootCause, RootCauseReport, is_cascade_message
from adt_ai.shared.progress import print_adt_header

_COMPILE_ERROR_CODE_RE = re.compile(r"\b(?:ORA|PLS)-\d+\b")
_MAX_COMPILE_ERROR_LINE_WIDTH = 80

_ERROR_STANZA_INDENT = "  "
_ERROR_LINE_INDENT = "    "

_ROOT_CAUSE_COLUMNS = ["OBJECT_TYPE", "OBJECT_NAME", "CAUSE", "BLAST"]
# Each verdict's stanza heading, which doubles as its glossary: the reader learns
# what MISSING means from the line the missing objects are listed under. Ordered
# most-actionable first. A cause whose culprit is never named (SOURCE, UNKNOWN)
# carries no list, the heading alone is the whole answer.
_CAUSE_SECTIONS = (
    ("MISSING", "MISSING - not there; restore it, then recompile:", True),
    ("GRANT",   "GRANT - it exists and this schema has no privilege on it:", True),
    ("SOURCE",  "SOURCE - its own source does not parse; nothing upstream to fix.", False),
    ("UNKNOWN", "UNKNOWN - invalid with no compile error to explain it.", False),
)


def _object_label(object_type: str, object_name: str) -> str:
    """``TYPE.NAME``, the one identifier every section of the report shares.

    The name alone is ambiguous and measurably so: a client DBADMIN schema carries
    ``UT_UTILS`` as both a ``PACKAGE`` and a ``PACKAGE BODY``, two objects with
    different errors. The type is what tells them apart (#212).
    """
    return f"{object_type}.{object_name}"


def _report_order(invalid: Sequence[ObjectError]) -> dict[tuple[str, str], int]:
    """Each invalid object's position in the INVALID OBJECTS listing.

    A sort key only. Until `#212` this was also printed as an ``ID`` column that
    every other section pointed back at; the per-object error list keys off the
    object itself, so the cross-reference (and the column) are gone. The
    ordering it encoded is not: the stanzas below still read in step with the
    listing above them.
    """
    return {
        (obj.object_type, obj.object_name): index
        for index, obj in enumerate(invalid)
    }


def print_root_causes(report: RootCauseReport, invalid: Sequence[ObjectError]) -> None:
    """Rank the still-invalid objects so the reader knows where to start (#205).

    Prints **below** INVALID OBJECTS and its compile-error list, the verdict
    reads under the evidence it is drawn from (#209; `#205` had this the other
    way round). Only roots are printed, most downstream damage first.

    The knock-ons they explain are classified but no longer listed: `#205` gave
    them a ``DERIVED - DO NOT START HERE`` section on the reasoning that an
    object vanishing from a report reads as fixed, and `#213` removed it,
    ``INVALID OBJECTS`` above already names every one of them, so the section
    restated a list the reader had just read. The classification stays load-
    bearing regardless: it is what keeps a knock-on out of the ranking, and what
    ``BLAST`` counts.
    """
    if not report or not report.roots:
        return
    order = _report_order(invalid)
    print_adt_header("ROOT CAUSES:")
    print_adt_table(
        [
            {
                "OBJECT_TYPE": cause.object_type,
                "OBJECT_NAME": cause.object_name,
                "CAUSE":       cause.cause,
                "BLAST":       cause.blast or "",
            }
            for cause in report.roots
        ],
        columns=_ROOT_CAUSE_COLUMNS,
    )
    _print_cause_sections(report.roots, order)


def _print_cause_sections(
    roots: Sequence[RootCause],
    order: dict[tuple[str, str], int],
) -> None:
    """What to fix, grouped by verdict, as stanzas rather than a table column.

    The culprit was a sixth column until the first live run: with a 28-character
    object name in the table, 80 columns leave it nine, and ``Z_SOAP...`` answers
    nothing. It is also the one genuinely unbounded field here, a qualified
    package member such as ``UT_COVERAGE_HELPER.T_UNIT_LINE_CALLS`` is 36
    characters, so the console contract's per-record stanza is its shape, not a
    truncated cell. Grouping by verdict makes the headings the glossary too, so
    the separate legend the column needed disappears with it.
    """
    printed = False
    for cause, heading, lists_culprits in _CAUSE_SECTIONS:
        matching = [root for root in roots if root.cause == cause]
        if not matching:
            continue
        # Exactly one blank above every heading. The ranked table already closes
        # with one, so the first heading takes that and only the rest emit their
        # own, an unconditional print() here gave the first heading two (#212).
        if printed:
            print()
        printed = True
        print(f"  {heading}")
        if not lists_culprits:
            continue
        for root in sorted(matching, key=lambda r: order.get(
                (r.object_type, r.object_name), 0)):
            # A culprit no evidence names is left blank rather than guessed at.
            culprit = root.culprit or "(not named by any error)"
            print(f"    {_object_label(root.object_type, root.object_name)} -> {culprit}")
    if printed:
        print()


def _print_invalid_object_errors(
    invalid: Sequence[ObjectError],
    error_details: Sequence[CompileError],
) -> None:
    details_by_object: dict[tuple[str, str], list[CompileError]] = {
        (obj.object_type, obj.object_name): [] for obj in invalid
    }
    for detail in error_details:
        key = (detail.object_type, detail.object_name)
        if key in details_by_object:
            details_by_object[key].append(detail)

    print_adt_table(
        [
            {
                "OBJECT_TYPE": obj.object_type,
                "OBJECT_NAME": obj.object_name,
                "ERROR":       _last_compile_error_code(details_by_object[
                    (obj.object_type, obj.object_name)
                ], obj.error),
                "ERRORS":      obj.errors,
            }
            for obj in invalid
        ],
        columns=["OBJECT_TYPE", "OBJECT_NAME", "ERROR", "ERRORS"],
    )
    _print_compile_error_list(invalid, details_by_object)


def _print_compile_error_list(
    invalid: Sequence[ObjectError],
    details_by_object: dict[tuple[str, str], list[CompileError]],
) -> None:
    """One stanza per object, one line per distinct error (#212).

    Replaces the four-column ``ID | LINE | POS | ERROR MESSAGE`` table, which had
    no section header, spent its widest column on an ``ID`` that existed only to
    point back at the listing above, and then truncated the message, the only
    field that is the answer, to whatever was left. Keying the stanza on the
    object removes the cross-reference, and the reclaimed width goes to the text.
    """
    stanzas = [
        (obj, _distinct_compile_errors(details_by_object[(obj.object_type, obj.object_name)]))
        for obj in invalid
    ]
    stanzas = [(obj, errors) for obj, errors in stanzas if errors]
    if not stanzas:
        return
    print_adt_header("COMPILE ERRORS:")
    for obj, errors in stanzas:
        print()
        print(f"{_ERROR_STANZA_INDENT}{_object_label(obj.object_type, obj.object_name)}")
        for (line, position), message in errors:
            for rendered in _compile_error_lines(line, position, message):
                print(rendered)
    print()


def _distinct_compile_errors(
    details: Sequence[CompileError],
) -> list[tuple[tuple[int, int], str]]:
    """This object's real errors, each once, at the lowest place it was reported.

    Two filters, both measured against the live a client DBADMIN schema run: cascade rows
    go (they restate a failure whose real cause is printed beside them), and a
    message repeated at several positions collapses to its lowest ``line.pos``,
    a missing grant referenced eight times is one thing to fix, not eight.

    Cascade rows are identified by :func:`is_cascade_message`, the same predicate
    the ranking discounts them with. This module kept its own literal set until
    `#213`: `#212` added ``Compilation unit analysis terminated`` to the ranker's
    and left this one alone, so the message stopped counting toward a verdict and
    kept printing. A row not worth counting is not worth printing.

    Deduping compares the **full** message. The replaced table compared what it
    rendered, where ``...object SSO...`` and ``...object SER...`` had already been
    truncated to the same 80-column prefix and would have collapsed into one.
    """
    lowest: dict[str, tuple[int, int]] = {}
    for detail in details:
        message = _clean_compile_error_message(detail.text)
        if not message or is_cascade_message(message):
            continue
        position = (detail.line, detail.position if detail.position is not None else -1)
        if message not in lowest or position < lowest[message]:
            lowest[message] = position
    return sorted(
        ((position, message) for message, position in lowest.items()),
        key=lambda item: (item[0], item[1]),
    )


def _compile_error_lines(line: int, position: int, message: str) -> list[str]:
    """``    - <line>.<pos> <message>``, wrapped under a hanging indent.

    Wrapped rather than truncated: outside a table column there is no cell to fit,
    and the message is the answer, ``PLS-00103: Encountered the symbol "-" when
    expecting o...`` names no symbol the reader can act on. This is the console
    contract's per-record stanza, the same shape `validate` was rebuilt into after
    it shipped compiler prose in a table column (`#163` → `#164`).
    """
    locator = f"- {line}.{max(position, 0)} "
    return textwrap.wrap(
        message,
        width             = _MAX_COMPILE_ERROR_LINE_WIDTH,
        initial_indent    = f"{_ERROR_LINE_INDENT}{locator}",
        subsequent_indent = f"{_ERROR_LINE_INDENT}{' ' * len(locator)}",
        break_long_words  = False,
        break_on_hyphens  = False,
    )


def _last_compile_error_code(details: Sequence[CompileError], fallback: str | None) -> str:
    for detail in sorted(details, key=_compile_error_sort_key, reverse=True):
        code = detail.error or _extract_compile_error_code(detail.text)
        if code:
            return code
    return fallback or ""


def _clean_compile_error_message(text: str) -> str:
    """One line's worth of message, whatever Oracle stored (#209).

    `user_errors.text` keeps whatever whitespace the compiler wrote, and several
    rows end with a newline, `ORA-00904: "WM_CONCAT": invalid identifier` on
    a client DBADMIN schema among them. Rendered raw in the table this list replaced, that
    newline ended the row early and the column's remaining width printed as a line
    of spaces underneath. Normalizing still matters for the list: it is what makes
    the dedupe key stable, and what lets `textwrap` own every line break.
    """
    return " ".join((text or "").split())


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
