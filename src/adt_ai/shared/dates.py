"""Shared date arithmetic for `-recent`/`-since`/`-until` windows."""

from __future__ import annotations

import argparse
import re
from datetime import date, datetime, timedelta

_FRACTION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)$")

_WINDOW_HELP = (
    "must be a number of days, or a fraction of a day such as 1/24 "
    "(one hour) or 5/1440 (five minutes)"
)


def recent_window(value: str) -> int | float:
    """Parse a `-recent` window: whole days, or a fraction of a day.

    Oracle counts a DATE in days, so the window reaches the query as
    `SYSDATE - :recent_days` and a fraction is simply a shorter window. `1/24`
    is the past hour, `5/1440` the past five minutes.

    A window that comes out whole is returned as an `int`, never a float. Every
    console header and report row that shows a day count is spelled from this
    value, so `-recent 7` has to keep printing the screen it always printed
    rather than picking up a `.0`.

    This is the `type=` on every module's `-recent`, which is what holds the
    four declarations at one parser shape (the shared-argument-semantics
    contract compares `type.__name__`).
    """
    text = str(value).strip()
    fraction = _FRACTION_RE.match(text)
    if fraction:
        numerator, denominator = (float(part) for part in fraction.groups())
        if denominator == 0:
            raise argparse.ArgumentTypeError(f"'{value}' divides by zero, {_WINDOW_HELP}")
        return _whole_if_possible(numerator / denominator)
    try:
        window = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' {_WINDOW_HELP}") from None
    # `float` also accepts 'nan' and 'inf', which no arithmetic downstream
    # survives: a NaN bind silently matches nothing and never says why.
    if window != window or window in (float("inf"), float("-inf")):
        raise argparse.ArgumentTypeError(f"'{value}' {_WINDOW_HELP}")
    return _whole_if_possible(window)


def _whole_if_possible(window: float) -> int | float:
    return int(window) if window.is_integer() else window


def is_sub_day_window(recent_days: int | float | None) -> bool:
    """Whether a `-recent` window is shorter than a day, so its header is an instant.

    Which is also the only case that needs the database clock: a whole-day
    window renders a calendar date and must not cost a round trip.
    """
    return recent_days is not None and not float(recent_days).is_integer()


def recent_since(recent_days: int | float, *, now: datetime | None = None) -> date | datetime:
    """Where a `-recent` window starts, for the console header.

    A whole-day window is inclusive of today: `-recent 1` means "changed today",
    so it starts at today minus (days - 1), not today minus days. It is spelled
    from the client's calendar date, as it always has been.

    A window shorter than a day has no day to be inclusive of, so it reports the
    instant it really starts at, and that instant belongs to the **database**:
    the filter is `SYSDATE - :recent_days`, so a client an hour or a timezone
    away from the server would otherwise print a cutoff the query never used.
    Pass `now` as the database clock. The client clock is the fallback for a
    caller that has no gateway to ask, and it is only ever approximate.
    """
    if float(recent_days).is_integer():
        return date.today() - timedelta(days=int(recent_days) - 1)
    return ((now or datetime.now()) - timedelta(days=recent_days)).replace(microsecond=0)


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
