"""Quoting for values that go into a generated SQLcl command line (ADT #653).

SQLcl tokenizes a command line itself, so a path carrying a space truncates in
silence unless it is quoted, and there is no escape for a double quote INSIDE a
SQLcl double-quoted token: the line simply ends early and the command runs
against something else, or not at all. So the rule here is quote what can be
quoted and refuse, by name, what cannot.

Refusing is the honest half. A value that cannot be represented has no working
spelling to fall back to, and the alternative (emitting a line SQLcl will
misread) is the failure this module exists to stop, one that shows up as a
wrong artifact rather than as an error.

`&` needs nothing: every generated script sets `SET DEFINE OFF`.
"""

from __future__ import annotations

from pathlib import Path


class SqlclQuotingError(ValueError):
    """A value cannot be represented inside a SQLcl command line."""


def quote_sqlcl_argument(value: str | Path, *, role: str) -> str:
    """`value` as a double-quoted SQLcl token, or a refusal naming `role`.

    `role` is what the reader calls the value ("wallet path", "staging folder"),
    so the message says which of a command's arguments is the problem.
    """
    text = str(value)
    reject_unquotable(text, role=role)
    return f'"{text}"'


def reject_unquotable(value: str, *, role: str) -> None:
    """Raise when `value` holds a character SQLcl's quoting cannot carry."""
    if '"' in value:
        raise SqlclQuotingError(
            f'The {role} contains a double quote, which SQLcl cannot quote: {value}\n'
            "Rename it, or move it somewhere without one."
        )
    if "\n" in value or "\r" in value:
        raise SqlclQuotingError(
            f"The {role} contains a line break, which SQLcl cannot quote: {value!r}\n"
            "Rename it, or move it somewhere without one."
        )
