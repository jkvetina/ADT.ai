"""`ERRORS & FAILURES:`, the per-record half of the ut3 report.

Split out of `render.py` at the repo's 20 KB per-file context budget
(`tests/contracts/test_context_file_size.py`). The seam is the one `render.py`'s
own docstring already draws: the two roll-ups and the results block are
**tables and rows**, sized to bounded values, while everything free-text a run
produces (a failure message, an ORA stack) is a **per-record stanza**, headed
by its status and wrapped beneath it, never a table column that would be sized
to its widest sentence. Nothing here reads a gateway, a table layout or a
coverage figure; it takes outcomes and a cap and prints stanzas.
"""

from __future__ import annotations

import textwrap

from adt_ai.export_db.runner import print_adt_header
from adt_ai.ut3.inventory import RESULT_ERRORED, RESULT_FAILED
from adt_ai.ut3.layout import DETAIL_INDENT, HEADING_INDENT, MESSAGE_WIDTH
from adt_ai.ut3.runner import Ut3Result


def print_problems(result: Ut3Result, limit: int | None = None) -> None:
    """One stanza per non-passing test: what happened, then why.

    **The status leads the heading**, `ERROR > A_UT.TEST_LABELS#LOOKUP_RAISES`,
    not the name with a trailing verdict. The reader is scanning for the ones
    that errored, and a status word parked at the end of a package-qualified
    identifier is behind the longest, most variable part of the line.

    **Every stanza gets a blank line above it**, the first one included, so the
    section reads as a list of records rather than one wall of text under a
    header.

    **The section is capped, and the cap is why the tables below it are legible.**
    Jan's 2026-08-11 run on `ICT_OWNER` produced 397 stanzas over 3 060 lines, so
    `SUMMARY:` came off the bottom of a terminal whose scrollback could not hold
    them, the header, the column row and the separator were all printed and all
    gone, which reads exactly like a renderer that dropped them. Nothing here was
    broken; the section was simply longer than the screen it prints on.

    ``limit`` is `ut_limit_errors`, and ``0`` or None prints every stanza.

    A package that could not run is not here and is not anywhere: an INVALID or
    unparsed `_UT` package is ignored outright.

    **No output mode reaches this section.** The section above it changes shape
    per mode, the `RUNNING TESTS:` bar by default, `TEST RESULTS:` under
    `-verbose`, nothing under `-silent`, and this one prints identically in all
    three. That is deliberate rather than incidental: the default mode drops the
    per-test rows, so these stanzas are the only place a failure's message
    survives, and a quiet mode that also took them would be the unreadable one.
    """
    problems = [
        outcome
        for outcome in result.outcomes
        if outcome.result in {RESULT_FAILED, RESULT_ERRORED}
    ]
    if not problems:
        return
    shown = problems[:limit] if limit else problems
    print_adt_header(_problems_header(len(shown), len(problems)))
    for outcome in shown:
        print()
        print(f"{HEADING_INDENT}{outcome.result} > {outcome.package}.{outcome.test}")
        for line in _wrapped_lines(outcome.message):
            print(line)
    print()


def _problems_header(shown: int, total: int) -> str:
    """`ERRORS & FAILURES:`, or `FIRST <n> ERRORS & FAILURES:` when the cap bit.

    **The header names the cap only when there is one to name**, the rule
    `_summary_header` already follows for `SUMMARY FOR <PATTERNS>:` one section
    below. A run with three failures under a cap of twenty is not showing the
    first twenty of anything, and a heading that says it is would be the only
    untrue line in the report.

    The suppressed stanzas are never silently gone: `SUMMARY:` and `MODULES:`
    carry the real `FAIL` and `ERROR` counts for every suite, uncapped, which is
    what the header points the reader at when the two disagree.
    """
    if shown >= total:
        return "ERRORS & FAILURES:"
    return f"FIRST {shown} ERRORS & FAILURES:"


def _wrapped_lines(message: str) -> list[str]:
    if not message:
        return []
    lines: list[str] = []
    for paragraph in message.splitlines():
        if not paragraph.strip():
            continue
        lines.extend(
            textwrap.wrap(
                paragraph.strip(),
                width              = MESSAGE_WIDTH,
                initial_indent     = DETAIL_INDENT,
                subsequent_indent  = DETAIL_INDENT,
                break_long_words   = False,
                break_on_hyphens   = False,
            )
            or [f"{DETAIL_INDENT}{paragraph.strip()}"]
        )
    return lines


__all__ = [name for name in globals() if not name.startswith("__")]
