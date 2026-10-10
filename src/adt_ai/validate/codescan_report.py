"""Turn what SQLcl's `codescan` printed and wrote into findings and a verdict (ADT #1026).

Three facts measured on SQLcl 26.3.0.0, captured in
`tests/fixtures/codescan_transcripts/`, decide the shape of this parser:

* **SQLcl exits 0 whatever it found**, so the exit code carries no signal and
  the report is the gate.
* **The report is empty when there is nothing to report**: a 0-byte file,
  which is not JSON. The console still prints `<n> files, <m> total distinct
  warnings`, so an empty or missing report is clean only when that line says
  zero warnings; one beside a non-zero count, or beside no count at all, is
  output this version cannot read. (First recorded as "no report written",
  because the producer then saved only non-empty reports; the first live run
  read every clean tree as unrecognised.)
* **Positions are 0-based**, and a finding at `0:0` that names a `target` is
  about a component rather than a place in the file.

Every outcome this cannot recognise is a failure, never a pass, for the reason
`report.py` gives for the compiler: an unread failure that renders as clean is
the one bug a gate must not have.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLEAN        = "CLEAN"
FINDINGS     = "FINDINGS"
EMPTY        = "EMPTY"
UNRECOGNISED = "UNRECOGNISED"

#: The line SQLcl closes every scan with, `1 files, 3 total distinct warnings`.
#: Captured, never remembered: `test_codescan_markers_are_captured.py` fails
#: when no transcript carries it.
_SUMMARY_MARKER = "total distinct warnings"
_SUMMARY_RE     = re.compile(
    r"(\d+)\s+files?,\s+(\d+)\s+" + re.escape(_SUMMARY_MARKER), re.IGNORECASE
)


@dataclass(frozen=True)
class Finding:
    """One codescan finding, its file relative to the tree that was scanned.

    ``line`` and ``column`` are 1-based, as an editor counts, and both ``None``
    for a component-level finding.
    """

    file    : str
    line    : int | None
    column  : int | None
    rule    : str
    message : str
    target  : str

    @property
    def key(self) -> tuple[str, str, str, str]:
        """What makes two findings the same one, run to run: never the position.

        A line moves whenever code above it changes, so a baseline keyed on it
        would report every finding under an edit as new.
        """
        return (self.file, self.rule, self.message, self.target)


@dataclass(frozen=True)
class TreeScan:
    outcome  : str
    findings : tuple[Finding, ...]
    raw      : str
    #: How many files SQLcl says it read, ``None`` when it never said.
    files    : int | None = None

    @property
    def failed(self) -> bool:
        """A scan that could not be read, or read nothing, never passes."""
        return self.outcome in (EMPTY, UNRECOGNISED)


def parse_codescan(console: str, report: str | None, tree: Path) -> TreeScan:
    """One tree's scan: its console text and its report, ``None`` when no file exists."""
    body = console or ""
    summary = _SUMMARY_RE.search(body)
    files = int(summary.group(1)) if summary else None
    if report is not None and report.strip():
        findings = _findings(report, tree)
        if findings is None:
            return TreeScan(UNRECOGNISED, (), f"{body.rstrip()}\n{report}", files)
        return TreeScan(FINDINGS if findings else CLEAN, findings, body, files)
    if summary is None:
        return TreeScan(UNRECOGNISED, (), body, files)
    if files == 0:
        return TreeScan(EMPTY, (), body, files)
    if int(summary.group(2)) == 0:
        return TreeScan(CLEAN, (), body, files)
    return TreeScan(UNRECOGNISED, (), body, files)


def _findings(report: str, tree: Path) -> tuple[Finding, ...] | None:
    """Every issue in the report, or ``None`` for a report of any other shape."""
    try:
        entries = json.loads(report)
        if not isinstance(entries, list):
            return None
        findings: list[Finding] = []
        for entry in entries:
            findings.extend(_entry_findings(entry, tree))
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    return tuple(findings)


def _entry_findings(entry: Any, tree: Path) -> list[Finding]:
    """One file's issues; raises on any shape the captures do not show."""
    file = entry["file"]
    issues = entry["issues"]
    if not isinstance(file, str) or not isinstance(issues, list):
        raise TypeError("not a codescan file entry")
    relative = _relative(file, tree)
    return [_finding(relative, issue) for issue in issues]


def _finding(file: str, issue: Any) -> Finding:
    line = int(issue["line"])
    column = int(issue["col"])
    target = str(issue.get("target") or "")
    if line == 0 and column == 0 and target:
        # The component, not a place in the file: `application.apx` with
        # `app:ORDERS`, where a `:1:1` would send the reader to line one.
        return Finding(file, None, None, str(issue["ruleNo"]), str(issue["msg"]), target)
    return Finding(file, line + 1, column + 1, str(issue["ruleNo"]), str(issue["msg"]), target)


def _relative(file: str, tree: Path) -> str:
    """The file as the tree knows it, through a symlinked root as well."""
    path = Path(file)
    if path == tree or path == tree.resolve():
        return path.name
    for base in (tree, tree.resolve()):
        try:
            return path.relative_to(base).as_posix()
        except ValueError:
            continue
    return path.as_posix()


__all__ = [
    "CLEAN",
    "EMPTY",
    "FINDINGS",
    "UNRECOGNISED",
    "Finding",
    "TreeScan",
    "parse_codescan",
]
