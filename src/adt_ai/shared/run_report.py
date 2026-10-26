"""The one JSON object a `-json` run prints instead of its screen (ADT #1070).

An agent driving `adtai` read the console and parsed it: the refusal header, the
`file:line:col` locator, the counts on a closing row. Every one of those is
written for a person and is free to change for one, so the parse broke the day a
screen improved. `-json` gives that reader data instead. Jan's scope, asked with
chips before this was written: the flag is the same in every module, and it
changes how ALL modules present their findings, never one command at a time.

The shape is one envelope for every command, so an agent learns it once:

* `errors`: each refusal the run printed, from the one shared screen
  (`print_adt_error`), so every module reports its refusals with no work of
  its own.
* `diagnostics`: what the command FOUND, recorded by each module from the
  object it already held before rendering it: a compile error, a failed test,
  an object the database refused, a difference between two schemas.
* `artifacts`: the files the run wrote, recorded by the one writer every
  artifact goes through (`text_files`), so no module enrols on its own.
* `result`: the counts a module's closing rows print.

Collection is a no-op outside a `-json` run. A call site records
unconditionally and a human run keeps nothing, which is what lets the recording
sit beside the rendering rather than behind a flag test at every site.
"""

from __future__ import annotations

import json
import re
import textwrap
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adt_ai import __version__

#: Bumped when a key changes meaning or leaves; a new key is not a new version.
FORMAT_VERSION = 1

#: How many written files are listed by path. An `export_db` of a whole schema
#: writes thousands, and a reader paying tokens for every path would rather ask
#: git. `total` still counts all of them and `truncated` says the list stopped.
ARTIFACT_LIMIT = 100

SEVERITIES = ("error", "warning", "info")

#: The diagnostic fields in the order they print, beside the three every entry
#: carries. An absent one is left out rather than written as `null`.
_OPTIONAL_FIELDS = (
    "file", "line", "column", "schema", "object_type", "object_name",
    "app", "page", "hint",
)


@dataclass
class RunReport:
    #: None when the first word named no command (`UNKNOWN COMMAND`).
    command     : str | None
    errors     : list[dict[str, Any]] = field(default_factory=list)
    diagnostics : list[dict[str, Any]] = field(default_factory=list)
    artifacts   : dict[str, str]       = field(default_factory=dict)
    summary     : dict[str, Any]       = field(default_factory=dict)

    def payload(
        self,
        *,
        exit_code : int,
        elapsed   : float,
        screen    : str | None = None,
    ) -> dict[str, Any]:
        """The object. `screen` is the captured console, kept only as a fallback.

        A run that recorded nothing at all, no refusal, no finding and no
        counts, is a mode no module has given data yet, and dropping its screen
        would hand the reader an empty answer to a question the command did
        answer. Its lines ride along under `screen` instead, so nothing a person
        would have seen is lost; any recorded field means the data is there and
        the screen is left out.
        """
        files = [
            {"path": path, "action": action}
            for path, action in list(self.artifacts.items())[:ARTIFACT_LIMIT]
        ]
        artifacts: dict[str, Any] = {"total": len(self.artifacts), "files": files}
        if len(self.artifacts) > ARTIFACT_LIMIT:
            artifacts["truncated"] = True
        payload: dict[str, Any] = {
            "format"      : FORMAT_VERSION,
            "adt"         : __version__,
            "command"     : self.command,
            "status"      : "SUCCESS" if exit_code == 0 else "FAILED",
            "exit_code"   : exit_code,
            "elapsed"     : round(elapsed, 2),
            "errors"      : self.errors,
            "diagnostics" : self.diagnostics,
            "artifacts"   : artifacts,
            "result"      : self.summary,
        }
        if screen and not (self.errors or self.diagnostics or self.summary):
            payload["screen"] = screen_lines(screen)
        return payload

    def render(self, *, exit_code: int, elapsed: float, screen: str | None = None) -> str:
        """One compact line: an agent pays for every space it reads."""
        payload = self.payload(exit_code=exit_code, elapsed=elapsed, screen=screen)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"


