from __future__ import annotations

import re
import sys

from adt_ai.shared.announce import mark_announced, mark_finished

DROPBOX_PATH_RE = re.compile(r"/Users/[^/]+/Library/CloudStorage/Dropbox/")


def print_adt_header(message: str, append: str = "", file=None) -> None:
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
    file keyed ``ict_owner`` and a ``-schema ict_owner`` argument both survive to
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


def print_module_banner(title: str = "", file=None) -> None:
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


def _commit_line() -> None:
    """Put a finished row's terminating newline on the stream now, not later.

    The runtime's ``_StdoutTracker`` holds trailing newlines back so the shared
    ``TIMER`` footer can still retract them, correct at the end of a command,
    and the reason ``print_adt_table`` already commits its own. Applied to a
    progress row it meant a row that had *finished* stayed unterminated on
    screen until the next action's first frame flushed the newline for it,
    including across the ``apex_timers.yaml`` write that sits between two
    export actions. A row that is done is done: closing it here removes the
    window in which anything else can land on its line (ADT #323).
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
        _commit_line()

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
        the single-header bars in ``rebuild``/``ut3``, never trip it. On a
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
            _commit_line()
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


def fixed_width_count_line(
    label: str,
    count: int,
    *,
    total: int | None = None,
    count_width: int = 1,
    indent: str = ROW_INDENT,
    line_width: int = 78,
) -> str:
    left = f"{indent}{label}"
    right = fixed_width_count_suffix(count, total=total, count_width=count_width)
    dots_len = line_width - len(left) - len(right) - 2
    dots = "." * max(1, dots_len)
    return f"{left} {dots} {right}"


def fixed_width_status_line(
    label: str,
    status: str,
    *,
    indent: str = ROW_INDENT,
    line_width: int = 78,
) -> str:
    left = f"{indent}{label}"
    right = status
    dots_len = line_width - len(left) - len(right) - 2
    dots = "." * max(1, dots_len)
    return f"{left} {dots} {right}"


def fixed_width_count_suffix(
    count: int,
    *,
    total: int | None = None,
    count_width: int = 1,
) -> str:
    if total is None:
        return f"{count:>{count_width}}"
    return f"{count:>{count_width}} | {total:>7}"


class FixedWidthProgressPrinter:
    def __init__(self, *, line_width: int = 78, indent: str = ROW_INDENT) -> None:
        self.line_width = line_width
        self.indent = indent
        self._active_left: str | None = None

    def begin(self, label: str, *, indent: str | None = None) -> None:
        self._active_left = f"{self.indent if indent is None else indent}{label}"
        # No mark_announced() here, nor on the dotted bar, nor on a streamed
        # table half-row: all three stop mid-line, and an open line is what the
        # runtime reads as an announcement (cli/constants.py _StdoutTracker).
        print(self._active_left, end="", flush=True)

    def finish(
        self,
        label: str,
        count: int,
        *,
        total: int | None = None,
        count_width: int = 1,
        indent: str | None = None,
    ) -> None:
        left = self._active_left or f"{self.indent if indent is None else indent}{label}"
        right = fixed_width_count_suffix(count, total=total, count_width=count_width)
        dots_len = self.line_width - len(left) - len(right) - 2
        dots = "." * max(1, dots_len)
        print(f" {dots} {right}")
        self._active_left = None

    def fail(
        self,
        label: str,
        *,
        status: str = "FAILED",
        indent: str | None = None,
    ) -> None:
        self.status(label, status, indent=indent)

    def status(
        self,
        label: str,
        status: str,
        *,
        indent: str | None = None,
    ) -> None:
        """Complete the active row with an arbitrary status word, not a count.

        For blocking work with no natural count (a scan, a session setup
        call) that would otherwise leave the row's label sitting alone with
        no visible progress until the whole thing resolves.
        """
        left = self._active_left or f"{self.indent if indent is None else indent}{label}"
        dots_len = self.line_width - len(left) - len(status) - 2
        dots = "." * max(1, dots_len)
        print(f" {dots} {status}")
        self._active_left = None

    def line(self, text: str) -> None:
        print(text)


def open_section(title: str) -> FixedWidthProgressPrinter:
    """A section header, and the printer its rows will stream through.

    The pair every announced phase needs, so a caller cannot print the header
    and then forget the label under it, which is the half-fix `#360` was written
    to stop.
    """
    print_adt_header(title)
    return FixedWidthProgressPrinter()


def streamed(progress: FixedWidthProgressPrinter, label: str, read):
    """Stream `label`, run `read`, close the row with how much came back.

    The whole shape of the console contract in one call, for the commonest case
    it applies to: a read that blocks, produces a countable result and has no
    rows of its own to stream. Shared rather than re-written per module because
    `#360` needed it in five of them, and a per-module copy is how the
    label-then-work order drifts out of one of them again.
    """
    progress.begin(label)
    try:
        result = read()
    except Exception:
        progress.fail(label)
        raise
    progress.finish(label, len(result) if hasattr(result, "__len__") else 1)
    return result
