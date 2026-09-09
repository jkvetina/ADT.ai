"""File shapes for the four 26ai types, which differ only in how they replay (`#738`).

`dictionary_ddl.py` has already assembled a statement spelled the way `GET_DDL`
spells one, and the common pass has already lowercased its identifiers and dropped
the owner. What is left is per-type and measured on the live 26ai fixture (Oracle AI
Database 23.26.3.0.0, SANDBOX, 2026-09-09):

* `CREATE OR REPLACE` is accepted for `MLE MODULE`, `MLE ENV` and `PROPERTY GRAPH`,
  so those three replay by replacing and need no guard;
* `CREATE OR REPLACE DOMAIN` answers `ORA-00922: missing or invalid option`, so a
  domain takes the drop-before-create guard an assertion and a materialized view
  already take;
* an MLE module's body is JavaScript, so it ends on `/` like a PL/SQL body and its
  source is never touched. The other three are plain SQL and end on `;`, because
  SQLcl has already run the statement by the time a `/` would re-run the buffer.
"""

from __future__ import annotations

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _drop_create_wrap,
    _ensure_sql_terminator,
    _ensure_statement_semicolon,
    _trim_trailing_blank_lines,
    qualified,
)


def normalize_domain(lines: list[str], context: NormalizationContext) -> list[str]:
    """A domain cannot be replaced in place, so the file drops it first."""
    kept = _ensure_statement_semicolon(_trim_trailing_blank_lines(list(lines)))
    return _drop_create_wrap(
        kept,
        f"DROP DOMAIN {qualified(context.object_name.upper(), context)}",
        slash = False,
    )


def normalize_mle_environment(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    """Plain SQL that replaces in place: a semicolon and no trailing slash."""
    return _ensure_statement_semicolon(_trim_trailing_blank_lines(list(lines))) + [""]


def normalize_property_graph(
    lines: list[str],
    context: NormalizationContext,
) -> list[str]:
    """Same shape as an environment; the graph body is a clause list, not a program."""
    return _ensure_statement_semicolon(_trim_trailing_blank_lines(list(lines))) + [""]


def normalize_mle_module(lines: list[str], context: NormalizationContext) -> list[str]:
    """A JavaScript body, terminated the way a PL/SQL body is.

    The source between `AS` and the terminator is the author's own program, and the
    body-preserving pass upstream is what keeps it byte for byte; all this adds is
    the `/` SQLcl needs to know the statement ended, since a JavaScript body is full
    of the semicolons that would otherwise end it early.
    """
    return _ensure_sql_terminator(_trim_trailing_blank_lines(list(lines)))
