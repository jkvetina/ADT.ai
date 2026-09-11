"""The one shape every refusal takes: `ERROR - <CODE>:` (ADT #764).

Eight shapes reported a refusal before this module, and none of the differences
were design. `validate` prefixed `validate: error:`, `connection` prefixed
`connection:`, `search_repo` prefixed `Error:` and printed it to **stdout**,
`patch -deploy` prefixed nothing at all, `export_db` rendered a dashed section,
and three screens replaced the module banner with `APEX DEPLOYMENT TOOL - ERROR`.
An argument refusal exited 2 in one module and 1 in the next. One screen of the
eight offered a remedy.

Jan, 2026-09-11: *"The errors must look the same and it must be clear it is an
error. We have different variants, different indentations, different style. It
is a travesty."*

Four decisions settled with him before this was written, each of which the code
below holds rather than restates:

* **The code is words**, reusing the screen names that already existed, so
  nothing has to learn a numeric registry to read a screen.
* **The description is indented plain text**, never a flat bullet list, with
  nested lists kept where the content genuinely is one. Jan: *"We should ditch
  the list ... You can keep the nested lists when it make sense."*
* **The `-debug` hint survives only where a traceback is the diagnosis**, which
  is `HINT_CODES`. Jan: *"Ditch this from know errors like CONNECTION error or
  missing CONFIG file, it is not relevant there at all, you are just confusing
  the user."*
* **The stream is stderr and the exit code is a property of the code**, so two
  modules cannot disagree about the same class of failure.

`ERROR_CODES` is the closed set, and `print_adt_error` refuses anything outside
it. That is deliberate: a call site that can pass a new string is exactly how
eight shapes accumulated, and `tests/contracts/console_surface.txt` now sees
every one of them because the scanner renders this function's first argument as
the header it actually prints.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TextIO

from adt_ai.shared.announce import settle_screen_before_error
from adt_ai.shared.progress import SECTION_GAP, print_adt_header

#: Every refusal ADT.ai can report. Adding one is a console redesign and needs
#: Jan's say-so, the same gate `console_surface.txt` puts on a section header.
ERROR_CODES: tuple[str, ...] = (
    "ARGUMENT INVALID",
    "UNKNOWN COMMAND",
    "INPUT NOT FOUND",
    "CONFIGURATION NOT FOUND",
    "CONFIGURATION INVALID",
    "CREDENTIAL UNAVAILABLE",
    "DATABASE CONNECTION FAILED",
    "DATABASE QUERY FAILED",
    "SQLCL SCRIPT FAILED",
    "STARTUP FAILED",
    "UNEXPECTED ERROR",
)

#: A refusal about what the user typed. Exits 2, the argparse convention, and
#: the reader's next move is to retype the command rather than fix an
#: environment.
USAGE_CODES = frozenset({"ARGUMENT INVALID", "UNKNOWN COMMAND"})

#: Where a Python traceback is genuinely the next piece of information. The
#: first two are failures ADT.ai could not classify at all; the second two print
#: foreign output (a SQL statement, a SQLcl transcript) where the ADT call path
#: that produced it is still missing.
HINT_CODES = frozenset(
    {
        "UNEXPECTED ERROR",
        "STARTUP FAILED",
        "DATABASE QUERY FAILED",
        "SQLCL SCRIPT FAILED",
    }
)

DEBUG_HINT = "Use -debug to show the Python traceback."

#: Every body line sits here. A caller's own leading spaces are kept on top of
#: it, which is what lets a searched-paths list nest at four without this module
#: knowing anything about lists.
BODY_INDENT = "  "


def error_header(code: str) -> str:
    """The rendered header, so a test and the scanner agree on one spelling."""
    return f"ERROR - {code}:"


def exit_code_for(code: str) -> int:
    """2 for a refusal about what was typed, 1 for a failure during the work.

    A property of the code rather than of the call site, deliberately: before
    this card `adtai validate -bogus` exited 2, `adtai recompile -tables
    -indexes` exited 1 and `adtai frobnicate` exited 1, which made the exit code
    unusable for anything reading it.
    """
    _check(code)
    return 2 if code in USAGE_CODES else 1


def _check(code: str) -> None:
    if code not in ERROR_CODES:
        raise ValueError(
            f"{code!r} is not an ADT.ai error code. The set is closed "
            f"({', '.join(ERROR_CODES)}); adding one is a console redesign."
        )


def _block(text: str | Sequence[str]) -> list[str]:
    """Body lines, indented, with blank lines left genuinely blank.

    A blank carrying `BODY_INDENT` is trailing whitespace on a line nobody can
    see, which `test_text_write_newline.py`'s sibling rules exist to keep out of
    the console.
    """
    lines = text.splitlines() if isinstance(text, str) else list(text)
    return [f"{BODY_INDENT}{line}".rstrip() for line in lines]


def print_adt_error(
    code: str,
    description: str | Sequence[str],
    details: str | Sequence[str] | None = None,
    *,
    debug_available: bool = False,
    file: TextIO | None = None,
) -> None:
    """Render one refusal, always to stderr.

    `description` is what failed, `details` is the optional explanation or the
    action steps under it. Both accept a string or a sequence of lines; a
    sequence is the shape to reach for when the content is a list, because each
    entry keeps whatever leading spaces it was given.

    `debug_available` is the caller's `hasattr(args, "debug")`, so the parser
    stays the single authority on whether the flag exists. The hint still prints
    only for `HINT_CODES`: five commands never declared `-debug`, and on the
    seven codes that name their own cause a traceback adds nothing the screen
    does not already say.
    """
    _check(code)
    stream = file if file is not None else sys.stderr
    # Settle stdout only when a run actually wrote there. `#465` added this for
    # a failure raised mid-row, where the crawling progress line carries no
    # newline and the banner would land flush against it; on a screen that is
    # entirely a refusal, stdout has had nothing at all and padding it to two
    # newlines prints two blank lines onto an otherwise empty stream (ADT #764,
    # after `_run_invalid_command` and the top-level argument screen moved
    # wholly to stderr). `print_adt_header` guards its own normalize the same
    # way, on the same attribute.
    if getattr(sys.stdout, "had_output", False):
        settle_screen_before_error()
    else:
        # The banner itself went to stderr, so stdout has nothing to settle and
        # `#465`'s two-blank rule lands on the wrong stream: measured live,
        # `export_db` opened its error on two blank lines and `validate` on one,
        # on the same tool, decided by which stream the banner took. The error
        # screen opens on two, always.
        normalize = getattr(stream, "normalize_trailing_newlines", None)
        if callable(normalize) and getattr(stream, "had_output", False):
            normalize(SECTION_GAP)
    print_adt_header(error_header(code), file=stream)
    for line in _block(description):
        print(line, file=stream)
    blocks: list[str | Sequence[str]] = []
    if details:
        blocks.append(details)
    if debug_available and code in HINT_CODES:
        blocks.append(DEBUG_HINT)
    for block in blocks:
        print(file=stream)
        for line in _block(block):
            print(line, file=stream)
    print(file=stream)
