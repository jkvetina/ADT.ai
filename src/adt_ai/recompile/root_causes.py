"""Rank a schema's remaining invalid objects by root cause (ADT #205).

A deployment that drops one object leaves a crater: the package that used it goes
invalid, the trigger and the view that used *that* follow, and ``INVALID OBJECTS``
prints the whole crater as one flat list. Every row looks equally like a place to
start, and all but a few are knock-ons.

This module separates the two. :func:`analyse` is pure, no gateway, no SQLite, no
console, so the ranking is unit-testable against fixture rows; :func:`rank_for_run`
is the thin entry point the runner calls, and owns the two scoped reads that feed
it. The three signals:

* the **error text** is the strongest signal, because Oracle usually names the
  culprit outright (``PLS-00905: object X is invalid``);
* the **stored source** covers the errors that name nothing (``ORA-00942``), read
  at the error's own line/position;
* the **dependency graph** (``config/internal/dependencies.db``) connects invalids whose
  error text points at nobody, and is what turns "these are roots" into "check
  this one first".

Three classes of failure are deliberately kept apart because their fixes are
unrelated: ``SOURCE`` (the object's own code is broken), ``MISSING`` (something it
needs is not there) and ``GRANT`` (it is there, and this schema cannot see it).
Recompiling forever never fixes the last one.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
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

# The verdict one member of a mutual invalidation is promoted to (#670). It is
# deliberately not a value `_classify` can reach: every member of such a loop
# reads as DERIVED on its own error row, and only the closed loop says
# otherwise. See `_blame_cycles` for why the promotion has to happen at all.
CYCLE = "CYCLE"


@dataclass(frozen=True)
class RootCause:
    object_type: str
    object_name: str
    # SOURCE | MISSING | GRANT | DERIVED | CYCLE | UNKNOWN
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
    """Is this error row a cascade, a restatement of a failure reported elsewhere?

    Public because the console reporter asks the same question: a row that is not
    worth counting is not worth printing either. It used to keep its own literal
    set, and the two drifted the moment one changed, `#212` added
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


def _reachable(node: str, dependents: dict[str, set[str]]) -> dict[str, int]:
    """Every node reachable from ``node``, mapped to how many hops away it is.

    Breadth-first and ``distances``-guarded: a package spec and body can depend
    on each other, and a real schema graph has cycles. ``node`` itself appears
    only when a cycle leads back to it, which is a fact the callers use.
    """
    distances: dict[str, int] = {}
    frontier = [node]
    hops = 0
    while frontier:
        hops += 1
        further: list[str] = []
        for current in frontier:
            for target in dependents.get(current, ()):
                if target in distances:
                    continue
                distances[target] = hops
                further.append(target)
        frontier = further
    return distances


def _blast_counts(
    universe: Sequence[str],
    root_nodes: set[str],
    dependents: dict[str, set[str]],
) -> dict[str, int]:
    """How many invalid objects each root is actually the one to fix for (#670).

    The walk this replaces counted a root's whole transitive reach, independently
    per root, so an object downstream of two unrelated roots was counted under
    both and two `BLAST` figures of 1 described one knock-on. Neither figure was
    true: fixing either root on its own leaves that object invalid, because the
    other one still breaks it.

    So every knock-on is OWNED by exactly one root, the nearest one that reaches
    it, ties broken by ``TYPE.NAME``, which is the identity order the table
    already sorts on. A root then counts a node when it owns it **or** when it
    reaches the root that owns it. That second clause is what keeps a chain
    honest: in ``A -> B -> C`` with A and B both roots, compiling A really does
    clear both objects below it, and B really does clear C, while two
    independent roots can no longer both claim the same downstream object.
    """
    reach = {root: _reachable(root, dependents) for root in root_nodes}
    owner: dict[str, str] = {}
    for node in universe:
        # A root never owns itself, or a two-object cycle would report a blast of
        # zero on both members where each genuinely clears the other.
        candidates = [
            (distances[node], root)
            for root, distances in reach.items()
            if root != node and node in distances
        ]
        if candidates:
            owner[node] = min(candidates)[1]
    return {
        root: sum(
            1
            for node in universe
            if node != root
            and node in reach[root]
            and (owner[node] == root or owner[node] in reach[root])
        )
        for root in root_nodes
    }


def _blame_cycles(blames: dict[str, str]) -> list[list[str]]:
    """The closed loops in "who does each knock-on blame" (#670).

    **A mutual invalidation classifies every member as DERIVED, so the report
    ends up with no roots at all and the console prints no `ROOT CAUSES:`
    section.** Two package bodies that each name the other in a `PLS-00905` are
    the smallest case, and it is not exotic: it is what a half-applied
    deployment leaves behind. Each object's own evidence is correct, the work
    really is elsewhere, and following "elsewhere" walks in a circle forever.

    Each node blames at most one other (the culprit its winning verdict named),
    so this is a functional graph and a loop is the only shape a walk can close
    into. Every node is walked once across all starts, hence the ``seen`` guard,
    and a walk that runs into a previous walk's territory has nothing new to
    report because that walk already recorded whatever loop it found.
    """
    seen: set[str] = set()
    cycles: list[list[str]] = []
    for start in sorted(blames):
        if start in seen:
            continue
        path: list[str] = []
        position: dict[str, int] = {}
        node = start
        while node in blames and node not in seen:
            seen.add(node)
            position[node] = len(path)
            path.append(node)
            node = blames[node]
        if node in position:
            cycles.append(path[position[node]:])
    return cycles


