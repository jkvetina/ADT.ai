from __future__ import annotations

import re
import sys

DROPBOX_PATH_RE = re.compile(r"/Users/[^/]+/Library/CloudStorage/Dropbox/")


def print_adt_header(message: str, append: str = "", file=None) -> None:
    print(file=file)
    print(f"{message}{(' ' + append).rstrip()}", file=file)
    print("-" * len(message), file=file)


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
    def __init__(self, line_width: int = 78) -> None:
        self.line_width = line_width
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
        """
        if self._open_row is None or self._open_row == header:
            return ""
        return "\n"

    def failed_text(self, header: str) -> str:
        # Padded to the crawling row's exact width: ``\r`` returns the cursor
        # but clears nothing, so a shorter replacement leaves that row's tail
        # on screen behind it.
        width = len(self.line_text(header, 0, 0))
        left = f"{header} " if header else ""
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

    def line_text(self, header: str, percent: int, seconds: int) -> str:
        max_dots = progress_dot_capacity(header, self.line_width)
        dot_count = min(max_dots, int(max_dots * percent / 100))
        progress = f"{'.' * dot_count} {percent}%"
        if not header:
            progress_width = self.line_width - 9
            return f"{progress:<{progress_width}} {format_seconds(seconds)} "
        progress_width = self.line_width - 9 - len(header) - 1
        return f"{header} {progress:<{progress_width}} {format_seconds(seconds)} "


def format_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"0:00:{seconds:02d}".rjust(8, " ")
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}".rjust(8, " ")


def progress_dot_capacity(header: str, width: int) -> int:
    extra = f"{header} " if header else ""
    return width - 5 - len(extra) - 9


def fixed_width_count_line(
    label: str,
    count: int,
    *,
    total: int | None = None,
    count_width: int = 1,
    indent: str = "  ",
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
    indent: str = "  ",
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
    def __init__(self, *, line_width: int = 78, indent: str = "  ") -> None:
        self.line_width = line_width
        self.indent = indent
        self._active_left: str | None = None

    def begin(self, label: str, *, indent: str | None = None) -> None:
        self._active_left = f"{self.indent if indent is None else indent}{label}"
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
