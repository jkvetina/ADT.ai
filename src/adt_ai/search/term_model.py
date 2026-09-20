"""What `search TERM` asks and answers, and the one rule every layer matches by (ADT #895).

A TERM is found by a case-insensitive substring test and nothing else: no
wildcard, no pattern, so `%` and `_` are literal. Every layer decides its lines
through `line_matches`, whatever it pushed down to SQLite or to git first, so
the five layers cannot disagree about what a hit is.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adt_ai.flow.model import FlowEdge
from adt_ai.shared.commit_cache import DEFAULT_COMMITS_TEMPLATE

#: The layers, in the order the report lists them and `-layer` names them.
LAYERS: tuple[str, ...] = ("APEX", "STATIC", "DB", "GIT", "FILES")

#: A file over this size is listed once rather than line by line. A line of a
#: bundled library says nothing a reader can use, and a thousand of them bury
#: every other hit on the screen.
BIG_FILE_BYTES = 512 * 1024

#: The flag a definition-shaped line carries.
DEFINED = "DEFINED"

_NAME = r"([A-Za-z_$][\w$#.]*)"

#: Definition shapes, JavaScript first, then PL/SQL. The captured group is the
#: name being defined; the line is flagged only when that name contains TERM,
#: so a call `calc_total(` inside another function's body stays a plain hit.
_DEFINITIONS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"\bfunction\s+{_NAME}\s*\(",
        rf"{_NAME}\s*=\s*function\b",
        rf"{_NAME}\s*:\s*function\b",
        rf"{_NAME}\s*=\s*(?:async\s*)?\([^)]*\)\s*=>",
        rf"\b(?:const|let|var)\s+{_NAME}\s*=.*=>",
        rf"\b(?:procedure|function)\s+{_NAME}",
    )
)


@dataclass(frozen=True)
class Hit:
    """One row of `HITS`: where TERM was found, and the line it was found on.

    `db_object` is `(owner, type, name)` on a DB hit only, what `USES / USED
    BY` looks the object up by. `line` is `None` for a hit that names a whole
    file or a commit.
    """

    source: str
    component: str = ""
    prop: str = ""
    app: int | None = None
    page: int | None = None
    line: int | None = None
    flag: str = ""
    excerpt: str = ""
    db_object: tuple[str, str, str] | None = None


@dataclass(frozen=True)
class Relation:
    """One `USES / USED BY` row: `obj` uses, or is used by, `kind` `name`."""

    obj: str
    relation: str
    kind: str
    name: str


@dataclass(frozen=True)
class TermRequest:
    """A TERM search, already parsed: the CLI resolves every flag into data.

    `app_ids` and `app_ranges` narrow APEX and STATIC, `page_ids` and
    `page_ranges` narrow APEX, and `branch` picks the GIT store. `owners` are
    the schemas DB reads, the ones `-schema` names or else the connection's
    default ones, and `config` is the project configuration whose
    `path_objects` and `object_types` say where `export_db` wrote them (ADT
    #904). Ranges are `(min, max)`, `max` `None` for an open `MIN+`.
    """

    root: Path
    term: str
    layers: tuple[str, ...] = LAYERS
    app_ids: tuple[int, ...] = ()
    app_ranges: tuple[tuple[int, int | None], ...] = ()
    page_ids: tuple[int, ...] = ()
    page_ranges: tuple[tuple[int, int | None], ...] = ()
    owners: tuple[str, ...] = ()
    branch: str | None = None
    cache_file_template: str = DEFAULT_COMMITS_TEMPLATE
    config: Mapping[str, Any] = field(default_factory=dict)

    @property
    def narrows_apps(self) -> bool:
        return bool(self.app_ids or self.app_ranges)

    @property
    def narrows_pages(self) -> bool:
        return bool(self.page_ids or self.page_ranges)


@dataclass
class TermResult:
    """Everything the report prints, plus which requested layers were read.

    `not_searched` holds `(layers, reason)` pairs, `layers` being the label the
    row opens on. A layer is `searched` once any part of it was read, so a run
    naming one refreshed and one unrefreshed application searched APEX and
    still names the other one.
    """

    hits: list[Hit] = field(default_factory=list)
    searched: set[str] = field(default_factory=set)
    not_searched: list[tuple[str, str]] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    links: list[FlowEdge] = field(default_factory=list)


def parse_layers(values: list[str] | None) -> tuple[str, ...]:
    """`-layer` values as layer names in report order; all five when none are given.

    Case-insensitive like every keyword vocabulary on the command line. An
    unknown value is a `ValueError` whose message is the refusal the dispatcher
    prints before the banner.
    """
    if not values:
        return LAYERS
    chosen = {value.upper() for value in values}
    unknown = sorted(chosen - set(LAYERS))
    if unknown:
        vocabulary = ", ".join(LAYERS[:-1]) + f" or {LAYERS[-1]}"
        raise ValueError(f"-layer {' '.join(unknown)}: a layer is {vocabulary}")
    return tuple(layer for layer in LAYERS if layer in chosen)


def line_matches(text: str, term: str) -> bool:
    return term.lower() in text.lower()


def sql_term(term: str) -> str | None:
    """The term SQLite can pre-filter on, or `None` when it cannot fold it."""
    return term.lower() if term.isascii() else None


def excerpt(line: str) -> str:
    """The line as one row cell: stripped, every run of whitespace one space.

    A tab or a stray carriage return inside a cell would move every column to
    its right, so they go with the indentation.
    """
    return " ".join(line.split())


def definition_flag(line: str, term: str) -> str:
    """`DEFINED` when the line defines something whose name contains TERM."""
    for pattern in _DEFINITIONS:
        for match in pattern.finditer(line):
            if line_matches(match.group(1), term):
                return DEFINED
    return ""


def is_big(name: str, size: int) -> bool:
    """A minified or oversized file, listed once with no line and no excerpt."""
    return ".min." in name.lower() or size > BIG_FILE_BYTES


def line_hits(text: str, term: str) -> list[tuple[int, str, str]]:
    """`(line, flag, excerpt)` for every line of `text` containing TERM, 1-based.

    Split on `\n` alone, never `splitlines()`: that also breaks on a form feed
    or a Unicode line separator, and a line number an editor disagrees with
    sends the reader to the wrong line. A `\r` before the break goes with the
    excerpt's whitespace.
    """
    return [
        (number, definition_flag(line, term), excerpt(line))
        for number, line in enumerate(text.split("\n"), start=1)
        if line_matches(line, term)
    ]


def in_selection(
    value: int | None,
    ids: tuple[int, ...],
    ranges: tuple[tuple[int, int | None], ...],
) -> bool:
    if value is None:
        return False
    return value in ids or any(
        value >= low and (high is None or value <= high) for low, high in ranges
    )


__all__ = [name for name in globals() if not name.startswith("_")]
