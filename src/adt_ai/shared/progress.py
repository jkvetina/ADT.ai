from __future__ import annotations

import re
import sys
from typing import TextIO

from adt_ai.shared.announce import mark_announced, mark_finished

DROPBOX_PATH_RE = re.compile(r"/Users/[^/]+/Library/CloudStorage/Dropbox/")


# The header's own blank, which every header gets.
OPENING_GAP = 1
# Plus one for whatever closed above it, when something did.
SECTION_GAP = 2


def print_adt_header(message: str, append: str = "", file: TextIO | None = None) -> None:
    """A titled section: two blank lines above it when something closed above,
    one when nothing did (ADT #468, corrected by #494).

    **The gap is normalized, never added to whatever is already there**, and that
    is the whole of it. An unconditional blank makes the gap a property of what
    happened to print ABOVE the header: a section that closes itself leaves one
    behind and a section ending on a bare row leaves none, so one header rendered
    two blanks and the next rendered one, on the same screen, for a reason no
    call site could see. `#443` added a ``lead_gap`` knob for the site that
    wanted the wider gap, which fixed that site and moved the problem: with a
    table above it the same knob rendered THREE. Jan, 2026-08-22: *"You invented
    3 empty lines in between RECENT COMMITS: and PATCH FILES:"*, and on `#451`,
    *"the looks of headers must be consistent!"*

    `normalize_trailing_newlines` **caps as well as pads**, which is what lets
    one call cover every starting shape: a closed section gets nothing added, a
    bare row gets one, a crawling progress row gets its own line terminated
    first. It is the mechanism the `TIMER` footer has used since `#269` and the
    error banner since `#465` (`shared/announce.settle_screen_before_error`), and
    the project SOP already states the rule in those words for the footer alone,
    *"Spacing has one knob-free authority ... No per-call `pending_newlines`
    overrides"*. Section headers are the surface that never got it.

    **The count comes from Jan's own arithmetic on `#456`**: *"Every header must
    have 1 empty line above, right. So why dont you print extra line after you
    run the dependencies? That would match both cases."* One blank belongs to the
    header, one to the section above it.

    `#468` gave both halves to every header and exempted only the H1, on the
    ground that it is first on the stream. That is true of the H1 and it is not
    the whole of it: a module banner is the command's TITLE rather than a
    section, so the first real section on a screen has no section above it
    either, and its second blank was separating it from nothing on every screen
    the tool prints. Jan, 2026-08-23: *"Two empty lines above first header is
    wrong (We are not counting module header)"*. So the banner and the section
    directly under it both open on `OPENING_GAP`, and everything below keeps the
    pair.

    **The predicate is a count, deliberately, and the wider one was written and
    withdrawn.** Asking instead whether the section above printed any ROWS reads
    as the more principled rule and reaches past the report: measured on the
    `patch -deploy` screen, a connection block that renders no version rows made
    `CONNECTING TO SCHEMA <s>:` and `DEPLOYING PATCH:` move too, headers nobody
    complained about, and a gap that changes with what a section happened to find
    is the opposite of `#451`'s *"the looks of headers must be consistent!"*.
    `#372` is the standing form of this: a predicate finer than the instruction
    ends up writing the design.

    **Three limits, written here rather than found later.** The H1 also takes the
    branch below on `had_output` alone, which is `False` on the first write of a
    run, so it keeps its single blank whatever the tracker says. The
    normalization needs the runtime's own `_StdoutTracker`, so a unit test
    printing to a bare buffer gets one blank and no verdict, degrading exactly as
    `announced` does; a stream carrying the normalizer but not the counter falls
    back to the wider gap, which is the pre-`#494` shape. And it is applied to
    stdout alone: an error screen renders its banner on stderr while the run's
    output went to stdout, and only the interleaved result is a screen, which a
    per-stream counter cannot see. That case keeps its own authority in
    `settle_screen_before_error`, which normalizes stdout before the stderr
    banner is written, and its own tests.
    """
    if (file is None or file is sys.stdout) and getattr(sys.stdout, "had_output", False):
        normalize = getattr(sys.stdout, "normalize_trailing_newlines", None)
        if callable(normalize):
            # The banner is section 1, so a section above this one means 2 or
            # more have already been opened.
            opened = getattr(sys.stdout, "sections_opened", SECTION_GAP)
            normalize(SECTION_GAP if opened > 1 else OPENING_GAP)
    print(file=file)
    print(f"{message}{(' ' + append).rstrip()}", file=file)
    print("-" * len(message), file=file)
    if file is None or file is sys.stdout:
        # A section header names the work that follows it, so the screen is
        # accounted for until something else prints over it. Only on stdout:
        # the error screens render this to stderr, where it explains nothing
        # about what stdout is waiting for (see shared/announce.py).
        #
        # `file is sys.stdout` is not `file is None` written twice.
        # `dependencies` selects its stream by output format (`chrome =
        # sys.stdout if args.format == "table" else sys.stderr`) and passes it
        # explicitly, so its banner reached stdout and announced nothing behind
        # it. The `-app` range then resolved against the database with the guard
        # seeing a blank screen, and `#360` read that as a missing header and
        # printed a new one instead of fixing the test (`#372`).
        mark_announced()


