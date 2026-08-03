"""Shared date arithmetic for `-recent`/`-since`/`-until` windows."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta


def recent_since(recent_days: int) -> date:
    # Inclusive of today: `-recent 1` means "changed today", so the window
    # starts at today minus (days - 1), not today minus days.
    return date.today() - timedelta(days=recent_days - 1)


def resolve_since(value: str, *, option: str = "-since") -> str:
    # `-since`/`-until` accept a YYYY-MM-DD date or an integer number of days
    # back (e.g. '7' -> 7 days ago). Both resolve to an ISO date string.
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"{option}: '{value}' is not a valid date") from exc
        return text
    if re.fullmatch(r"\d+", text):
        return (date.today() - timedelta(days=int(text))).isoformat()
    raise ValueError(
        f"{option}: '{value}' must be a YYYY-MM-DD date or a number of days back"
    )
