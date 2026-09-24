"""`adtai search TERM -data`: the rows holding TERM, in the database (ADT #879, #920).

Connects the way `export_data` connects, schema by schema with its own
connection block, lists the tables, views, materialized views and synonyms the
same `export:` `ignore:` patterns leave in, one list per object type as Jan
asked on 2026-09-22 (*"But I would like to have a list per object type."*),
and streams one row per object while it reads it. The hits print last, in
the layout Jan picked on 2026-09-22: grouped by table, one `ROW | COLUMN |
VALUE` line per hit, *"Show table, row, column, value for each hit, but in a
readable way"*. An object the database refused follows them under `WARNING -
NOT SEARCHED`, its row having closed `FAILED` while the rest were read.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from itertools import groupby
from pathlib import Path

from adt_ai.cli.commands_search_term import _window
from adt_ai.cli.constants import (
    GatewayFactory,
    QueryGateway,
    print_adt_header,
    print_module_banner,
)
from adt_ai.cli.context import (
    _flatten_arg_groups,
    _is_database_connection_error,
    _load_startup_context,
    _print_connection_block,
)
from adt_ai.cli.diff_reporters import MAX_LINE, _trim
from adt_ai.cli.gateways import build_gateway, cached_schema_gateway_factory
from adt_ai.cli.refresh_connect import _resolve_refresh_names
from adt_ai.cli.search_autobuild import _refresh_namespace
from adt_ai.rebuild.reveal import REVEAL_DEFAULT_LIMIT
from adt_ai.search.data import (
    KIND_LABELS,
    KINDS,
    DataFailure,
    DataHit,
    SearchDiscovery,
    search_objects,
)
from adt_ai.shared.fixed_width import FixedWidthProgressPrinter
from adt_ai.shared.sql_like import split_patterns

#: The row key and the column name give way at this width, so a composite key
#: or a long column name never takes the value's room.
NAME_WIDTH = 30
HIT_INDENT = "    "


class _TableRows:
    """One streamed row per object: the name before its queries, the hits after."""

    def __init__(self) -> None:
        self._printer = FixedWidthProgressPrinter()

    def begin(self, table: str) -> None:
        self._printer.begin(table.upper())

    def finish(self, table: str, hits: int) -> None:
        self._printer.finish(table.upper(), hits)

    def fail(self, table: str) -> None:
        self._printer.fail(table.upper())


def _run_search_data(
    args: argparse.Namespace, gateway_factory: GatewayFactory | None = None
) -> int:
    """Search the objects of every requested schema for TERM, then print the hits."""
    print_module_banner("SEARCH")
    root = Path(args.root).expanduser().resolve()
    named = _resolve_refresh_names(_flatten_arg_groups(args.schema))
    startup = _load_startup_context(_refresh_namespace(root, schemas=named or None))
    connections = startup.connections
    environment = connections.default_environment
    schemas = (
        connections.expand_schemas(named, environment=environment)
        if named
        else connections.default_schemas(environment)
    )
    targets = {
        schema: connections.resolve(environment=environment, schema=schema) for schema in schemas
    }

    def default_gateway_factory(schema: str) -> QueryGateway:
        return build_gateway(startup, targets[schema])

    factory = cached_schema_gateway_factory(gateway_factory or default_gateway_factory, debug=False)
    limit = REVEAL_DEFAULT_LIMIT if args.limit is None else args.limit
    names = _flatten_arg_groups(args.name) or None
    hits: list[DataHit] = []
    failures: list[DataFailure] = []
    for schema in schemas:
        gateway = factory(schema)
        _print_connection_block(gateway, targets[schema], debug=False)
        objects = SearchDiscovery(gateway).objects(
            names  = names,
            ignore = split_patterns(targets[schema].export.get("ignore")),
        )
        if not objects:
            print_adt_header("SEARCHING 0 OBJECTS:")
        for kind in KINDS:
            group = [item for item in objects if item.kind == kind]
            if not group:
                continue
            print_adt_header(f"SEARCHING {len(group)} {KIND_LABELS[kind]}:")
            hits += search_objects(
                gateway,
                group,
                args.term,
                limit or None,
                _TableRows(),
                # Two schemas can hold an object of one name, so a run reading
                # more than one names each object's owner.
                label       = f"{schema}." if len(schemas) > 1 else "",
                failures    = failures,
                fatal_error = _is_database_connection_error,
            )
    _print_data_hits(hits, args.term)
    _print_not_searched(failures)
    # `0` only once every object was read: a partial answer says so.
    return 1 if failures else 0


def _print_not_searched(failures: Sequence[DataFailure]) -> None:
    """The objects the database refused, under the heading `search TERM` closes on.

    One line each, the object and the first line of the database's message,
    `V_BROKEN: ORA-04063: view "APP.V_BROKEN" has errors`.
    """
    if not failures:
        return
    print_adt_header("WARNING - NOT SEARCHED:")
    for failure in failures:
        message = str(failure.error).strip().splitlines()
        print(f"  {failure.table}: {message[0] if message else type(failure.error).__name__}")
    print()


def _print_data_hits(hits: Sequence[DataHit], term: str) -> None:
    """The hits grouped by object, `ROW | COLUMN | VALUE`, the value cut around TERM."""
    if not hits:
        print_adt_header("HITS (0):")
        print("  (none)")
        print()
        return
    print_adt_header(f"DATA HITS ({len(hits)}):")
    for table, group in groupby(hits, key=lambda hit: hit.table):
        rows = list(group)
        row_width = min(NAME_WIDTH, max(len(hit.row) for hit in rows))
        column_width = min(NAME_WIDTH, max(len(hit.column) for hit in rows))
        print()
        print(f"  {table}")
        for hit in rows:
            row = str(_trim(hit.row, row_width))
            column = str(_trim(hit.column, column_width))
            left = f"{HIT_INDENT}{row:<{row_width}} | {column:<{column_width}} | "
            print(left + _window(hit.value, term, MAX_LINE - len(left)))
    print()


__all__ = [name for name in globals() if not name.startswith("__")]
