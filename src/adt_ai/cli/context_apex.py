from __future__ import annotations

import argparse
import re
from collections.abc import Mapping
from dataclasses import dataclass

from adt_ai.cli.constants import APEX_EXPORT_ACTIONS
from adt_ai.export_apex.filters import ApexComponentFilter, ApexPageSelection
from adt_ai.shared.sql_like import matches_sql_like


@dataclass(frozen=True)
class ApexAppSelection:
    """A parsed `-app` selection: plain ids plus closed/open id ranges.

    `explicit_ids` keeps the existing plain-id tokens; `ranges` holds
    `(min, max)` for closed ranges and `(min, None)` for open `MIN+` ranges.
    """

    explicit_ids: tuple[str, ...] = ()
    ranges: tuple[tuple[int, int | None], ...] = ()

    @property
    def has_ranges(self) -> bool:
        return bool(self.ranges)

def _parse_apex_app_selection(tokens: list[str] | None) -> ApexAppSelection | None:
    if not tokens:
        return None
    explicit: list[str] = []
    ranges: list[tuple[int, int | None]] = []
    for raw in tokens:
        token = raw.strip()
        if not token:
            continue
        parsed_range = _parse_range_token(token, "-app")
        if parsed_range is not None:
            ranges.append(parsed_range)
        else:
            explicit.append(token)
    return ApexAppSelection(explicit_ids=tuple(explicit), ranges=tuple(ranges))

def _parse_range_token(token: str, flag: str) -> tuple[int, int | None] | None:
    """Parse a `MIN-MAX` or `MIN+` range token; ``None`` means a plain token."""
    open_match = re.fullmatch(r"(\d+)\+", token)
    closed_match = re.fullmatch(r"(\d+)-(\d+)", token)
    if open_match:
        return (int(open_match.group(1)), None)
    if closed_match:
        low = int(closed_match.group(1))
        high = int(closed_match.group(2))
        if low > high:
            raise ValueError(
                f"invalid {flag} range '{token}': min {low} is greater than max {high}"
            )
        return (low, high)
    if "-" in token or "+" in token:
        raise ValueError(f"invalid {flag} range '{token}': use MIN-MAX or MIN+")
    return None

def _parse_apex_page_selection(tokens: list[str] | None) -> ApexPageSelection | None:
    if not tokens:
        return None
    explicit: list[int] = []
    ranges: list[tuple[int, int | None]] = []
    for raw in tokens:
        for token in [part.strip() for part in raw.split(",") if part.strip()]:
            _append_page_selection_token(token, explicit, ranges)
    return ApexPageSelection(explicit_ids=tuple(explicit), ranges=tuple(ranges))

def _append_page_selection_token(
    token: str,
    explicit: list[int],
    ranges: list[tuple[int, int | None]],
) -> None:
    parsed_range = _parse_range_token(token, "-page")
    if parsed_range is not None:
        ranges.append(parsed_range)
    else:
        explicit.append(int(token))

def _parse_apex_component_filters(
    tokens: list[str] | None,
) -> tuple[ApexComponentFilter, ...]:
    if not tokens:
        return ()
    filters: list[ApexComponentFilter] = []
    for raw in tokens:
        token = raw.strip()
        if not token:
            continue
        component_type, separator, name_pattern = token.partition(":")
        if not separator or not component_type.strip() or not name_pattern.strip():
            raise ValueError(
                f"invalid -component filter '{token}': use TYPE:NAME_PATTERN"
            )
        filters.append(ApexComponentFilter(component_type.strip(), name_pattern.strip()))
    return tuple(filters)

def _parse_apex_export_filters(
    page_tokens: list[str] | None,
    component_tokens: list[str] | None,
) -> tuple[ApexPageSelection | None, tuple[ApexComponentFilter, ...]]:
    return (
        _parse_apex_page_selection(page_tokens),
        _parse_apex_component_filters(component_tokens),
    )

def _parse_apex_export_filter_groups(
    page_groups: list[list[str]] | None,
    component_groups: list[list[str]] | None,
) -> tuple[ApexPageSelection | None, tuple[ApexComponentFilter, ...]]:
    return _parse_apex_export_filters(
        _flatten_arg_groups(page_groups),
        _flatten_arg_groups(component_groups),
    )

