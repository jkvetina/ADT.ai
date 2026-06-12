"""Orchestration for the discovery module.

Ties the discovery pieces into one read-only pipeline: gather statements (one
inline ``-sql`` or a ``;``-split ``-file``), validate each as a single SELECT
(safety layer 1), run it under a READ ONLY transaction (safety layer 2), render
the result as a Markdown section, and append the run to a timestamped report.

Validation and database failures are captured into the report as error sections
rather than raised — a discovery run reports every query it was asked to run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from adt_ai.db import QueryGateway
from adt_ai.discovery.render import DEFAULT_ROW_LIMIT, query_label, render_result, render_section
from adt_ai.discovery.report import (
    append_sections,
    ensure_discovery_ignored,
    next_query_index,
    report_path,
)
from adt_ai.discovery.validator import DiscoveryValidationError, validate_select_only

RESULT_BLOCK_START = "/*"
RESULT_BLOCK_END   = "*/"

_RESULT_BLOCK_RE = re.compile(r"\n/\*\n.*?\*/", re.DOTALL)


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

    ``/* … */`` result blocks written back by ``-file`` mode are stripped
    before splitting so re-runs see clean SQL.
    """
    clean = _RESULT_BLOCK_RE.sub("", text)
    return [statement.strip() for statement in clean.split(";") if statement.strip()]


class DiscoveryRunner:
    def __init__(self, gateway: QueryGateway) -> None:
        self.gateway = gateway

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
            return section, result, QueryOutcome(index=index, ok=False, label=label, error=str(error))

        try:
            rows = self.gateway.read_only_fetch_all(validated)
        except Exception as error:
            result = render_result(error=str(error), limit=limit)
            section = render_section(index=index, sql=validated, error=str(error), limit=limit)
            return section, result, QueryOutcome(index=index, ok=False, label=label, error=str(error))

        result = render_result(rows=rows, limit=limit)
        section = render_section(index=index, sql=validated, rows=rows, limit=limit)
        return section, result, QueryOutcome(index=index, ok=True, label=label, row_count=len(rows))
