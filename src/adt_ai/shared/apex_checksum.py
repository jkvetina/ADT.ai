"""The one reader of an application's APEX checksum (ADT #962).

`export_apex` records the checksum an export was taken at and `patch` compares
the live application against that record, so the two values are only
comparable when they come from the same read. They did not: the export read
with an APEX session open, `patch` without one, and on PLAYGROUND an open session
moves the value. Both call this now, and the block, `queries.APEX_CHECKSUM_BLOCK`,
detaches any session before it reads, so a caller that still needs its session
reads the checksum before opening one.

Nothing here decides what a failure means. An application id nothing is
installed on raises from the block, and whether that is an answer ("absent")
or an error is the caller's question: `patch` answers it, an export never asks
it because it only reads applications it just exported.
"""

from __future__ import annotations

from adt_ai.shared import queries
from adt_ai.shared.db import QueryGateway


def read_apex_checksum(gateway: QueryGateway, app_id: int) -> str:
    """``app_id``'s `CHECKSUM-SH256`, as APEX answered it, framing stripped.

    APEX returns the value as file contents, so it arrives with the line
    endings of whatever wrote it; the `SH256:` prefix is part of the value.
    """
    value = gateway.fetch_clob(queries.APEX_CHECKSUM_BLOCK, {"app_id": app_id})
    return str(value or "").strip()