def analyse(
    invalid: Sequence[ObjectError],
    error_details: Sequence[CompileError],
    schema: str = "",
    dependents: Mapping[str, Sequence[str]] | None = None,
    source_lines: dict[tuple[str, str, int], str] | None = None,
    schema_invalid: Sequence[tuple[str, str]] | None = None,
) -> RootCauseReport:
    """Split the invalid set into ranked roots and the knock-ons they explain.

    ``dependents`` maps an invalid object's ``TYPE.NAME`` node to the invalid
    objects that depend on it (the caller reads it from ``config/internal/dependencies.db``;
    an absent mirror simply means the ranking runs on error evidence alone).
    ``source_lines`` maps ``(type, name, line)`` to that source line, for the
    errors that name nothing.

    ``schema_invalid`` is the ``(type, name)`` of **every** invalid object in the
    schema, which is a different set from ``invalid`` the moment ``-type`` or
    ``-name`` narrows the run (#670). Only the classification reads it, and only
    to answer "is the object Oracle blamed itself invalid": against the scoped
    list alone the answer for anything outside the scope was no, so
    ``adtai recompile -type "PACKAGE BODY"`` reported a body whose own spec had
    not compiled as ``MISSING`` and told the reader to restore an object that is
    right there. Omit it and the scoped list stands in, which is the old
    behaviour and the honest fallback for a caller that has only that list.
    """
    source_lines = source_lines or {}
    # Name → node, for resolving a culprit Oracle named to an invalid object. An
    # object name can carry two types (spec and body); the spec is the one worth
    # pointing at, and it sorts first, so the first write wins.
    index_source = (
        list(schema_invalid)
        if schema_invalid is not None
        else [(obj.object_type, obj.object_name) for obj in invalid]
    )
    invalid_names: dict[str, str] = {}
    for object_type, object_name in sorted(index_source, key=lambda pair: (pair[1], pair[0])):
        invalid_names.setdefault(object_name.upper(), _node(object_type, object_name))

    details_by_object: dict[tuple[str, str], list[CompileError]] = {}
    for detail in error_details:
        details_by_object.setdefault((detail.object_type, detail.object_name), []).append(detail)

    edges: dict[str, set[str]] = {
        node: set(targets) for node, targets in (dependents or {}).items()
    }

    verdicts: dict[tuple[str, str], tuple[str, str, int]] = {}
    # node → the node its winning DERIVED verdict blames, which is the walk
    # `_blame_cycles` follows. One entry per knock-on, never per error row.
    blames: dict[str, str] = {}
    # The way back from a node string to the `verdicts` key, so the cycle pass
    # can rewrite a verdict without re-parsing `TYPE.NAME` (an object name may
    # itself contain a dot, so parsing it back is not safe).
    key_of: dict[str, tuple[str, str]] = {}
    for obj in invalid:
        key = (obj.object_type, obj.object_name)
        key_of[_node(*key)] = key
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
                if candidate == "DERIVED":
                    blames[_node(*key)] = matching[0][2]
                break
        # An error naming an invalid object is an edge the graph may not carry,
        # a fresh compile failure predates the mirror's last refresh.
        for item_cause, _culprit, upstream in classified:
            if item_cause == "DERIVED" and upstream:
                edges.setdefault(upstream, set()).add(_node(*key))
        verdicts[key] = (cause, culprit, len(own))

    # A closed blame loop leaves the report with no roots at all, so one member
    # of each loop is promoted: the noisiest, ties by identity. Fixing it is what
    # breaks the circle, and the rest of the loop clears behind it.
    for cycle in _blame_cycles(blames):
        promoted = min(cycle, key=lambda node: (-verdicts[key_of[node]][2], node))
        key = key_of[promoted]
        _cause, culprit, errors = verdicts[key]
        verdicts[key] = (CYCLE, culprit, errors)

    universe = [_node(obj.object_type, obj.object_name) for obj in invalid]
    root_nodes = {
        _node(obj.object_type, obj.object_name)
        for obj in invalid
        if verdicts[(obj.object_type, obj.object_name)][0] != "DERIVED"
    }
    blast = _blast_counts(universe, root_nodes, edges)

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
            blast       = blast.get(_node(*key), 0),
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
    dependents_for: Callable[[list[str]], Mapping[str, Sequence[str]]],
) -> RootCauseReport | None:
    """Rank one recompile run's leftovers, doing the three reads :func:`analyse` needs.

    Two of them are scoped to the damage: source only for the errors that name
    nothing, graph edges only for the objects still invalid. The third is
    deliberately not scoped, one whole-schema name read so a culprit outside a
    ``-type``/``-name`` run can still be recognised as invalid rather than
    reported as missing (#670); compilation stays scoped, only the classification
    sees the wider list. A run that ends clean returns ``None`` and pays for none
    of the three.
    """
    if not invalid:
        return None
    source_lines = discovery.error_source_lines(source_lookups_needed(invalid, error_details))
    nodes = [_node(obj.object_type, obj.object_name) for obj in invalid]
    return analyse(
        invalid,
        error_details,
        schema         = schema,
        dependents     = dependents_for(nodes),
        source_lines   = source_lines,
        schema_invalid = [
            (obj.object_type, obj.object_name) for obj in discovery.invalid_object_names()
        ],
    )