def schema_label(schema: object) -> str:
    """Render a schema name for the console: an Oracle identifier is uppercase.

    A schema reaches a header with whatever casing it was spelled, a connection
    file keyed ``app_owner`` and a ``-schema app_owner`` argument both survive to
    the screen verbatim, while the dictionary they name is uppercase. ``#237``
    uppercased the ``REFRESHING`` header inline and left the four others raw, so
    one run printed both casings a dozen lines apart (ADT #240).

    Display only. The connection file's own casing still owns lookups and paths:
    it renders ``<schema>`` into ``path_apex``, names patch groups, and keys the
    SQLcl connection, so uppercasing at ``Connections.resolve()`` would move
    exported files on disk.
    """
    return str(schema or "").upper()


ADT_TOOL_NAME = "APEX DEPLOYMENT TOOL"
MODULE_BANNER_SEPARATOR = " - "


def module_banner(title: str = "") -> str:
    """The command's H1: ``APEX DEPLOYMENT TOOL - EXPORT_APEX``.

    Separated with ``-`` rather than ``:`` so the dashed rule beneath reads as an
    underline of the whole title. Every *section* header ends with a colon; this
    line is the one documented exception, and giving it its own separator is what
    keeps the two kinds of header telling themselves apart (ADT #237).
    """
    if not title:
        return ADT_TOOL_NAME
    return f"{ADT_TOOL_NAME}{MODULE_BANNER_SEPARATOR}{title}"


def print_module_banner(title: str = "", file: TextIO | None = None) -> None:
    """Print the command H1 through the one shared renderer.

    Underlined across its full width, unlike the ``append`` form, where the rule
    covers only the message. The title is one unit here, not a label plus a value.
    """
    print_adt_header(module_banner(title), file=file)


FAILED_STATUS = "FAILED"

# Every row a command streams under a section header sits two columns in: the
# dotted action rows, the count rows `FixedWidthProgressPrinter` writes, and the
# progress bars. It is one constant so the three renderers below cannot disagree
# about it, which they did until `#378`.
ROW_INDENT = "  "


def row_left_margin(header: str) -> str:
    """What sits left of the dots: the label, or the bare indent it carries.

    **The margin belongs to the row, not to the caller's label.** A labelled row
    used to get its two spaces only because every call site happened to spell
    them into its own string (``"  FULL APP EXPORT"``), so the indent was a
    property of the text passed in rather than a decision this module made. A
    caller with no label to pass therefore got no margin, and both compact bars
    (`export_db` `#373`, `export_apex` `#376`) drew flush against column 0 while
    every row above them was indented. Jan, 2026-08-16: *"All progress bars start
    with 2 spaces ... You should have reusable components, so how come you decide
    to implement it differently?"*

    A labelled row's bytes are unchanged: the label already opens on the indent,
    and the trailing space is the same separator the old format string wrote.

    `#378` moved only the headerless arm. The labelled one returned the caller's
    string verbatim, so every label still spelled its own `"  "` and this
    constant moved nothing on screen: a shared component that was shared for one
    of its two branches. `#380` finished it. The indent is prepended here, every
    label constant lost its leading spaces in the same commit, and not one
    printed byte changed.
    """
    return f"{ROW_INDENT}{header} " if header else ROW_INDENT


