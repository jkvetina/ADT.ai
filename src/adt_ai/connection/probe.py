"""What `connection -test` reads once it is connected (ADT #948).

A connection test answers two questions: did the credentials open a session, and
is it the schema the reader expected. The first is the connect itself. The second
is a count of what the schema holds per object type, the same shape the export
overview prints, so a wrong schema or an empty one is obvious at a glance.

The count reads `USER_OBJECTS` directly and filters nothing a project configured.
It is not an export preview: `prefix` and `ignore` decide what `export_db`
writes, not what the connected user owns, and a test that silently applied them
would report a smaller schema than the one it reached. Only Oracle's own
scaffolding stays out, the system-generated names, the secondary objects of a
domain index, and the recycle bin, none of which anyone created. So do partition
and LOB segments: they are a table's storage, not objects of their own, and the
export overview does not count them either.
"""

from __future__ import annotations

from dataclasses import dataclass

from adt_ai.connection.queries import CONNECT_PROBE_QUERY, OBJECT_COUNTS_QUERY
from adt_ai.shared.db import QueryGateway


@dataclass(frozen=True)
class ObjectCount:
    object_type : str
    total       : int
    invalid     : int


def object_counts(gateway: QueryGateway) -> list[ObjectCount]:
    return [
        ObjectCount(
            str(row["OBJECT_TYPE"]),
            int(row["TOTAL"] or 0),
            int(row["INVALID"] or 0),
        )
        for row in gateway.fetch_all(OBJECT_COUNTS_QUERY, {})
    ]


def open_session(gateway: QueryGateway) -> None:
    """Open a session and nothing more; a refused connect raises from here."""
    gateway.fetch_all(CONNECT_PROBE_QUERY, {})
