"""Turn SQLcl's ``apex validate`` output into rows and a pass/fail verdict.

SQLcl exits ``0`` whatever the compiler says (measured on 26.1.2.132.1334, card
`#163`), so the exit code carries no signal and the printed text is the only
source of truth. That makes this parser the gate: every outcome it cannot
recognise is a failure, never a pass, because an unread failure that renders as
clean is the one bug a validation gate must not have.

The compiler prints two blocks with an identical record shape, ``File:``,
``Line:``, ``Column:``, ``Type:``, then either ``Error:`` or ``Warning:`` closing
the record. They are parsed per block rather than by scanning the whole text, so
a warning's fields can never leak into the first error record.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass

# Jan's terminal is 80 columns, the same ceiling recompile's compile-error
# rendering already assumes (`_MAX_COMPILE_ERROR_LINE_WIDTH`).
MESSAGE_WIDTH   = 80
_LOCATOR_INDENT = "  "
_DETAIL_INDENT  = "    "

SUCCESS      = "SUCCESS"
ERRORS       = "ERRORS"
EMPTY        = "EMPTY"
NOT_FOUND    = "NOT_FOUND"
UNRECOGNISED = "UNRECOGNISED"

# SQLcl wordings. Kept as substrings rather than full-line equalities, and
# matched case-insensitively, so a capitalisation or spacing tweak upstream does
# not silently drop an outcome into UNRECOGNISED. Both halves are load-bearing:
# 26.1.2 headed the block `APEXLang Compile Errors:` and 26.2.1.0 heads it
# `APEXlang Compile Errors:`, so a case-sensitive match reported every compile
# error on the shipped SQLcl as output the parser could not read.
_SUCCESS_MARKER   = "Validation successful"
_ERRORS_MARKER    = "APEXlang Compile Errors:"
_WARNINGS_MARKER  = "APEXlang Compile Warnings:"
_EMPTY_MARKER     = "does not contain APEXlang files"
_NOT_FOUND_MARKER = "Could not find file or directory with inputPath"

_FIELD_RE = re.compile(r"^(File|Line|Column|Type|Error|Warning):\s?(.*)$")


def _marker_span(text: str, marker: str) -> tuple[int, int] | None:
    """Where a marker sits in `text`, ignoring case, or None when it is absent.

    A regex search rather than a `casefold()` compare because `casefold` can
    change a string's length (`ß` folds to `ss`), and every index here is used to
    slice the ORIGINAL text: a compiler message carrying such a character would
    shift the cut and take a record with it.
    """
    match = re.search(re.escape(marker), text, re.IGNORECASE)
    return match.span() if match else None


def _has_marker(text: str, marker: str) -> bool:
    return _marker_span(text, marker) is not None


@dataclass(frozen=True)
class CompileMessage:
    file    : str
    line    : int | None
    column  : int | None
    type    : str
    message : str


# Kept as the historical name for an error row.
CompileError = CompileMessage


@dataclass(frozen=True)
class FolderReport:
    outcome  : str
    errors   : tuple[CompileMessage, ...]
    raw      : str
    warnings : tuple[CompileMessage, ...] = ()

    @property
    def failed(self) -> bool:
        # Gate semantics: exit 0 means every requested folder validated clean.
        # EMPTY and NOT_FOUND are failures too, a folder that was asked for and
        # had nothing to check is a broken export, not a quiet success. The
        # compiler agrees: NO_APEXLANG_FILES is one of its own error types.
        return self.outcome != SUCCESS

    @property
    def status(self) -> str:
        """One short word (or phrase) for the streamed progress row."""
        if self.outcome == SUCCESS:
            if self.warnings:
                # A bare "OK" that hid an ignored file would be a misleading
                # pass: FILE_IGNORED means the compiler never checked it.
                count = len(self.warnings)
                return f"OK ({count} warning{'s' if count != 1 else ''})"
            return "OK"
        if self.outcome == ERRORS:
            return str(len(self.errors))
        return self.outcome


def parse_validate_output(text: str) -> FolderReport:
    body = text or ""
    warnings = _parse_block(body, _WARNINGS_MARKER, "Warning")

    if _has_marker(body, _ERRORS_MARKER):
        errors = _parse_block(body, _ERRORS_MARKER, "Error")
        # "Errors happened but we could not read them" must not render as an
        # empty error table, which reads exactly like a pass.
        outcome = ERRORS if errors else UNRECOGNISED
        return FolderReport(outcome, errors, body, warnings)
    if _has_marker(body, _NOT_FOUND_MARKER):
        return FolderReport(NOT_FOUND, (), body, warnings)
    if _has_marker(body, _EMPTY_MARKER):
        return FolderReport(EMPTY, (), body, warnings)
    if _has_marker(body, _SUCCESS_MARKER):
        return FolderReport(SUCCESS, (), body, warnings)
    return FolderReport(UNRECOGNISED, (), body, warnings)


def _parse_block(text: str, marker: str, closing_field: str) -> tuple[CompileMessage, ...]:
    """Collect one block's records, stopping where the next block begins.

    ``closing_field`` (``Error:`` or ``Warning:``) is the last field the compiler
    prints per record, so it closes the record, a record with a missing
    ``Line``/``Column`` still lands rather than swallowing the next one. Values
    keep the compiler's own spacing: ``Component:   not found`` says the
    component name came back blank, and squashing it would hide that.
    """
    span = _marker_span(text, marker)
    if span is None:
        return ()
    region = text[span[1]:]
    for other in (_ERRORS_MARKER, _WARNINGS_MARKER):
        if other == marker:
            continue
        boundary = _marker_span(region, other)
        if boundary is not None:
            region = region[:boundary[0]]

    records: list[CompileMessage] = []
    current: dict[str, str] = {}
    for line in region.splitlines():
        match = _FIELD_RE.match(line)
        if match is None:
            continue
        field, value = match.group(1), match.group(2)
        if field in {"Error", "Warning"}:
            if field != closing_field:
                current = {}
                continue
            records.append(
                CompileMessage(
                    file    = current.get("File", ""),
                    line    = _as_int(current.get("Line")),
                    column  = _as_int(current.get("Column")),
                    type    = current.get("Type", ""),
                    message = value,
                )
            )
            current = {}
            continue
        if field == "File" and "File" in current:
            # A new File before the previous record closed: drop the partial one
            # rather than merging two records into a wrong row.
            current = {}
        current[field] = value
    return tuple(records)


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    return int(text) if text.isdigit() else None


def message_lines(
    messages : tuple[CompileMessage, ...],
    width    : int = MESSAGE_WIDTH,
) -> list[str]:
    """Render one stanza per message: locator, then type and text nested under it.

    This was a five-column table until card `#164`. The compiler's prose does not
    fit a column, one ``REFERENCE_NOT_FOUND`` row ran past 150 characters, and
    the terminal's own re-wrap then broke the table's alignment as well. A stanza
    per message keeps every line inside the 80 columns ``recompile`` already
    treats as the console ceiling (``_MAX_COMPILE_ERROR_LINE_WIDTH``).

    Where ``recompile`` truncates its message column, this wraps: the validate
    message *is* the answer, ``REFERENCE_NOT_FOUND`` names the file that is
    missing, so cutting it at a width would throw away the reason for the run.
    """
    lines: list[str] = []
    for message in messages:
        if lines:
            lines.append("")
        lines.append(f"{_LOCATOR_INDENT}{_locator(message)}")
        if message.type:
            lines.append(f"{_DETAIL_INDENT}{message.type}")
        lines.extend(_wrapped(message.message, width))
    return lines


def _locator(message: CompileMessage) -> str:
    """``file:line:col``, the editor-clickable convention, minus absent parts.

    The compiler omits ``Line``/``Column`` on whole-file findings such as
    ``NO_APEXLANG_FILES``, and a rendered ``application.apx:None:None`` would
    read as a parser bug. A column never appears without its line: the pair is
    only meaningful in order.
    """
    parts = [message.file or "(no file)"]
    if message.line is not None:
        parts.append(str(message.line))
        if message.column is not None:
            parts.append(str(message.column))
    return ":".join(parts)


def _wrapped(text: str, width: int) -> list[str]:
    """Wrap the message body under the detail indent.

    Long words are never broken. A token wider than the remaining space is almost
    always a path, the one thing in the message a reader wants to select and
    paste, so it is allowed to overhang rather than be split across lines.
    """
    body = text.strip()
    if not body:
        return []
    return textwrap.wrap(
        body,
        width             = width,
        initial_indent    = _DETAIL_INDENT,
        subsequent_indent = _DETAIL_INDENT,
        break_long_words  = False,
        break_on_hyphens  = False,
    )