def _flatten_arg_groups(groups: list[list[str]] | None) -> list[str] | None:
    if not groups:
        return None
    return [
        part.strip()
        for group in groups
        for item in group
        for part in item.split(",")
        if part.strip()
    ]

def _flatten_compile_setting_groups(groups: list[list[str]] | None) -> list[str] | None:
    """Flatten repeated/space groups of ``-scope`` or ``-warnings`` tokens.

    Each token is split on both comma and ``+`` and upper-cased, so
    ``-scope IDENTIFIERS STATEMENTS``, ``-scope IDENTIFIERS,STATEMENTS``,
    ``-scope IDENTIFIERS+STATEMENTS``, and a repeated ``-scope`` all yield the same
    list. Unlike ``-name``/``-type`` these are keyword tokens (case-insensitive
    vocabulary), so ``+`` — invalid in an Oracle identifier — is a safe extra
    separator and case is normalised.
    """
    if not groups:
        return None
    return [
        part.strip().upper()
        for group in groups
        for item in group
        for chunk in item.split(",")
        for part in chunk.split("+")
        if part.strip()
    ]


def _app_in_selection(app_id: int | str, selection: ApexAppSelection) -> bool:
    if str(app_id) in selection.explicit_ids:
        return True
    try:
        numeric = int(app_id)
    except (TypeError, ValueError):
        return False
    return any(
        numeric >= low and (high is None or numeric <= high)
        for low, high in selection.ranges
    )

@dataclass(frozen=True)
class ApexScope:
    """What one schema's APEX export is scoped to: workspace, group, app ids.

    Frozen and named rather than a dict: `export_apex` reads these three fields
    back at seven call sites, and a string subscript turns a typo into a
    `KeyError` raised mid-export against a live database instead of an
    `AttributeError` the type checker and the import already know about.
    """

    workspace: str | None = None
    group: str | None = None
    app_ids: list[str] | None = None


def _apex_scope(
    apex_config: Mapping[str, object],
    workspace: str | None = None,
    group: str | None = None,
    app_ids: list[str] | None = None,
) -> ApexScope:
    return ApexScope(
        workspace = workspace or _string_or_none(apex_config.get("workspace")),
        group     = group or _string_or_none(apex_config.get("group")),
        app_ids   = app_ids or _split_config_values(apex_config.get("app")),
    )

def _apex_actions(
    args: argparse.Namespace,
    _config: Mapping[str, object] | None = None,
) -> dict[str, bool]:
    actions = dict.fromkeys(APEX_EXPORT_ACTIONS, False)
    if getattr(args, "all_formats", False):
        return {action: True for action in APEX_EXPORT_ACTIONS}
    for action in APEX_EXPORT_ACTIONS:
        if getattr(args, action, False):
            actions[action] = True
    return actions

def _apex_explicit_actions(args: argparse.Namespace) -> frozenset[str]:
    """The formats the user named by their own flag.

    `_apex_actions` folds `-all` and the individual flags into one
    `dict[str, bool]`, which is the right shape for "export this" but loses
    *why* a format is selected. Some output is owed only to a format the user
    asked for by name — the APEXlang pre-26.1 skip row is a notice about
    something `-all` never requested (ADT #235) — so the distinction is carried
    rather than re-derived downstream. Empty under `-all`, by construction.
    """
    if getattr(args, "all_formats", False):
        return frozenset()
    return frozenset(
        action for action in APEX_EXPORT_ACTIONS if getattr(args, action, False)
    )

def _apex_recent_report_only(
    args: argparse.Namespace,
    actions: Mapping[str, bool],
    recent_days: int | None,
) -> bool:
    return (
        not getattr(args, "reveal", False)
        and not any(actions.values())
        and recent_days is not None
    )

def _string_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)

def _split_config_values(value: object) -> list[str] | None:
    if value is None:
        return None
    values = value if isinstance(value, list | tuple) else [value]
    normalized = [
        part.strip()
        for item in values
        for part in str(item).replace(" ", ",").split(",")
        if part.strip()
    ]
    return normalized or None

def _has_job_recent_conflict(recent_days: int | None, object_types: list[str]) -> bool:
    if recent_days is None:
        return False
    return any(matches_sql_like("JOB", object_type) for object_type in object_types)
