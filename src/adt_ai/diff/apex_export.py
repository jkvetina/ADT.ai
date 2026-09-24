"""A CHANGED application exported on both sides and read as components (ADT #892).

Split out of `diff/apex.py` by ADT #923, which took that module past the 20 KB
context size. What stays there is what a side is, what it answered and how the
two answers compare; what moved is the drill-down's own read: the export each
format runs, the options it is bound with, and both sides read at once.
"""
from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from adt_ai.diff.apex_components import APEXLANG, READABLE, SPLIT, Component, parse_components
from adt_ai.export_apex import queries
from adt_ai.export_apex.postprocess import _bind_params
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.row_values import row_value

if TYPE_CHECKING:
    from adt_ai.diff.apex import ApexSide

#: The export each format runs, and the options it is bound with. Every option
#: is off: dates, comments, translations and audit stamps are what two
#: environments holding one application disagree about, and original ids would
#: only put back the ids the comparison ignores.
_EXPORT_QUERIES = {
    APEXLANG : queries.EXPORT_APEXLANG_QUERY,
    READABLE : queries.EXPORT_READABLE_QUERY,
    SPLIT    : queries.EXPORT_SPLIT_QUERY,
}
_EXPORT_OPTIONS = {
    "originals"               : "N",
    "with_comments"           : "N",
    "with_date"               : "N",
    "with_ir_public_reports"  : "N",
    "with_ir_private_reports" : "N",
    "with_ir_notifications"   : "N",
    "with_translations"       : "N",
    "with_no_subscriptions"   : "N",
    "with_acl_assignments"    : "N",
    "with_audit_info"         : "",
}


def read_components(
    side: ApexSide, app_ids: Sequence[int], export: str
) -> dict[int, dict[str, Component]]:
    """Each named application exported in `export` and read as components.

    The runner reads both sides in one format, since two formats never compare,
    and reads an application again as split SQL when its readable export came
    back empty on either side (Jan: *"if you dont have readable, you should use
    -split"*).
    """
    return {
        app_id: _export_components(side.gateway, side.own_id(app_id), export)
        for app_id in app_ids
    }


def read_both(
    pool: ThreadPoolExecutor,
    source: ApexSide,
    target: ApexSide,
    app_ids: Sequence[int],
    export: str,
) -> tuple[dict[int, dict[str, Component]], dict[int, dict[str, Component]]]:
    """Both sides' components at once, on the runner's own pool."""
    source_read = pool.submit(read_components, source, app_ids, export)
    target_read = pool.submit(read_components, target, app_ids, export)
    return source_read.result(), target_read.result()


def _export_components(gateway: QueryGateway, app_id: int, export: str) -> dict[str, Component]:
    gateway.execute(queries.EXPORT_START_QUERY, {"app_id": app_id})
    sql = _EXPORT_QUERIES[export]
    gateway.execute(sql, _bind_params(sql, {"app_id": app_id, **_EXPORT_OPTIONS}))
    files = {
        str(row_value(row, "FILE_NAME") or ""): str(row_value(row, "CLOB_CONTENT") or "")
        for row in gateway.fetch_all(queries.FETCH_FILES_QUERY)
    }
    return parse_components(export, {_relative(name, app_id): text for name, text in files.items()})


def _relative(name: str, app_id: int) -> str:
    """The member name without its `f<id>/` root, which only split and readable carry."""
    return name.removeprefix(f"f{app_id}/")


__all__ = [name for name in globals() if not name.startswith("_")]
