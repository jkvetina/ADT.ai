"""Rank a schema's remaining invalid objects by root cause (ADT #205).

A deployment that drops one object leaves a crater: the package that used it goes
invalid, the trigger and the view that used *that* follow, and ``INVALID OBJECTS``
prints the whole crater as one flat list. Every row looks equally like a place to
start, and all but a few are knock-ons.

This module separates the two. :func:`analyse` is pure — no gateway, no SQLite, no
console — so the ranking is unit-testable against fixture rows; :func:`rank_for_run`
is the thin entry point the runner calls, and owns the two scoped reads that feed
it. The three signals:

* the **error text** is the strongest signal, because Oracle usually names the
  culprit outright (``PLS-00905: object X is invalid``);
* the **stored source** covers the errors that name nothing (``ORA-00942``), read
  at the error's own line/position;
* the **dependency graph** (``config/dependencies.db``) connects invalids whose
  error text points at nobody, and is what turns "these are roots" into "check
  this one first".

Three classes of failure are deliberately kept apart because their fixes are
unrelated: ``SOURCE`` (the object's own code is broken), ``MISSING`` (something it
needs is not there) and ``GRANT`` (it is there, and this schema cannot see it).
Recompiling forever never fixes the last one.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from adt_ai.recompile.inventory import CompileError, ObjectError

# Cascade markers. Oracle emits one per statement it gave up on, so a single
# missing table can print three rows for one real fault: counting them as errors
# ranks the noisiest object first instead of the most upstream one. Matched
# after the `PL/SQL: ` prefix is stripped, so `PL/SQL: ORA-00942: ...` (a real
# error that merely shares the prefix) is never swallowed by them.
_CASCADE_MESSAGES = frozenset({
    "statement ignored",
    "sql statement ignored",
    "compilation unit analysis terminated",
})
_PLSQL_PREFIX_RE = re.compile(r"^PL/SQL:\s*", re.IGNORECASE)

# Culprit-naming errors. The captured group is the object Oracle blames.
_DERIVED_INVALID_RE = re.compile(
    r"PLS-00905:\s*object\s+([A-Za-z0-9_$#.\"]+)\s+is\s+invalid", re.IGNORECASE)
_DERIVED_SPEC_RE = re.compile(
    r"PLS-00304:\s*cannot compile body of\s+'([^']+)'", re.IGNORECASE)
_IDENTIFIER_RE = re.compile(
    r"PLS-00201:\s*identifier\s+'([^']+)'\s+must be declared", re.IGNORECASE)
_GRANT_OBJECT_RE = re.compile(
    r"PLS-00904:\s*insufficient privilege to access object\s+([A-Za-z0-9_$#.\"]+)",
    re.IGNORECASE)
_INVALID_IDENTIFIER_RE = re.compile(r'ORA-00904:\s*"([^"]+)"', re.IGNORECASE)

# Errors that name nothing.
_GRANT_PLAIN_RE = re.compile(r"\bORA-01031\b", re.IGNORECASE)
_MISSING_TABLE_RE = re.compile(r"\bORA-00942\b", re.IGNORECASE)
# PLS-00103 is Oracle's parse error: the object's own text does not compile, so
# there is nothing upstream to fix and it is a root by construction.
_SYNTAX_RE = re.compile(r"\bPLS-00103\b", re.IGNORECASE)

_IDENTIFIER_CHARS = re.compile(r"[A-Za-z0-9_$#.]")
_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z0-9_$#.]+")

# Verdict precedence when one object carries several classes of error. DERIVED
# wins because it means the work is elsewhere; GRANT beats MISSING because
# "you cannot see it" is a more specific answer than "it is not there".
_CAUSE_ORDER = ("DERIVED", "GRANT", "MISSING", "SOURCE", "UNKNOWN")


@dataclass(frozen=True)
class RootCause:
    object_type: str
    object_name: str
    # SOURCE | MISSING | GRANT | DERIVED | UNKNOWN
    cause: str
    # what to fix: the object Oracle named, the identifier read out of the source,
    # or "" when nothing in the evidence names one.
    culprit: str
    # invalid objects that clear transitively once this one compiles.
    blast: int
    # own errors, cascade rows excluded.
    errors: int

    @property
    def node(self) -> str:
        return f"{self.object_type}.{self.object_name}"


@dataclass(frozen=True)
class RootCauseReport:
    roots: list[RootCause] = field(default_factory=list)
    derived: list[RootCause] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.roots or self.derived)


def is_cascade_message(text: str) -> bool:
    """Is this error row a cascade — a restatement of a failure reported elsewhere?

    Public because the console reporter asks the same question: a row that is not
    worth counting is not worth printing either. It used to keep its own literal
    set, and the two drifted the moment one changed — `#212` added
    `Compilation unit analysis terminated` here and left the reporter's copy
    alone, so the message was discounted from the ranking and printed anyway
    (`#213`). One predicate, both callers.
    """
    return _PLSQL_PREFIX_RE.sub("", text or "").strip().lower() in _CASCADE_MESSAGES


def _node(object_type: str, object_name: str) -> str:
    return f"{object_type}.{object_name}"


def _strip_own_schema(name: str, schema: str) -> str:
    """Drop a leading ``SCHEMA.`` qualifier, and only that one.

    A dotted culprit is ambiguous: ``SYS.XMLTYPE_LIB`` is owner-qualified while
    ``UT_COVERAGE_HELPER.T_UNIT_LINE_CALLS`` is a package member. Stripping the
    head unconditionally would resolve ``SYS.XMLTYPE_LIB`` to a *local* object of
    the same name and report a separate root as a knock-on. Only the connected
    schema's own prefix is redundant, so only that one is removed.
    """
    head, sep, tail = name.partition(".")
    if sep and schema and head.upper() == schema.upper():
        return tail
    return name


def _matches_invalid(culprit: str, invalid_names: dict[str, str], schema: str) -> str:
    """Resolve a named culprit to an invalid object's node, or "" when it is not one.

    Matches on the *first* segment after the own-schema prefix is dropped: for
    ``PKG.MEMBER`` that is the package (the object to fix) and for a foreign
    ``OWNER.OBJ`` it is an owner, which correctly matches nothing local.
    """
    bare = _strip_own_schema(culprit, schema).partition(".")[0].strip('"').upper()
    return invalid_names.get(bare, "")


def _source_token_at(line_text: str, position: int | None) -> str:
    """The identifier the compiler was looking at, read out of the stored source.

    ``ORA-00942`` names no object and leaves no ``USER_DEPENDENCIES`` row (Oracle
    records a dependency only for a reference that resolved), so neither the error
    text nor the graph can say what is missing. The error's own position can: it
    points at the offending token. Falls back to the next identifier to the right
    when the position lands on whitespace or punctuation.
    """
    if not line_text:
        return ""
    index = max(0, (position or 1) - 1)
    if index >= len(line_text) or not _IDENTIFIER_CHARS.match(line_text[index]):
        match = _IDENTIFIER_TOKEN.search(line_text, index)
        return match.group(0).strip(".") if match else ""
    start = index
    while start > 0 and _IDENTIFIER_CHARS.match(line_text[start - 1]):
        start -= 1
    end = index
    while end < len(line_text) - 1 and _IDENTIFIER_CHARS.match(line_text[end + 1]):
        end += 1
    return line_text[start:end + 1].strip(".")


def source_lookups_needed(
    invalid: Sequence[ObjectError],
    error_details: Sequence[CompileError],
) -> list[tuple[str, str, int]]:
    """``(type, name, line)`` triples whose culprit can only come from the source.

    Only the errors that name nothing are listed, so the runner never pulls source
    for an object whose error already told us what to fix. Views are excluded:
    they have no ``USER_SOURCE`` rows, and their ``ORA-00904`` names the
    identifier anyway.
    """
    wanted = {(obj.object_type, obj.object_name) for obj in invalid}
    lookups: list[tuple[str, str, int]] = []
    seen: set[tuple[str, str, int]] = set()
    for detail in error_details:
        key = (detail.object_type, detail.object_name)
        if key not in wanted or detail.object_type == "VIEW":
            continue
        if not _MISSING_TABLE_RE.search(detail.text or ""):
            continue
        triple = (detail.object_type, detail.object_name, detail.line)
        if triple not in seen:
            seen.add(triple)
            lookups.append(triple)
    return lookups


def _classify(
    detail: CompileError,
    invalid_names: dict[str, str],
    schema: str,
    source_lines: dict[tuple[str, str, int], str],
) -> tuple[str, str, str]:
    """One error row → ``(cause, culprit, derived_from_node)``."""
    text = detail.text or ""

    for pattern in (_DERIVED_INVALID_RE, _DERIVED_SPEC_RE, _IDENTIFIER_RE):
        match = pattern.search(text)
        if match:
            culprit = _strip_own_schema(match.group(1).strip('"'), schema)
            upstream = _matches_invalid(match.group(1), invalid_names, schema)
            # An object Oracle names that is *itself* invalid is upstream work;
            # one that is not invalid is not there at all, which is the root.
            if upstream and upstream != _node(detail.object_type, detail.object_name):
                return ("DERIVED", culprit, upstream)
            return ("MISSING", culprit, "")

    match = _GRANT_OBJECT_RE.search(text)
    if match:
        return ("GRANT", _strip_own_schema(match.group(1).strip('"'), schema), "")
    if _GRANT_PLAIN_RE.search(text):
        return ("GRANT", "", "")

    match = _INVALID_IDENTIFIER_RE.search(text)
    if match:
        return ("MISSING", match.group(1), "")

    if _MISSING_TABLE_RE.search(text):
        line_text = source_lines.get(
            (detail.object_type, detail.object_name, detail.line), "")
        return ("MISSING", _source_token_at(line_text, detail.position), "")

    if _SYNTAX_RE.search(text):
        return ("SOURCE", "", "")

    return ("UNKNOWN", "", "")


def _blast_radius(node: str, dependents: dict[str, set[str]]) -> int:
    """Transitive dependents of ``node`` within the invalid set.

    Iterative and ``seen``-guarded: a package spec and body can depend on each
    other, and a real schema graph has cycles.
    """
    seen: set[str] = set()
    queue = list(dependents.get(node, ()))
    while queue:
        current = queue.pop()
        if current in seen or current == node:
            continue
        seen.add(current)
        queue.extend(dependents.get(current, ()))
    return len(seen)


def analyse(
    invalid: Sequence[ObjectError],
    error_details: Sequence[CompileError],
    schema: str = "",
    dependents: dict[str, Sequence[str]] | None = None,
    source_lines: dict[tuple[str, str, int], str] | None = None,
) -> RootCauseReport:
    """Split the invalid set into ranked roots and the knock-ons they explain.

    ``dependents`` maps an invalid object's ``TYPE.NAME`` node to the invalid
    objects that depend on it (the caller reads it from ``config/dependencies.db``;
    an absent mirror simply means the ranking runs on error evidence alone).
    ``source_lines`` maps ``(type, name, line)`` to that source line, for the
    errors that name nothing.
    """
    source_lines = source_lines or {}
    # Name → node, for resolving a culprit Oracle named to an invalid object. An
    # object name can carry two types (spec and body); the spec is the one worth
    # pointing at, and it sorts first, so the first write wins.
    invalid_names: dict[str, str] = {}
    for obj in sorted(invalid, key=lambda o: (o.object_name, o.object_type)):
        invalid_names.setdefault(obj.object_name.upper(), _node(obj.object_type, obj.object_name))

    details_by_object: dict[tuple[str, str], list[CompileError]] = {}
    for detail in error_details:
        details_by_object.setdefault((detail.object_type, detail.object_name), []).append(detail)

    edges: dict[str, set[str]] = {
        node: set(targets) for node, targets in (dependents or {}).items()
    }

    verdicts: dict[tuple[str, str], tuple[str, str, int]] = {}
    for obj in invalid:
        key = (obj.object_type, obj.object_name)
        own = [d for d in details_by_object.get(key, []) if not is_cascade_message(d.text or "")]
        classified = [
            _classify(detail, invalid_names, schema, source_lines) for detail in own
        ]
        cause = "UNKNOWN"
        culprit = ""
        for candidate in _CAUSE_ORDER:
            matching = [item for item in classified if item[0] == candidate]
            if matching:
                cause, culprit = candidate, matching[0][1]
                break
        # An error naming an invalid object is an edge the graph may not carry —
        # a fresh compile failure predates the mirror's last refresh.
        for item_cause, _culprit, upstream in classified:
            if item_cause == "DERIVED" and upstream:
                edges.setdefault(upstream, set()).add(_node(*key))
        verdicts[key] = (cause, culprit, len(own))

    roots: list[RootCause] = []
    derived: list[RootCause] = []
    for obj in invalid:
        key = (obj.object_type, obj.object_name)
        cause, culprit, errors = verdicts[key]
        entry = RootCause(
            object_type = obj.object_type,
            object_name = obj.object_name,
            cause       = cause,
            culprit     = culprit,
            blast       = _blast_radius(_node(*key), edges),
            errors      = errors,
        )
        (derived if cause == "DERIVED" else roots).append(entry)

    # Most downstream damage first, then the noisiest, then stable by identity.
    roots.sort(key=lambda c: (-c.blast, -c.errors, c.object_type, c.object_name))
    derived.sort(key=lambda c: (c.object_type, c.object_name))
    return RootCauseReport(roots=roots, derived=derived)


def rank_for_run(
    discovery: Any,
    schema: str,
    invalid: Sequence[ObjectError],
    error_details: Sequence[CompileError],
    dependents_for: Callable[[list[str]], dict[str, Sequence[str]]],
) -> RootCauseReport | None:
    """Rank one recompile run's leftovers, doing the two reads :func:`analyse` needs.

    Both reads are scoped to the damage: source only for the errors that name
    nothing, graph edges only for the objects still invalid. A run that ends clean
    returns ``None`` and pays for neither.
    """
    if not invalid:
        return None
    source_lines = discovery.error_source_lines(source_lookups_needed(invalid, error_details))
    nodes = [_node(obj.object_type, obj.object_name) for obj in invalid]
    return analyse(
        invalid,
        error_details,
        schema       = schema,
        dependents   = dependents_for(nodes),
        source_lines = source_lines,
    )
