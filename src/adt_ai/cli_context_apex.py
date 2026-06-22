from __future__ import annotations

import argparse
import fnmatch
import re
from collections.abc import Mapping
from dataclasses import dataclass

from adt_ai.cli_constants import APEX_EXPORT_ACTIONS


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
        open_match = re.fullmatch(r"(\d+)\+", token)
        closed_match = re.fullmatch(r"(\d+)-(\d+)", token)
        if open_match:
            ranges.append((int(open_match.group(1)), None))
        elif closed_match:
            low = int(closed_match.group(1))
            high = int(closed_match.group(2))
            if low > high:
                raise ValueError(
                    f"invalid -app range '{token}': min {low} is greater than max {high}"
                )
            ranges.append((low, high))
        elif "-" in token or "+" in token:
            raise ValueError(f"invalid -app range '{token}': use MIN-MAX or MIN+")
        else:
            explicit.append(token)
    return ApexAppSelection(explicit_ids=tuple(explicit), ranges=tuple(ranges))

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

def _apex_scope(
    apex_config: Mapping[str, object],
    workspace: str | None = None,
    group: str | None = None,
    app_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "workspace": workspace or _string_or_none(apex_config.get("workspace")),
        "group": group or _string_or_none(apex_config.get("group")),
        "app_ids": app_ids or _split_config_values(apex_config.get("app")),
    }

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

def _is_enabled(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)

def _has_job_recent_conflict(recent_days: int | None, object_types: list[str]) -> bool:
    if recent_days is None:
        return False
    return any(_matches_adt_like("JOB", object_type) for object_type in object_types)

def _matches_adt_like(value: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(value.upper(), pattern.upper().replace("%", "*"))
