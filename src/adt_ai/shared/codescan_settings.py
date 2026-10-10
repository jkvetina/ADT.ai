"""The `config.yaml` keys codescan reads, read in one place (ADT #1026, #1025, #1024, #1023).

Three modules ask: `validate` reads `codescan_paths` and `codescan_fail_on` to
know which trees to scan and what fails the run, `patch` reads
`patch_codescan` and `codescan_fail_on` to know whether `-create` scans the
files it carries and what that scan may stop, both read `codescan_ignore` for
the findings a scan suppresses and `codescan_rules` for the rules it reports,
and `doctor` reads `codescan_fail_on` and
`patch_codescan` to know whether the SQLcl floor applies to a project at all.
`doctor` ships in every release and `validate` does not, so the reader lives
here rather than in any of them, and they cannot come to disagree about what a
value means.

A value neither key knows is refused by name rather than read as a default: a
gate that quietly fell back to `new` on a typo of `any` would pass runs the
project meant to fail.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The store under `config/internal/` that holds each tree's baseline.
CODESCAN_STORE = "codescan.db"

#: The export_db tree(s) `path_objects` resolves to.
DATABASE = "database"
#: Every `apexlang/` tree at the export shape `path_apex` and `apex_path_app` give.
APEX = "apex"
CODESCAN_PATHS = (DATABASE, APEX)

#: Only a finding the baseline does not hold fails the run.
FAIL_ON_NEW = "new"
#: Every finding fails the run.
FAIL_ON_ANY = "any"
#: Nothing a scan finds fails the run; it is a report.
FAIL_ON_NONE = "none"
FAIL_ON_VALUES = (FAIL_ON_NEW, FAIL_ON_ANY, FAIL_ON_NONE)

#: `patch -create` scans nothing.
PATCH_CODESCAN_OFF = "off"
#: `patch -create` scans the files it carries, lists what it found, and builds.
PATCH_CODESCAN_WARN = "warn"
#: `patch -create` scans the files it carries and writes nothing on a violation.
PATCH_CODESCAN_BLOCK = "block"
PATCH_CODESCAN_VALUES = (PATCH_CODESCAN_OFF, PATCH_CODESCAN_WARN, PATCH_CODESCAN_BLOCK)

#: What `config/config.yaml` ships, and so what a project without the keys gets.
DEFAULT_PATHS = CODESCAN_PATHS
DEFAULT_FAIL_ON = FAIL_ON_NEW
DEFAULT_PATCH_CODESCAN = PATCH_CODESCAN_OFF


class CodescanSettingError(ValueError):
    """A codescan key holding a value nothing here can act on."""


def codescan_paths(config: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The kinds of tree a bare `validate -codescan` scans, in the order written.

    A bare word is one kind, so `codescan_paths: database` reads as it looks. A
    kind named twice is scanned once, and an empty list scans nothing, which
    `validate` refuses the way it refuses a bare run with no export.
    """
    raw = (config or {}).get("codescan_paths")
    if raw is None:
        return DEFAULT_PATHS
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list | tuple):
        raise CodescanSettingError(
            "codescan_paths IS NOT A LIST\n\n"
            f"codescan_paths lists the trees to scan: {', '.join(CODESCAN_PATHS)}."
        )
    kinds: list[str] = []
    for value in values:
        kind = str(value).strip().lower()
        if kind not in CODESCAN_PATHS:
            raise CodescanSettingError(
                f"UNKNOWN codescan_paths '{value}'\n\n"
                f"codescan_paths lists the trees to scan: {', '.join(CODESCAN_PATHS)}."
            )
        if kind not in kinds:
            kinds.append(kind)
    return tuple(kinds)


def codescan_fail_on(config: Mapping[str, Any] | None) -> str:
    """Which findings fail a `validate -codescan` run: `new`, `any` or `none`."""
    raw = (config or {}).get("codescan_fail_on")
    if raw is None:
        return DEFAULT_FAIL_ON
    value = str(raw).strip().lower()
    if value not in FAIL_ON_VALUES:
        raise CodescanSettingError(
            f"UNKNOWN codescan_fail_on '{raw}'\n\n"
            "codescan_fail_on takes new, any or none."
        )
    return value


@dataclass(frozen=True)
class CodescanIgnore:
    """One `codescan_ignore` entry: a rule code, why, and how narrowly (ADT #1024).

    ``file`` is the path from the project root, ``None`` for every file;
    ``component`` is `(type, id)`, ``None`` for the whole file.
    """

    code      : str
    reason    : str
    file      : str | None = None
    component : tuple[str, str] | None = None


#: The keys an entry may carry. Anything else is refused: a misspelled `file`
#: read as absent would widen the ignore to every file in every tree.
_IGNORE_KEYS = ("code", "reason", "file", "component")

#: Two lines, each inside 80 columns once the error screen indents it.
_IGNORE_SHAPE = (
    "Each entry needs a code and a reason; file narrows it to a path from the\n"
    "project root, component to {type, id} of an APEX component in that file."
)


def codescan_ignore(config: Mapping[str, Any] | None) -> tuple[CodescanIgnore, ...]:
    """The findings a codescan run suppresses, each with the reason it gave.

    Every entry is checked before anything scans, so a missing code or reason
    is refused by its number in the list. A component with no file is refused
    here as well: SQLcl 26.3 refuses it itself, but only once the run has
    started, and writes no report (`ignored_component_no_file` capture).
    """
    raw = (config or {}).get("codescan_ignore")
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple):
        raise CodescanSettingError(f"codescan_ignore IS NOT A LIST\n\n{_IGNORE_SHAPE}")
    return tuple(_ignore_entry(number, entry) for number, entry in enumerate(raw, start=1))


