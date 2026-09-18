"""The `DEPSCAN$<n>#<n>` helper procedures, named and removed in one place (ADT #888).

`APEX_APP_OBJECT_DEPENDENCY.SCAN` generates them on the schema it scans. ADT's own
scans drop them again behind one boundary (`dependencies/component_scan.py`,
`#699`), but a schema can still carry them from a scan ADT did not run: the APEX
Builder's own dependency utility, another release, or a run from before `#699`.

Jan, 2026-09-18: *"`recompile -trailing` is fixing DEPSCAN$ procedures -- they
should be ignored -- they should not be present at all."* So the name pattern
lives in `shared/queries/scan_helpers.py`, once, and every reader of
`user_objects` that must not see a helper filters on it: `export_db` never
writes one to the repository, and `recompile` never compiles or rewrites one.
`recompile` also removes whatever strays it finds, silently, the way
`dependencies` and `patch` already do after their own scan (Jan's answer:
*"Silent, like elsewhere"*).
"""

from __future__ import annotations

from typing import Any

from adt_ai.shared.queries.scan_helpers import (
    DROP_SCAN_HELPERS_STATEMENT,
    SCAN_HELPER_NAME_PATTERN,
    SCAN_HELPERS_QUERY,
    not_a_scan_helper,
)


def drop_scan_helpers(gateway: Any) -> None:
    """Take the helper procedures away, on their own, unconditionally."""
    gateway.execute(DROP_SCAN_HELPERS_STATEMENT)


def drop_stray_scan_helpers(gateway: Any) -> None:
    """Drop the helpers only when the schema carries some.

    For a command that did not run a scan itself: the read costs one query, and
    it is what keeps a clean schema free of any DDL the user did not ask for.
    """
    if gateway.fetch_all(SCAN_HELPERS_QUERY):
        drop_scan_helpers(gateway)


__all__ = [
    "DROP_SCAN_HELPERS_STATEMENT",
    "SCAN_HELPERS_QUERY",
    "SCAN_HELPER_NAME_PATTERN",
    "drop_scan_helpers",
    "drop_stray_scan_helpers",
    "not_a_scan_helper",
]
