"""Orchestration for the discovery module.

Ties the discovery pieces into one read-only pipeline: gather statements (one
inline ``-sql`` or a ``;``-split ``-file``), validate each as a single SELECT
(safety layer 1), run it under a READ ONLY transaction (safety layer 2), render
the result as a Markdown section, and append the run to a timestamped report.

Validation and per-query database failures are captured into the report as error
sections rather than raised. Callers can still mark setup/connection failures as
fatal so an unreachable database fails the command instead of becoming a result.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from adt_ai.discovery.render import DEFAULT_ROW_LIMIT, query_label, render_result, render_section
from adt_ai.discovery.report import (
    append_sections,
    ensure_discovery_ignored,
    next_query_index,
    report_path,
)
from adt_ai.discovery.validator import DiscoveryValidationError, validate_select_only
from adt_ai.export_db.normalizers import sql_spans
from adt_ai.shared import text_files
from adt_ai.shared.db import QueryGateway

# A distinctive sentinel (not a bare ``/*``) so a hand-written ``/* … */``
# block comment in a ``-file`` is never mistaken for a written-back result block
# and scrubbed. ``-file`` re-runs only strip blocks carrying this marker.
RESULT_BLOCK_START = "/* ADT-RESULT"
RESULT_BLOCK_END   = "*/"

_RESULT_BLOCK_RE = re.compile(
    r"\n" + re.escape(RESULT_BLOCK_START) + r"\n.*?" + re.escape(RESULT_BLOCK_END),
    re.DOTALL,
)


@dataclass(frozen=True)
class DiscoveryRequest:
    root: Path
    when: datetime
    sql: str | None = None
    statements_file: Path | None = None
    limit: int = DEFAULT_ROW_LIMIT
    no_log: bool = False


@dataclass(frozen=True)
class QueryOutcome:
    index: int
    ok: bool
    label: str = ""
    row_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class DiscoveryResult:
    report_path: Path | None
    outcomes: list[QueryOutcome] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    results: list[str] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.ok)

    @property
    def error_count(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.ok)


def split_statements(text: str) -> list[str]:
    """Split a statements file on ``;`` into trimmed, non-empty statements.

    Written-back result blocks (``/* ADT-RESULT … */``) are scrubbed first so
    re-runs see clean SQL. Splitting then treats only *top-level* ``;`` as a
    separator: a ``;`` inside a single-quoted literal, a ``/* … */`` block
    comment, or a ``--`` line comment is data, not a statement boundary.
    """
    clean = _RESULT_BLOCK_RE.sub("", text)
    return _split_top_level_semicolons(clean)


def write_file_results(file_path: Path, results: list[str]) -> None:
    """Rewrite ``file_path`` inserting rendered results after each statement.

    Each statement keeps its ``;`` and gets a ``/* … */`` block appended.
    On re-runs the old blocks are replaced, so the file stays clean.
    Statements without a matching result keep their ``;`` and no block is added.

    Lives beside ``split_statements`` because the two are one contract read in
    opposite directions, the writer emits the ``ADT-RESULT`` sentinel that the
    reader scrubs, and both now share ``_RESULT_BLOCK_RE`` instead of keeping a
    private copy each.
    """
    text = file_path.read_text(encoding="utf-8")
    stripped = _RESULT_BLOCK_RE.sub("", text)
    terminators = _top_level_semicolons(stripped)
    parts: list[str] = []
    start = 0
    result_index = 0
    last_had_result = False
    terminator_ends = {terminator + 1 for terminator in terminators}
    ends = sorted(terminator_ends)
    if not ends or ends[-1] != len(stripped):
        ends.append(len(stripped))
    for end in ends:
        chunk = stripped[start:end]
        terminated = end in terminator_ends
        body = chunk[:-1] if terminated else chunk
        if body.strip():
            parts.append(chunk.rstrip())
            if result_index < len(results):
                parts.append(
                    f"\n{RESULT_BLOCK_START}\n{results[result_index]}\n{RESULT_BLOCK_END}\n"
                )
                last_had_result = True
            else:
                last_had_result = False
            result_index += 1
        elif end == len(stripped) and last_had_result:
            pass
        else:
            parts.append(chunk)
        start = end
    text_files.write_text(file_path, "".join(parts))


def _top_level_semicolons(text: str) -> list[int]:
    """Offsets of statement terminators, excluding strings and comments."""
    return [
        index
        for kind, span_start, span_end in sql_spans(text, identifiers=True)
        if kind == "code"
        for index in range(span_start, span_end)
        if text[index] == ";"
    ]


def _split_top_level_semicolons(text: str) -> list[str]:
    """Split a file of statements on the semicolons that are SQL.

    Scanned through `export_db.normalizers.sql_spans()` since ADT #474 rather
    than through this module's own `in_string` walk, which answered correctly and
    was the fourth copy of one scan. The rule `#299` wrote is that a new scanner
    reuses it or repeats that bug, and the copy in `table_folds.py` had already
    repeated it. Nothing about the answer changes: a semicolon inside a string
    literal or a comment was never a statement terminator here either.
    """
    statements: list[str] = []
    start = 0
    for index in _top_level_semicolons(text):
        statements.append(text[start:index])
        start = index + 1
    statements.append(text[start:])
    return [statement.strip() for statement in statements if statement.strip()]


class DiscoveryRunner:
    def __init__(
        self,
        gateway: QueryGateway,
        fatal_error: Callable[[Exception], bool] | None = None,
    ) -> None:
        self.gateway = gateway
        self.fatal_error = fatal_error or (lambda error: False)

    def run(self, request: DiscoveryRequest) -> DiscoveryResult:
        statements = self._statements(request)
        # ``-nolog`` runs queries and renders results but writes nothing to disk:
        # no report file, no numbering continuation, no .gitignore touch.
        if request.no_log:
            path = None
            index = 1
        else:
            path = report_path(request.root, request.when)
            index = next_query_index(path)

        sections: list[str] = []
        results: list[str] = []
        outcomes: list[QueryOutcome] = []
        for statement in statements:
            section, result, outcome = self._run_one(index, statement, request.limit)
            sections.append(section)
            results.append(result)
            outcomes.append(outcome)
            index += 1

        if sections and not request.no_log:
            append_sections(path, sections)
            ensure_discovery_ignored(request.root)
        return DiscoveryResult(
            report_path = path,
            outcomes    = outcomes,
            sections    = sections,
            results     = results,
        )

    @staticmethod
    def _statements(request: DiscoveryRequest) -> list[str]:
        if request.statements_file is not None:
            return split_statements(request.statements_file.read_text(encoding="utf-8"))
        if request.sql is not None and request.sql.strip():
            return [request.sql.strip()]
        return []

    def _run_one(self, index: int, statement: str, limit: int) -> tuple[str, str, QueryOutcome]:
        label = query_label(index, statement)
        try:
            validated = validate_select_only(statement)
        except DiscoveryValidationError as error:
            result = render_result(error=str(error), limit=limit)
            section = render_section(index=index, sql=statement, error=str(error), limit=limit)
            return section, result, QueryOutcome(
                index=index, ok=False, label=label, error=str(error)
            )

        try:
            rows = self.gateway.read_only_fetch_all(validated)
        except Exception as error:
            if self.fatal_error(error):
                raise
            result = render_result(error=str(error), limit=limit)
            section = render_section(index=index, sql=validated, error=str(error), limit=limit)
            return section, result, QueryOutcome(
                index=index, ok=False, label=label, error=str(error)
            )

        result = render_result(rows=rows, limit=limit)
        section = render_section(index=index, sql=validated, rows=rows, limit=limit)
        return section, result, QueryOutcome(index=index, ok=True, label=label, row_count=len(rows))
