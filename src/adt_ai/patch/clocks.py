"""Resolving the two clocks the export-freshness gate compares.

``patch -create`` asks one question of every file it would snapshot: did the
database move past this export? One side of that comparison is a repo file's
mtime, an absolute epoch on THIS host. The other is the mirrored
``LAST_DDL_TIME``, a naive wall-clock reading taken on the DATABASE server.
Turning the second into an epoch is the whole job of this module, and it is
arithmetic rather than policy, so it lives beside the gate instead of inside it
(:mod:`adt_ai.patch.staleness` owns what to do with the answer).

Before ADT #394 there was no second clock: ``datetime.fromisoformat(value)
.timestamp()`` resolved the database's reading in the host's zone, so the two
sides were read off different clocks and the error was the offset between them.
Measured on `local-26ai` (`APPS@FREEPDB1`, container UTC, host CEST) on
2026-08-18, a view re-created at host `17:57:51` recorded ``LAST_DDL_TIME =
15:57:50`` against a file mtime of `17:55:33`, and the gate passed a stale
export where it had to refuse.

`dependencies -refresh` now records the database's own UTC offset per scope, and
:func:`ddl_seconds` reads through it. The offset comes from ``SYSTIMESTAMP``
rather than ``SESSIONTIMEZONE``, because the session zone is whatever
python-oracledb set from this host and would hand the same bug back wearing a
database-side spelling.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# The `refreshes` stamps carry no sub-second part, so this is the format
# both readings parse under.
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# Oracle's TZH:TZM spelling, with the colon optional so a hand-edited mirror
# holding `+0200` still resolves.
_OFFSET_RE = re.compile(r"([+-])(\d{1,2}):?(\d{2})")


def offset_zone(offset: str) -> timezone | None:
    """Oracle's ``TZH:TZM`` spelling (``+02:00``) as a fixed-offset zone.

    Unparseable reads as None rather than falling back to UTC: a wrong zone
    shifts every comparison by whole hours without saying so, and not being
    silently wrong is this gate's entire purpose.
    """
    match = _OFFSET_RE.fullmatch(str(offset).strip())
    if match is None:
        return None
    sign, hours, minutes = match.groups()
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    return timezone(-delta if sign == "-" else delta)


def ddl_seconds(value: str, offset: str) -> int | None:
    """Whole seconds for a mirrored ``LAST_DDL_TIME``, read on its own clock.

    The mirror stores ``str(value)`` of whatever the driver returned
    (`dependencies/refresh.py`), which for an Oracle DATE is
    ``2026-08-09 19:56:15``. ISO parsing is tried FIRST so a date-only value, a
    ``T`` separator or a sub-second part all still resolve: a format this could
    not read would report "nothing is stale" for every object, which is the one
    failure mode a guard must not have.

    ``offset`` is the database server's UTC offset for this object's owner. A
    value that already carries its own zone keeps it; nothing here overrides an
    explicit one.
    """
    zone = offset_zone(offset)
    if zone is None:
        return None
    parsed: datetime | None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        parsed = _strptime(value)
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return int(parsed.timestamp())


def stamp_seconds(stamp: str) -> int | None:
    """Whole seconds since the epoch for a ``refreshes`` stamp, or None.

    Whole seconds because the stamp carries no sub-second part: a file written
    inside the stamp's own second was caught by that refresh, not missed by it,
    so only a strictly newer second counts as stale.

    **That is a choice between two errors, not an oversight.** `#657` proposed
    comparing the caller's full float mtime instead, on the reading that a
    same-second edit is silently called fresh. It is, and the alternative is
    worse. The missing precision is on THIS side: `_now_stamp` writes whole
    seconds, so a refresh that really did run after an export written at
    `11:30:46.9` records as `11:30:46` and a full-float compare calls the scope
    stale. `patch -create` auto-refreshes on that report (`#569`), records the
    same truncated stamp, and reports stale again, which is a refusal no refresh
    can clear. Measured against `tests/cli/test_patch_section_order.py`, which
    exports and refreshes inside one second exactly as `-create` does.

    Closing the gap for real means storing the stamp with a sub-second part, and
    that is a change to a shipped store's format and to every screen that prints
    a stamp. Until then the second is inclusive, and the miss it allows is an
    export and a refresh landing in the same second in that order.

    Read in the HOST's zone deliberately, unlike :func:`ddl_seconds`. Both sides
    of the graph-freshness comparison are ADT's own stamps, written by this host
    (`_now_stamp` in `dependencies/runner.py`), so there is no second clock in
    that comparison and introducing one would be the bug in reverse.
    """
    parsed = _strptime(stamp)
    return None if parsed is None else int(parsed.timestamp())


def _strptime(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, STAMP_FORMAT)
    except (TypeError, ValueError):
        return None