_ACTIVE: RunReport | None = None


def active() -> RunReport | None:
    return _ACTIVE


@contextmanager
def collecting(command: str | None) -> Iterator[RunReport]:
    """Collect one run's report; nothing is collected outside this block."""
    global _ACTIVE
    previous = _ACTIVE
    report = RunReport(command)
    _ACTIVE = report
    try:
        yield report
    finally:
        _ACTIVE = previous


def diagnostic(
    severity : str,
    type     : str,
    message  : str,
    **fields : Any,
) -> None:
    """Record one finding. `type` is the tool's own code (`SYNTAX`, `PLS-00201`)."""
    if severity not in SEVERITIES:
        raise ValueError(f"{severity!r} is not a diagnostic severity ({', '.join(SEVERITIES)})")
    unknown = set(fields) - set(_OPTIONAL_FIELDS)
    if unknown:
        raise ValueError(f"unknown diagnostic field(s): {', '.join(sorted(unknown))}")
    report = _ACTIVE
    if report is None:
        return
    entry: dict[str, Any] = {"severity": severity, "type": type, "message": message}
    for name in _OPTIONAL_FIELDS:
        value = fields.get(name)
        if value is not None and value != "":
            entry[name] = str(value) if isinstance(value, Path) else value
    report.diagnostics.append(entry)


def result(**values: Any) -> None:
    """Merge counts into the run's `result`; a later call for a key wins."""
    report = _ACTIVE
    if report is not None:
        report.summary.update(values)


def listing(name: str, items: Sequence[Any]) -> None:
    """A list the command answers with (rows, hits, authors), capped like `artifacts`.

    `<name>` holds at most `ARTIFACT_LIMIT` entries and `<name>_total` always
    counts all of them, so a reader can tell a short answer from a cut one.
    """
    report = _ACTIVE
    if report is None:
        return
    report.summary[name] = list(items[:ARTIFACT_LIMIT])
    report.summary[f"{name}_total"] = len(items)


def artifact(path: Path, *, created: bool) -> None:
    """A file the run wrote. Called by `text_files`, never by a module."""
    report = _ACTIVE
    if report is None or _is_internal(path):
        return
    key = str(path)
    if key in report.artifacts:
        # Written twice in one run: it now exists either way, and the first
        # write said whether the run created it.
        return
    report.artifacts[key] = "created" if created else "updated"


#: `shared/internal_paths`' `config/internal/`, spelled here because that module
#: writes through `text_files`, which imports this one. A test holds the two equal.
INTERNAL_PARTS = ("config", "internal")


def _is_internal(path: Path) -> bool:
    """ADT.ai's own bookkeeping (timers, watermarks), never a file the user asked for."""
    parts = Path(path).parts
    width = len(INTERNAL_PARTS)
    return any(parts[index:index + width] == INTERNAL_PARTS for index in range(len(parts)))


def refusal(
    code        : str,
    description : str | Sequence[str],
    details     : str | Sequence[str] | None,
) -> None:
    """A refusal screen, as data. Called by `print_adt_error`, never by a module."""
    report = _ACTIVE
    if report is None:
        return
    lines = [*_lines(description), *(_lines(details) if details else [])]
    message = lines[0] if lines else ""
    report.errors.append({"code": code, "message": message, "details": lines[1:]})


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def screen_lines(screen: str) -> list[str]:
    """The captured console as plain lines: no colour codes, no blank lines.

    A carriage return is a progress row redrawing itself, so only what it
    settled on is kept.
    """
    lines: list[str] = []
    # `split`, never `splitlines`, which breaks on the `\r` itself and would
    # keep every frame of a redrawn row as a line of its own.
    for raw in _ANSI.sub("", screen).split("\n"):
        line = raw.rsplit("\r", 1)[-1].rstrip()
        if line:
            lines.append(line)
    return lines


def _lines(text: str | Sequence[str]) -> list[str]:
    raw = textwrap.dedent(text).splitlines() if isinstance(text, str) else list(text)
    return [line.rstrip() for line in raw if line.strip()]