def _ignore_entry(number: int, entry: Any) -> CodescanIgnore:
    def refuse(problem: str) -> CodescanSettingError:
        return CodescanSettingError(f"codescan_ignore ENTRY {number} {problem}\n\n{_IGNORE_SHAPE}")

    if not isinstance(entry, Mapping):
        raise refuse("IS NOT A MAPPING")
    for key in entry:
        if key not in _IGNORE_KEYS:
            raise refuse(f"HAS UNKNOWN KEY '{key}'")
    code = _text(entry.get("code"))
    if not code:
        raise refuse("NAMES NO code")
    reason = _text(entry.get("reason"))
    if not reason:
        raise refuse("GIVES NO reason")
    file = None
    if "file" in entry:
        file = _text(entry.get("file"))
        if not file:
            raise refuse("file IS EMPTY")
    component = None
    if "component" in entry:
        if file is None:
            raise refuse("NAMES A component BUT NO file")
        raw = entry.get("component")
        kind = _text(raw.get("type")) if isinstance(raw, Mapping) else ""
        ident = _text(raw.get("id")) if isinstance(raw, Mapping) else ""
        if not kind or not ident:
            raise refuse("component NEEDS type AND id")
        component = (kind, ident)
    return CodescanIgnore(code, reason, file, component)


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


#: A rule code as SQLcl lists it, `G-7120` or `APEX-035`, or a prefix of one
#: closed by `*`, `G-7*` or `APEX-*`; `*` alone is every rule.
_RULE_RE = re.compile(r"[A-Z0-9-]*\*?")

#: Two lines, each inside 80 columns once the error screen indents it.
_RULES_SHAPE = (
    "codescan_rules lists the rule codes to report, G-7120 or APEX-035, or a\n"
    "prefix closed by *, G-7* or APEX-*; an empty list reports every rule."
)


def codescan_rules(config: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The rule codes a codescan run reports, ``()`` for every rule (ADT #1023).

    Codes read upper case, as SQLcl prints them, in the order written and once
    each. A code is not checked against SQLcl's own list, which grows with
    every release; only its shape is, so `G 7120` or `G-*7` is refused by its
    place in the list rather than read as a profile that reports nothing.
    """
    raw = (config or {}).get("codescan_rules")
    if raw is None:
        return ()
    values = [raw] if isinstance(raw, str) else raw
    if not isinstance(values, list | tuple):
        raise CodescanSettingError(f"codescan_rules IS NOT A LIST\n\n{_RULES_SHAPE}")
    rules: list[str] = []
    for number, value in enumerate(values, start=1):
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise CodescanSettingError(
                f"codescan_rules ENTRY {number} IS NOT A RULE CODE\n\n{_RULES_SHAPE}"
            )
        code = str(value).strip().upper()
        if not code:
            raise CodescanSettingError(f"codescan_rules ENTRY {number} IS EMPTY\n\n{_RULES_SHAPE}")
        if not _RULE_RE.fullmatch(code):
            raise CodescanSettingError(
                f"codescan_rules ENTRY {number} '{value}' IS NOT A RULE CODE\n\n{_RULES_SHAPE}"
            )
        if code not in rules:
            rules.append(code)
    return tuple(rules)


def rule_selected(rule: str, rules: tuple[str, ...]) -> bool:
    """Whether a finding under ``rule`` is one `codescan_rules` reports."""
    if not rules:
        return True
    code = rule.strip().upper()
    return any(
        code.startswith(pattern[:-1]) if pattern.endswith("*") else code == pattern
        for pattern in rules
    )


def patch_codescan(config: Mapping[str, Any] | None) -> str:
    """Whether `patch -create` scans the files it carries: `off`, `warn` or `block`.

    YAML reads a bare `off` as false, so false is `off` as well; any other
    value outside the three is refused by name.
    """
    raw = (config or {}).get("patch_codescan")
    if raw is None or raw is False:
        return DEFAULT_PATCH_CODESCAN
    value = str(raw).strip().lower()
    if raw is True or value not in PATCH_CODESCAN_VALUES:
        raise CodescanSettingError(
            f"UNKNOWN patch_codescan '{raw}'\n\n"
            "patch_codescan takes off, warn or block."
        )
    return value


__all__ = [
    "APEX",
    "CODESCAN_PATHS",
    "CODESCAN_STORE",
    "DATABASE",
    "DEFAULT_FAIL_ON",
    "DEFAULT_PATCH_CODESCAN",
    "DEFAULT_PATHS",
    "FAIL_ON_ANY",
    "FAIL_ON_NEW",
    "FAIL_ON_NONE",
    "FAIL_ON_VALUES",
    "PATCH_CODESCAN_BLOCK",
    "PATCH_CODESCAN_OFF",
    "PATCH_CODESCAN_VALUES",
    "PATCH_CODESCAN_WARN",
    "CodescanIgnore",
    "CodescanSettingError",
    "codescan_fail_on",
    "codescan_ignore",
    "codescan_paths",
    "codescan_rules",
    "patch_codescan",
    "rule_selected",
]
