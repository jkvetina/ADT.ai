"""What a driven SQLcl printed, read back one line at a time (ADT #396, #449, #760).

Split out of ``shared.sqlcl_session`` by ADT #923, to make room there for the
long-statement fix, along a seam that module already had: everything here reads
text and none of it touches a process. The session's reader, the gateway's error check
and the Windows script transport share this one answer about what a prompt
fragment, a sentinel, SQLcl's own noise and an Oracle error look like.
``error_in`` and ``_clean`` are still importable from ``shared.sqlcl_session``,
which is where callers have always found them.
"""

from __future__ import annotations

import re

_PROMPT = "SQL>"

# SQLcl's own noise, dropped before anything tries to read a reply. The memory
# warning is what a large `SET LONG` earns, and the JVM prints its own notice when
# JAVA_TOOL_OPTIONS is set in the environment.
_NOISE = re.compile(
    r"^(Picked up JAVA_TOOL_OPTIONS|Warning: This LONG setting|It is recommended to reduce)"
)

# An Oracle error in SQLcl's own report block. Matched on the report markers rather
# than on the code alone: `recompile` and `ut` both SELECT error text containing
# `ORA-` and `PLS-` codes, so a bare code search would read a successful query's
# own rows as a failure.
_ERROR_REPORT = re.compile(
    r"^(Error starting at line|Error report -|Error at Command Line|SP2-\d+|USAGE:)"
)
_ERROR_CODE = re.compile(r"\b(ORA-\d{5}|PLS-\d{5}|SP2-\d{4})\b")


def _is_sentinel(line: str, marker: str) -> bool:
    """True for the marker SQLcl printed, never for the `prompt` line we wrote.

    This used to be a plain `marker in line`, which is correct exactly as long as
    the terminal does not echo. Measured on 2026-08-21, a Windows pseudo console
    does: the echoed `prompt <<<ADT-SQLCL-n>>>` matched first, the exchange ended
    before SQLcl had answered, and the caller got an empty string as its reply.

    The two are told apart by shape rather than by timing. What we write is a
    `prompt` COMMAND; what SQLcl writes back is the bare marker. The `SQL>`
    fragments come off first because the prompt carries no newline, so a marker
    can arrive with one glued to its front.
    """
    text = line.strip()
    while text.startswith(_PROMPT):
        text = text[len(_PROMPT):].strip()
    return marker in text and not text.lower().startswith("prompt")


def _clean_line(line: str) -> str | None:
    """One line with its prompt fragments off, or ``None`` for SQLcl's own noise.

    Per line rather than per transcript so a live reader sees exactly what the
    returned transcript will carry (ADT #760); a reader shown the raw line and a
    caller handed the cleaned one is two answers to one question.
    """
    text = line
    while text.lstrip().startswith(_PROMPT):
        text = text.lstrip()[len(_PROMPT):]
    if _NOISE.match(text.strip()):
        return None
    return text


def _clean(lines: list[str]) -> list[str]:
    """Drop the prompt fragments and SQLcl's own startup noise."""
    return [cleaned for line in lines if (cleaned := _clean_line(line)) is not None]


def error_in(output: str) -> str | None:
    """The Oracle error SQLcl reported, or ``None``.

    Keyed on SQLcl's own report markers rather than on a bare code search: a
    successful `recompile` or `ut` query returns rows whose text carries `ORA-`
    and `PLS-` codes, and reading those as a failure would break the two commands
    most likely to be run against a broken schema.
    """
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if not _ERROR_REPORT.match(line.strip()):
            continue
        for candidate in lines[index:]:
            found = _ERROR_CODE.search(candidate)
            if found:
                return candidate.strip()
        return line.strip()
    return None