def commit_line() -> None:
    """Put a finished row's terminating newline on the stream now, not later.

    The runtime's ``_StdoutTracker`` holds trailing newlines back so the shared
    ``TIMER`` footer can still retract them, correct at the end of a command,
    and the reason ``print_adt_table`` already commits its own. Applied to a
    progress row it meant a row that had *finished* stayed unterminated on
    screen until the next action's first frame flushed the newline for it,
    including across the ``apex_timers.yaml`` write that sits between two
    export actions. A row that is done is done: closing it here removes the
    window in which anything else can land on its line (ADT #323).

    **Public, and called from `shared/fixed_width.py` as well, since ADT #670.**
    The labelled fixed-width row is the other kind of row a command finishes,
    and its three closers were still ending on a plain ``print``, so `#323`'s
    window stayed open on every one of them. One helper for both families,
    because a second copy is how the two ends of a split drift.
    """
    commit = getattr(sys.stdout, "commit_pending", None)
    if callable(commit):
        commit()


class DottedProgressBar:
    """The redrawable dotted row every long-running command crawls under.

    Two constructor knobs exist for the bars whose label CHANGES while the row
    stays open (`export_db -compact`, `export_apex -compact`, `#380`); a bar with
    one fixed label passes neither and renders exactly as it always has.

    ``dot_capacity`` fixes the dot track for the whole run instead of sizing it
    against each label. Without it the track is whatever is left after the label,
    so `PACKAGE` at 50% drew more dots than `MATERIALIZED VIEW` at 50% and the
    bar read as walking backwards. Jan, 2026-08-16: *"the dots should always
    match available space to calculate 100%"*. The caller passes the capacity of
    its WIDEST label, which both compact bars know before they open the row.

    ``single_row`` says every draw is the same row however the label reads, so
    `_row_break` never fires. The guard it turns off is `#323`'s, and turning it
    off is safe only here: a bar that draws one row cannot weld two of them.
    """

    def __init__(
        self,
        line_width: int = 78,
        dot_capacity: int | None = None,
        single_row: bool = False,
    ) -> None:
        self.line_width = line_width
        self._dot_capacity = dot_capacity
        self._single_row = single_row
        # Header of the row being redrawn right now, nothing has closed it yet
        #, or None once a row closed itself. Read by _row_break.
        self._open_row: str | None = None

    def print_failed(self, header: str) -> None:
        """Complete an abandoned crawling row with FAILED, and end the line.

        The crawling row is rewritten in place with ``\\r`` and no trailing
        newline. Leaving it that way meant whatever printed next, for an
        uncaught failure, the error banner's own leading blank line, was spent
        terminating it instead, so the banner landed flush against the progress
        bar (ADT #232).
        """
        print(f"{self._row_break(header)}\r{self.failed_text(header)}", flush=True)
        self._open_row = None
        commit_line()

    def _row_break(self, header: str) -> str:
        """The newline that keeps a new row off a line another row still owns.

        A redrawn row carries **no** newline for as long as it crawls, so the
        only thing separating it from the row after it is that row's own leading
        ``\\r``. The separation is therefore a property of the stream arriving
        intact, not of anything this process did, and when it doesn't arrive
        intact (a resized window, a dropped chunk, a consumer that renders
        ``\\r`` as nothing) the two rows weld into a single 125-character line,
        the second starting at column 46 of the first::

            READABLE COMPONENTS ......  EMBEDDED CODE REPORT ....... 100%  0:00:06

        Jan hit that twice on APP 133/DA-DOCS on 2026-08-13, on two different
        rows of the same export, and it reads as the first row having failed
        when both had in fact succeeded (ADT #323). The bar is the only place
        that knows a row is still open, so it is the only place that can refuse
        to start another one on top of it: worst case now degrades to a row
        frozen at a stale percentage, never two rows on one line.

        Keyed on the header changing, so one row's own in-place redraws, and
        the single-header bars in ``rebuild``/``ut``, never trip it. On a
        healthy sequence every row closes itself, this returns ``""``, and the
        bytes are identical to what the bar has always emitted.

        A ``single_row`` bar opts out entirely: its label names the slice running
        right now rather than a row of its own, so a label change is the same row
        being repainted and a newline there would print one row per object
        type -- the per-item listing `-compact` exists to replace (`#380`).
        """
        if self._single_row or self._open_row is None or self._open_row == header:
            return ""
        return "\n"

    def failed_text(self, header: str) -> str:
        # Padded to the crawling row's exact width: ``\r`` returns the cursor
        # but clears nothing, so a shorter replacement leaves that row's tail
        # on screen behind it.
        width = len(self.line_text(header, 0, 0))
        left = row_left_margin(header)
        dots = "." * max(1, width - len(left) - len(FAILED_STATUS) - 1)
        return f"{left}{dots} {FAILED_STATUS}"

    def print_line(
        self,
        header: str,
        percent: int,
        seconds: int,
        close: bool = False,
    ) -> None:
        line = self.line_text(header, percent, seconds)
        end = "\n" if close else ""
        print(f"{self._row_break(header)}\r{line}", end=end, flush=True)
        self._open_row = None if close else header
        if close:
            commit_line()
        elif percent >= 100:
            # **A bar at 100% is a result wearing an open line.** Every bar in
            # the repo holds the figure at 99 until its units are all accounted
            # for, so this draw is the row saying its own work is done, and
            # whatever blocks next owes an announcement of its own. Here rather
            # than in each bar, so a bar written later inherits it (`#379`).
            mark_finished()

    def line_text(self, header: str, percent: int, seconds: int) -> str:
        # The run's own budget when it has one, so a percentage means the same
        # number of dots on every row of the segment (`#380`); otherwise sized
        # against this label, which is what a one-label bar has always done.
        max_dots = (
            self._dot_capacity
            if self._dot_capacity is not None
            else progress_dot_capacity(header, self.line_width)
        )
        dot_count = min(max_dots, int(max_dots * percent / 100))
        progress = f"{'.' * dot_count} {percent}%"
        # One branch, because the margin is decided in one place. This used to
        # fork on `if not header:` and the headerless arm simply had no left
        # margin at all, which is how two commands shipped a bar sitting two
        # columns left of every row around it (`#378`).
        left = row_left_margin(header)
        progress_width = self.line_width - 9 - len(left)
        return f"{left}{progress:<{progress_width}} {format_seconds(seconds)} "


