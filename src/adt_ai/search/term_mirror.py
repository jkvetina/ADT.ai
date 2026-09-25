"""The two `search TERM` layers the dependency mirror holds, APEX and STATIC (ADT #895).

Each reads only what a refresh stamped as filled, the `apex_source` stamp for
an application's component text and static files. A version 5 mirror carries
`app` stamps from before the text existed, and those never count: a layer
nobody filled is reported as not searched, which is the difference between
"nothing there" and "never looked". DB is read from the files `export_db` wrote
rather than from here (`term_exports.py`, ADT #904); what its hits use and are
used by is read here, from the graph.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import replace
from typing import Any

from adt_ai.dependencies.schema import APEX_SOURCE_SCOPE
from adt_ai.dependencies.store import DependencyStore
from adt_ai.search import queries
from adt_ai.search.term_model import (
    Hit,
    Relation,
    TermRequest,
    TermResult,
    in_selection,
    is_big,
    line_hits,
    line_matches,
    sql_term,
)

APEX_LAYERS = ("APEX", "STATIC")

#: What a dependent references when the hit was in a body: the spec. A caller
#: of `CORE_API` depends on `PACKAGE CORE_API`, never on its body.
_SPEC_OF = {"PACKAGE BODY": "PACKAGE", "TYPE BODY": "TYPE"}


def stamped(store: DependencyStore, scope_type: str) -> list[str]:
    """The scopes a text refresh stamped, as the stamp spells them."""
    return [row["scope"] for row in store.last_refreshes() if row["type"] == scope_type]


def search_apex_layers(store: DependencyStore, request: TermRequest, result: TermResult) -> None:
    """APEX and STATIC, whichever of the two the request asked for."""
    layers = [layer for layer in APEX_LAYERS if layer in request.layers]
    if not layers:
        return
    label = " / ".join(layers)
    refreshed = {
        int(scope) for scope in stamped(store, APEX_SOURCE_SCOPE) if scope.isdigit()
    }
    apps = _requested_apps(request, refreshed, label, result)
    if not apps:
        if not request.narrows_apps:
            result.not_searched.append(
                (label, "no application source is mirrored, and none could be refreshed")
            )
        return
    result.searched.update(layers)
    if "APEX" in layers:
        result.hits.extend(_apex_hits(store, request, apps))
    if "STATIC" in layers:
        result.hits.extend(_static_hits(store, request.term, apps))


def _requested_apps(
    request: TermRequest,
    refreshed: set[int],
    label: str,
    result: TermResult,
) -> list[int]:
    """The refreshed applications in scope, naming every one asked for and missing.

    Without `-app` that is every refreshed application. A named id nobody
    refreshed is a row of its own under `WARNING - NOT SEARCHED`, and so is a range that
    reaches no refreshed application, rather than either reading as no hits.
    """
    if not request.narrows_apps:
        return sorted(refreshed)
    for app_id in request.app_ids:
        if app_id not in refreshed:
            result.not_searched.append(
                (label, f"APP {app_id} is not mirrored, and could not be refreshed")
            )
    ranged = {app for app in refreshed if in_selection(app, (), request.app_ranges)}
    if request.app_ranges and not ranged:
        result.not_searched.append(
            (label, "-app range matched no mirrored application, and none could be refreshed")
        )
    return sorted((set(request.app_ids) & refreshed) | ranged)


def _in(values: Iterable[Any]) -> str:
    return ",".join("?" for _ in values)


def _rows(store: DependencyStore, template: str, scope: list[Any], term: str, **slot: str) -> Any:
    """The rows of `template` over `scope`, pre-filtered on TERM where SQLite can."""
    folded = sql_term(term)
    sql = template.format(match=queries.TERM_MATCH if folded else "", **slot)
    params = [*scope, folded] if folded else scope
    return store.connection.execute(sql, params).fetchall()


def _apex_hits(store: DependencyStore, request: TermRequest, apps: list[int]) -> Iterator[Hit]:
    rows = _rows(store, queries.APEX_SOURCE_TEMPLATE, apps, request.term, apps=_in(apps))
    for row in rows:
        page = row["PAGE_ID"]
        if request.narrows_pages and not in_selection(
            page, request.page_ids, request.page_ranges
        ):
            continue
        # The type leads the name: the table has no column of its own for it,
        # and `Customers` alone could be a region, a list or a report.
        component = f"{row['COMPONENT_TYPE']} {row['COMPONENT_NAME'] or row['COMPONENT_ID']}"
        for line, flag, text in line_hits(row["TEXT"] or "", request.term):
            yield Hit(
                source    = "APEX",
                app       = row["APPLICATION_ID"],
                page      = page,
                component = component,
                prop      = row["PROPERTY"],
                line      = line,
                flag      = flag,
                excerpt   = text,
            )


def _static_hits(store: DependencyStore, term: str, apps: list[int]) -> Iterator[Hit]:
    for row in _rows(store, queries.STATIC_FILES_TEMPLATE, apps, term, apps=_in(apps)):
        text = row["TEXT"]
        workspace = row["SCOPE"] == "WORKSPACE"
        base = Hit(
            source    = "STATIC",
            app       = None if workspace else row["APPLICATION_ID"],
            component = row["FILE_NAME"],
            prop      = row["SCOPE"],
        )
        size = row["BYTES"] if row["BYTES"] is not None else len(text.encode("utf-8"))
        if is_big(row["FILE_NAME"], size):
            # Only a whole-file hit is decided here: the SQL pre-filter cannot
            # fold a non-ASCII term, so a row it let through may still not match.
            if line_matches(text, term):
                yield base
            continue
        for line, flag, cell in line_hits(text, term):
            yield replace(base, line=line, flag=flag, excerpt=cell)


def relations(store: DependencyStore, hits: Iterable[Hit]) -> list[Relation]:
    """`USES / USED BY` for each distinct database object with a hit.

    One object per owner and name, so a hit in a spec and one in its body ask
    about one object. What it uses is read for every type hit; what uses it is
    read of the spec, which is what a dependent references, and so are the
    APEX components that use it, named by their page. A body using its own
    spec is how Oracle records every package and type, not a relation, so the
    object's own spec and body are never listed against it.
    """
    objects: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for hit in hits:
        if hit.db_object is None:
            continue
        owner, kind, name = hit.db_object
        objects.setdefault((owner, name), (hit.component, set()))[1].add(kind)
    rows: list[Relation] = []
    for (owner, name), (label, kinds) in objects.items():
        itself = {f"{kind}.{name}" for pair in _SPEC_OF.items() for kind in pair}
        uses = {
            node
            for kind in kinds
            for node in store.uses(f"{kind}.{name}", owners=[owner])
            if node not in itself
        }
        specs = {_SPEC_OF.get(kind, kind) for kind in kinds}
        used_by = {
            node
            for kind in specs
            for node in store.used_by(f"{kind}.{name}", owners=[owner])
            if node not in itself
        }
        # The owner goes along: two schemas' objects of one name have two sets
        # of APEX callers, and the hit is in exactly one of them (`#958`).
        pages = {
            _apex_page(caller)
            for kind in specs
            for caller in store.apex_callers(f"{kind}.{name}", owners=[owner])
        }
        rows.extend(Relation(label, "USES", *node.split(".", 1)) for node in sorted(uses))
        rows.extend(Relation(label, "USED BY", *node.split(".", 1)) for node in sorted(used_by))
        rows.extend(Relation(label, "USED BY", kind, page) for kind, page in sorted(pages))
    return rows


def _apex_page(caller: dict[str, Any]) -> tuple[str, str]:
    """An APEX caller as `(kind, name)`: its page as `APP.PAGE`, or its application."""
    if caller["page_id"] is None:
        return "APEX APP", str(caller["application_id"])
    return "APEX PAGE", f"{caller['application_id']}.{caller['page_id']}"


__all__ = [name for name in globals() if not name.startswith("_")]
