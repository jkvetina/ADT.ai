from __future__ import annotations

from typing import Any

DEFAULT_ROW_LIMIT = 200


def render_section(
    *,
    index: int,
    sql: str,
    rows: list[dict[str, Any]] | None = None,
    error: str | None = None,
    limit: int = DEFAULT_ROW_LIMIT,
) -> str:
    """Render one discovery query as a Markdown report section.

    Layout: a ``## `` header (from a leading ``-- comment`` or auto ``Query N``),
    the SQL in a fenced ``sql`` block, then the body:

    - ``error`` set → the message as a ``>`` blockquote;
    - empty ``rows`` → ``_(0 rows)_``;
    - otherwise → a padded GitHub Markdown table, truncated to ``limit`` rows
      with a ``_(truncated: N more rows)_`` note when more rows exist.
    """
    parts = [_header(index, sql), "", "```sql", sql.strip(), "```", ""]
    parts.append(render_result(rows=rows, error=error, limit=limit))
    return "\n".join(parts) + "\n"


def render_result(
    *,
    rows: list[dict[str, Any]] | None = None,
    error: str | None = None,
    limit: int = DEFAULT_ROW_LIMIT,
) -> str:
    if error is not None:
        return _blockquote(error)
    if not rows:
        return "_(0 rows)_"
    return _table(rows, limit)


def query_label(index: int, sql: str) -> str:
    """Return the display label for a query, leading ``-- comment`` or ``Query N``."""
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("--"):
            text = stripped[2:].strip()
            if text:
                return text
        break
    return f"Query {index}"


def _header(index: int, sql: str) -> str:
    return f"## {query_label(index, sql)}"


def _blockquote(message: str) -> str:
    return "\n".join(f"> {line}" for line in message.splitlines())


def _cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|")


def _table(rows: list[dict[str, Any]], limit: int) -> str:
    columns = list(rows[0].keys())
    display = rows[:limit]
    matrix = [[_cell(row.get(column)) for column in columns] for row in display]
    widths = [
        max(len(columns[i]), *(len(row[i]) for row in matrix)) if matrix else len(columns[i])
        for i in range(len(columns))
    ]

    def line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)) + " |"

    lines = [
        line(columns),
        "| " + " | ".join("-" * width for width in widths) + " |",
    ]
    lines.extend(line(row) for row in matrix)

    remainder = len(rows) - len(display)
    if remainder > 0:
        lines.append("")
        lines.append(f"_(truncated: {remainder} more rows)_")
    return "\n".join(lines)
