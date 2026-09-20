"""`search TERM`: where a piece of text lives, across five layers, offline (ADT #895).

The layers are read in the order the report lists them. The dependency mirror
answers APEX and STATIC, the files `export_db` wrote answer DB (ADT #904), and
the mirror then answers the graph sections, what each database object with a
hit uses and is used by; the page-link store answers how the pages with an APEX
hit connect. Nothing here connects to Oracle, and nothing here prints: the CLI
renders the `TermResult`.
"""

from __future__ import annotations

from adt_ai.dependencies.store import DependencyStore
from adt_ai.flow.model import FlowEdge
from adt_ai.flow.store import ApexFlowStore
from adt_ai.search.term_exports import search_db_layer
from adt_ai.search.term_history import search_files_layer, search_git_layer
from adt_ai.search.term_mirror import APEX_LAYERS, relations, search_apex_layers
from adt_ai.search.term_model import TermRequest, TermResult
from adt_ai.shared.internal_paths import internal_path


def run_term_search(request: TermRequest) -> TermResult:
    result = TermResult()
    _search_database(request, result)
    search_git_layer(request, result)
    search_files_layer(request, result)
    result.links = _page_links(request, result)
    return result


def _search_database(request: TermRequest, result: TermResult) -> None:
    """APEX and STATIC from the mirror, DB from the export, and the DB hits' graph.

    A project with no mirror at all names APEX and STATIC as not searched and
    goes on: the text search never builds a store, and a missing one is the same
    answer as an empty one, never an error. DB needs no mirror, only the files;
    without one its hits simply come with no `USES / USED BY`.
    """
    db_path = internal_path(request.root, "dependencies.db")
    if not db_path.exists():
        apex = " / ".join(layer for layer in APEX_LAYERS if layer in request.layers)
        if apex:
            result.not_searched.append(
                (apex, "no dependency database, and it could not be built")
            )
        search_db_layer(request, result)
        return
    with DependencyStore.open(db_path) as store:
        search_apex_layers(store, request, result)
        search_db_layer(request, result)
        result.relations = relations(store, result.hits)


def _page_links(request: TermRequest, result: TermResult) -> list[FlowEdge]:
    """The links into and out of every page with an APEX hit, each link once.

    Read only for an application the page-link store holds; a page of one it
    does not hold simply has no row, since `PAGE LINKS` adds to the hits rather
    than being a layer of its own.
    """
    pages = sorted(
        {
            (hit.app, hit.page)
            for hit in result.hits
            if hit.source == "APEX" and hit.app is not None and hit.page is not None
        }
    )
    db_path = internal_path(request.root, "flow.db")
    if not pages or not db_path.exists():
        return []
    links: dict[FlowEdge, None] = {}
    with ApexFlowStore.open(db_path) as store:
        for app_id, page in pages:
            if store.has_app(app_id):
                links.update(
                    dict.fromkeys([*store.incoming(app_id, page), *store.outgoing(app_id, page)])
                )
    return list(links)


__all__ = [name for name in globals() if not name.startswith("_")]
