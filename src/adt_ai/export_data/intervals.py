"""Oracle's two interval types as text, in the spellings Oracle itself reads back.

Card `#923` taught the export both types. The driver hands an INTERVAL DAY TO
SECOND over as a `timedelta` and an INTERVAL YEAR TO MONTH as
`oracledb.IntervalYM`, and neither has a `str()` any Oracle conversion reads:
`1 day, 2:03:04.000005` fails with ORA-01867, and the named tuple used to stop
the export outright. Every spelling the export writes lives here, so the side
that writes a value and the side that reads it back cannot drift:

* the SQL form a CSV cell carries, `+1 02:03:04.000005` and `+01-02`, which the
  MERGE loads with `TO_DSINTERVAL` and `TO_YMINTERVAL`;
* the ISO 8601 duration a JSON sidecar carries, `P1DT2H3M4.000005S` and
  `P1Y2M`, spelled exactly as Oracle's own `JSON_SERIALIZE` prints the same
  interval, which `JSON_VALUE ... RETURNING INTERVAL` reads back.

Both forms put one sign on the whole value, the way Oracle does. The driver does
not: a `timedelta` keeps only its days negative, so minus one hour is
`-1 day, 23:00:00`, and an `IntervalYM` signs each field it fills, so minus
fourteen months is `(-1, -2)` and minus two is `(0, -2)`. The sign is therefore
taken off the total and the parts are counted from what is left.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

_MICROSECONDS_PER_SECOND = 1_000_000
_MONTHS_PER_YEAR = 12


def ds_interval_text(value: timedelta) -> str:
    """An INTERVAL DAY TO SECOND as `TO_DSINTERVAL` reads it: `+1 02:03:04.000005`."""
    negative, days, hours, minutes, seconds, fraction = _ds_parts(value)
    sign = "-" if negative else "+"
    return f"{sign}{days} {hours:02d}:{minutes:02d}:{seconds:02d}.{fraction:06d}"


def ds_interval_iso(value: timedelta) -> str:
    """An INTERVAL DAY TO SECOND as Oracle's JSON prints it: `P1DT2H3M4.000005S`.

    A part that is zero is left out, a fraction is six digits whenever there is
    one, and an interval of nothing is `P0D`.
    """
    negative, days, hours, minutes, seconds, fraction = _ds_parts(value)
    time_part = (
        (f"{hours}H" if hours else "")
        + (f"{minutes}M" if minutes else "")
        + _iso_seconds(seconds, fraction)
    )
    body = (f"{days}D" if days else "") + (f"T{time_part}" if time_part else "")
    return f"{'-' if negative else ''}P{body or '0D'}"


def _iso_seconds(seconds: int, fraction: int) -> str:
    if fraction:
        return f"{seconds}.{fraction:06d}S"
    return f"{seconds}S" if seconds else ""


def is_ym_interval(value: Any) -> bool:
    """Whether `value` is the driver's `oracledb.IntervalYM`, told by its fields.

    python-oracledb defines it as a named tuple of `years` and `months`, and the
    export reaches the driver only through the gateway, so the shape is checked
    rather than the class imported. Without the check it passes for a tuple,
    which a JSON document writes as an array.
    """
    return getattr(type(value), "_fields", None) == ("years", "months")


def ym_interval_text(value: Any) -> str:
    """An INTERVAL YEAR TO MONTH as `TO_YMINTERVAL` reads it: `+01-02`.

    The years are padded to two digits, the way Oracle prints the default
    YEAR(2) column; a longer one keeps every digit it has.
    """
    negative, years, months = _ym_parts(value)
    return f"{'-' if negative else '+'}{years:02d}-{months:02d}"


def ym_interval_iso(value: Any) -> str:
    """An INTERVAL YEAR TO MONTH as Oracle's JSON prints it: `P1Y2M`, or `P0Y`."""
    negative, years, months = _ym_parts(value)
    body = (f"{years}Y" if years else "") + (f"{months}M" if months else "")
    return f"{'-' if negative else ''}P{body or '0Y'}"


def _ds_parts(value: timedelta) -> tuple[bool, int, int, int, int, int]:
    total = (value.days * 86_400 + value.seconds) * _MICROSECONDS_PER_SECOND
    total += value.microseconds
    seconds, fraction = divmod(abs(total), _MICROSECONDS_PER_SECOND)
    minutes, second = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    days, hour = divmod(hours, 24)
    return total < 0, days, hour, minute, second, fraction


def _ym_parts(value: Any) -> tuple[bool, int, int]:
    total = int(value.years) * _MONTHS_PER_YEAR + int(value.months)
    years, months = divmod(abs(total), _MONTHS_PER_YEAR)
    return total < 0, years, months
