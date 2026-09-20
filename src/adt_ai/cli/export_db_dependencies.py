"""`export_db` brings the dependency mirror level with what it just exported (`#30`).

An export is the moment the repository and the database agree about a set of
objects, which makes it the cheapest moment to bring the mirror `search -to`,
`-from` and `-impact` answer from level with them: the connection is already
open and the names are already known. So after each schema's export the mirror
is refreshed for exactly the objects written, through the same scoped refresh
`rebuild` uses, and never for the rest of the schema.

A project with no mirror yet has nothing a scoped refresh could extend, and a
mirror holding one exported package would answer every other question in that
schema with an empty list. So the first export builds the whole schema instead.

The section carries `UPDATING DEPENDENCIES:`, Jan's own header for this same
block on `patch` (ADT `#413`), rather than a second name for the same work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    REFRESH_HEADER,
    DependencyIndexRequest,
    DependencyIndexRunner,
    GatewayFactory,
)
from adt_ai.export_db.files import ObjectWritePlan
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.progress import FixedWidthProgressPrinter, print_adt_header

#: The longest name list the scoped refresh is handed, in characters. The names
#: travel as ONE comma-joined bind that `APEX_STRING.SPLIT` reads in SQL, where a
#: VARCHAR2 bind stops at 4000 bytes unless the database runs with
#: `MAX_STRING_SIZE=EXTENDED`. A whole-schema export can name thousands of
#: objects, and past this length the schema's own incremental refresh is the
#: same answer for the exported objects (it reloads exactly the ones whose DDL
#: moved) at one pass over the dictionary instead of a LIKE per name.
SCOPED_FILTER_LIMIT = 4000

#: Files `export_db` writes that are not dictionary objects: a grants file is
#: named after its schema (`APP_schema`, `received/HR`) and a data file after the
#: table it holds rows of. The same pair `export_db/files.py` sets apart. Their
#: names would spend the bind above and could match an unrelated object.
NOT_DICTIONARY_OBJECTS = frozenset({"DATA", "GRANT"})


def refresh_exported_dependencies(
    root: Path,
    config: dict[str, Any],
    schema: str,
    plans: list[ObjectWritePlan],
    gateway_factory: GatewayFactory,
    *,
    silent: bool,
) -> None:
    """Refresh the mirror for the objects in ``plans``, or the whole schema if none exists."""
    names = sorted(
        {
            plan.object.name.upper()
            for plan in plans
            if plan.object.object_type.upper() not in NOT_DICTIONARY_OBJECTS
        }
    )
    mirror_exists = internal_path(root, "dependencies.db").exists()
    if mirror_exists and not names:
        return
    scoped = mirror_exists and len(",".join(names)) <= SCOPED_FILTER_LIMIT
    # Printed under `-silent` too: the console contract keeps banners,
    # connection blocks, section headers and the timer whatever else a silent
    # run suppresses.
    print_adt_header(REFRESH_HEADER)
    DependencyIndexRunner(gateway_factory).refresh(
        DependencyIndexRequest(
            root          = root,
            schemas       = [schema],
            config        = config,
            refresh_names = names if scoped else None,
            progress      = None if silent else FixedWidthProgressPrinter(),
        )
    )


__all__ = [
    "refresh_exported_dependencies",
]
