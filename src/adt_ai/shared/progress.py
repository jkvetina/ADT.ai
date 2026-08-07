from __future__ import annotations

import re

DROPBOX_PATH_RE = re.compile(r"/Users/[^/]+/Library/CloudStorage/Dropbox/")


def print_adt_header(message: str, append: str = "", file=None) -> None:
    print(file=file)
    print(f"{message}{(' ' + append).rstrip()}", file=file)
    print("-" * len(message), file=file)


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

    Underlined across its full width — unlike the ``append`` form, where the rule
    covers only the message. The title is one unit here, not a label plus a value.
    """
    print_adt_header(module_banner(title), file=file)


FAILED_STATUS = "FAILED"


class DottedProgressBar:
    def __init__(self, line_width: int = 78) -> None:
        self.line_width = line_width

    def print_failed(self, header: str) -> None:
        """Complete an abandoned crawling row with FAILED, and end the line.

        The crawling row is rewritten in place with ``\\r`` and no trailing
        newline. Leaving it that way meant whatever printed next — for an
        uncaught failure, the error banner's own leading blank line — was spent
        terminating it instead, so the banner landed flush against the progress
        bar (ADT #232).
        """
        print(f"\r{self.failed_text(header)}", flush=True)

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
        print(f"\r{line}", end=end, flush=True)

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