def format_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"0:00:{seconds:02d}".rjust(8, " ")
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}".rjust(8, " ")


def progress_dot_capacity(header: str, width: int) -> int:
    return width - 5 - len(row_left_margin(header)) - 9


# The labelled fixed-width row family lives in `shared/fixed_width.py` since
# `#436`: this module was over the repo's 20 KB per-file context budget and the
# seam was already there, screen furniture here and the one row shape five
# modules stream through over there. Re-exported rather than swept through every
# call site, the same facade shape `export_db/normalizers.py` keeps over
# `object_normalizers/`, so `from adt_ai.shared.progress import ...` still
# resolves and the split cost no importer a line.
from adt_ai.shared.fixed_width import (  # noqa: E402,F401  (facade: re-exported, unused here)
    DEFAULT_VALUE_WIDTH,
    LABEL_ELISION,
    LEADER_DOTS_MINIMUM,
    FixedWidthProgressPrinter,
    fit_label,
    fixed_width_count_line,
    fixed_width_count_suffix,
    fixed_width_row,
    fixed_width_status_line,
    open_section,
    streamed,
)

__all__ = [
    "ADT_TOOL_NAME",
    "DEFAULT_VALUE_WIDTH",
    "DROPBOX_PATH_RE",
    "DottedProgressBar",
    "FAILED_STATUS",
    "FixedWidthProgressPrinter",
    "LABEL_ELISION",
    "LEADER_DOTS_MINIMUM",
    "MODULE_BANNER_SEPARATOR",
    "OPENING_GAP",
    "ROW_INDENT",
    "SECTION_GAP",
    "annotations",
    "commit_line",
    "fit_label",
    "fixed_width_count_line",
    "fixed_width_count_suffix",
    "fixed_width_row",
    "fixed_width_status_line",
    "format_seconds",
    "mark_announced",
    "mark_finished",
    "module_banner",
    "open_section",
    "print_adt_header",
    "print_module_banner",
    "progress_dot_capacity",
    "re",
    "row_left_margin",
    "schema_label",
    "streamed",
    "sys",
]
