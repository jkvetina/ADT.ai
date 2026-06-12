from __future__ import annotations


class DottedProgressBar:
    def __init__(self, line_width: int = 78) -> None:
        self.line_width = line_width

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
