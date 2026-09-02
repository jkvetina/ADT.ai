"""The read half of `DependencyStore` (ADT #512).

Split out of `store.py` when `#510` pushed it past the 20 KB context guard
(`tests/contracts/test_context_file_size.py`), the same guard and the same answer
`#367`, `#276`, `#430`, `#494` and `#499` reached before it: a module that crosses
it is split, never registered as debt in `LEGACY_CONTEXT_SIZE_EXCEPTIONS`.

The seam is the one the class already carried in its own source, a `# writers`
band over the refresh-and-record half and a `# queries` band over this one. It is
a MIXIN rather than a second class because the two halves share one open
connection and one `_resolve_types`, so a caller holding a store still holds every
method it held before and not one import anywhere moved.

Nothing here writes. A method that mutates the mirror belongs on the other side of
the seam, which is what makes the split worth keeping rather than merely tidy.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from adt_ai.dependencies import apex_pages, queries, scope
from adt_ai.dependencies import edges as _edges
from adt_ai.dependencies.classify import split_node
from adt_ai.dependencies.foreign_key_tree import foreign_key_tree as _foreign_key_tree
from adt_ai.dependencies.owner_case import owner_params as _owner_params

#: How deep `impact` walks before it stops. Re-exported by `store` so the one
#: importer outside this package (`cli/commands_dependencies.py`) is unmoved.
DEFAULT_MAX_DEPTH = 20


class DependencyQueries:
    """Every read `DependencyStore` answers, over `self.connection`."""

    #: Declared, never assigned here: the mixin reads the one open connection its
    #: host sets in `DependencyStore.__init__`, and a checker reading this half on
    #: its own has no other way to know the attribute exists.
    connection: Any

    def _resolve_types(self, type_: str, name: str, owner_params: list[str]) -> list[str]:
        """Return object types to query against.

        When ``type_`` is non-empty (caller passed ``TYPE.NAME``), it is returned
        as-is. When it is empty (bare name), the distinct ``OBJECT_TYPE`` values
        from ``USER_OBJECTS`` are looked up so the caller's query can match real
        Oracle type strings instead of searching for an empty string that never
        exists. Falls back to ``[""]`` when the name is not found, which lets the
        downstream query return an empty result set rather than erroring.
        """
        if type_:
            return [type_]
        rows = self.connection.execute(
            queries.resolve_object_types_query(len(owner_params)),
            (name, *owner_params),
        ).fetchall()
        resolved = [row["OBJECT_TYPE"] for row in rows]
        return resolved if resolved else [type_]

    def uses(self, node: str, owners: Iterable[str] | None = None) -> list[str]:
        """Direct internal objects ``node`` depends on (``TYPE.NAME`` strings).

        ``owners`` (the query-mode ``-schema`` filter) narrows the dependent
        side (``d.OWNER``) to disambiguate which owner's ``node`` is queried.
        Bare names (no ``TYPE.`` prefix) are resolved from ``USER_OBJECTS``.
        """
        type_, name = split_node(node)
        owner_params = _owner_params(owners)
        result: set[str] = set()
        for t in self._resolve_types(type_, name, owner_params):
            rows = self.connection.execute(
                queries.dependency_uses_query(len(owner_params)),
                (t, name, *owner_params),
            ).fetchall()
            result.update(f"{row['t']}.{row['n']}" for row in rows)
        return sorted(result)

    def used_by(self, node: str, owners: Iterable[str] | None = None) -> list[str]:
        """Direct internal objects that depend on ``node``.

        ``owners`` narrows the referenced side (``d.REFERENCED_OWNER``), the
        owner of the queried object, to disambiguate same-named objects.
        Bare names (no ``TYPE.`` prefix) are resolved from ``USER_OBJECTS``.
        """
        type_, name = split_node(node)
        owner_params = _owner_params(owners)
        result: set[str] = set()
        for t in self._resolve_types(type_, name, owner_params):
            rows = self.connection.execute(
                queries.dependency_used_by_query(len(owner_params)),
                (t, name, *owner_params),
            ).fetchall()
            result.update(f"{row['t']}.{row['n']}" for row in rows)
        return sorted(result)

    def impact(
        self,
        node: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
        owners: Iterable[str] | None = None,
    ) -> list[tuple[str, int]]:
        """Transitive reverse closure: every object broken by changing ``node``.

        Walks ``USER_DEPENDENCIES`` backward (``REFERENCED_* → dependent``) with a
        depth cap; ``UNION`` plus the cap make cycles terminate. ``MIN(depth)``
        per node, self excluded, ordered by depth then node.

        ``owners`` constrains only the seed (the first hop off ``node``), so it
        picks which owner's object roots the walk without truncating its reach.
        Bare names (no ``TYPE.`` prefix) are resolved from ``USER_OBJECTS``.
        """
        type_, name = split_node(node)
        owner_params = _owner_params(owners)
        depths: dict[str, int] = {}
        for t in self._resolve_types(type_, name, owner_params):
            rows = self.connection.execute(
                queries.dependency_impact_query(len(owner_params)),
                (t, name, max_depth, *owner_params, t, name),
            ).fetchall()
            for row in rows:
                key = f"{row['t']}.{row['n']}"
                if key not in depths or row["d"] < depths[key]:
                    depths[key] = row["d"]
        result = list(depths.items())
        result.sort(key=lambda item: (item[1], item[0]))
        return result

    def constraints(self, table: str | None = None) -> list[dict[str, Any]]:
        """Reconstruct the flattened constraint shape from the raw mirrors.

        Replaces the Oracle ``LISTAGG`` the old query baked into YAML: a
        constraint's columns come from ``USER_CONS_COLUMNS`` (ordered by
        ``POSITION``); a foreign key's referenced table/columns resolve across
        ``R_OWNER + R_CONSTRAINT_NAME`` (cross-owner safe). Each row is
        ``{tbl, kind, cols, ref_tbl, ref_cols}``. Ordered by table, kind, name.
        """
        columns_by_constraint: dict[tuple[Any, Any], list[str]] = {}
        for row in self.connection.execute(queries.CONSTRAINT_COLUMNS_QUERY).fetchall():
            key = (row["OWNER"], row["CONSTRAINT_NAME"])
            columns_by_constraint.setdefault(key, []).append(row["COLUMN_NAME"])

        table_by_constraint: dict[tuple[Any, Any], Any] = {
            (row["OWNER"], row["CONSTRAINT_NAME"]): row["TABLE_NAME"]
            for row in self.connection.execute(queries.CONSTRAINT_TABLES_QUERY).fetchall()
        }

        sql = queries.CONSTRAINTS_QUERY
        params: list[Any] = []
        if table is not None:
            sql += queries.CONSTRAINTS_TABLE_FILTER_CLAUSE
            params.append(table)
        sql += queries.CONSTRAINTS_ORDER_CLAUSE

        result: list[dict[str, Any]] = []
        for row in self.connection.execute(sql, params).fetchall():
            cols = columns_by_constraint.get((row["OWNER"], row["CONSTRAINT_NAME"]), [])
            if row["CONSTRAINT_TYPE"] == "R":
                ref_key = (row["R_OWNER"], row["R_CONSTRAINT_NAME"])
                ref_tbl = table_by_constraint.get(ref_key)
                ref_cols = columns_by_constraint.get(ref_key, [])
            else:
                ref_tbl = None
                ref_cols = []
            result.append(
                {
                    "tbl": row["TABLE_NAME"],
                    "kind": row["CONSTRAINT_TYPE"],
                    "cols": ", ".join(cols),
                    "ref_tbl": ref_tbl,
                    "ref_cols": ", ".join(ref_cols) if ref_cols else None,
                }
            )
        return result

    def foreign_key_tree(self, constraint_name: str) -> dict[str, list[dict[str, Any]]]:
        """Return FK cascade rows around ``constraint_name`` sorted by traversal path."""
        return _foreign_key_tree(self.connection, constraint_name)

    def affected_columns(self, node: str) -> list[dict[str, Any]]:
        """View columns whose lineage traces to ``node`` (PL/Scope-derived).

        Empty unless the schema was refreshed with PL/Scope on. Reconstructs the
        old ``columns.yaml`` lineage from raw ``USER_IDENTIFIERS`` /
        ``USER_STATEMENTS`` at query time, then keeps rows sourced from ``node``.
        """
        identifiers = self.connection.execute(queries.USER_IDENTIFIERS_ALL_QUERY).fetchall()
        statements = self.connection.execute(queries.USER_STATEMENTS_ALL_QUERY).fetchall()
        if not identifiers and not statements:
            return []
        _, name = split_node(node)
        refs = scope.parse(identifiers, statements)
        document = scope.columns_doc(refs, self.uses_edges())
        return [row for row in scope.column_rows(document) if row["src_table"] == name]

    def apex_page_components(
        self,
        app_id: int,
        explicit_ids: tuple[int, ...],
        ranges: tuple[tuple[int, int | None], ...],
    ) -> list[dict[str, Any]]:
        """Distinct APEX components recorded against selected page ids."""
        return apex_pages.apex_page_components(self.connection, app_id, explicit_ids, ranges)

    def apex_page_db_objects(
        self,
        app_id: int,
        explicit_ids: tuple[int, ...],
        ranges: tuple[tuple[int, int | None], ...],
    ) -> list[dict[str, Any]]:
        """Distinct DB objects recorded against selected APEX page ids."""
        return apex_pages.apex_page_db_objects(self.connection, app_id, explicit_ids, ranges)

    def apex_callers(self, node: str) -> list[dict[str, Any]]:
        """APEX app/page/component properties that depend on ``node``.

        The primary source is the ``APEX_USED_DB_OBJECTS`` mirror joined to its
        component properties. For table impacts, PL/Scope-derived view-column
        lineage widens the target set to APEX-rendered views and annotates rows
        whose component property value equals the affected view column.
        """
        type_, name = split_node(node)
        targets: dict[tuple[str, str], list[dict[str, Any] | None]] = {
            (type_, name): [None],
        }
        for column in self.affected_columns(node):
            targets.setdefault(("VIEW", column["view_name"]), []).append(column)

        rows = self.connection.execute(queries.APEX_CALLERS_QUERY).fetchall()

        result: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            object_type = row["USED_DB_OBJECT_TYPE"]
            object_name = row["USED_DB_OBJECT_NAME"]
            lineage_rows = targets.get((object_type, object_name))
            if not lineage_rows and not object_type:
                matches = [
                    (key, value)
                    for key, value in targets.items()
                    if key[1] == object_name
                ]
                if len(matches) == 1:
                    object_type = matches[0][0][0]
                    lineage_rows = matches[0][1]
            if not lineage_rows:
                continue
            for lineage in lineage_rows:
                column_name = None
                source = None
                if lineage is not None:
                    column_name = lineage["column_name"]
                    source = f"{lineage['src_table']}.{lineage['src_column']}"
                    property_value = row["PROPERTY_VALUE"]
                    if property_value and str(property_value).strip().upper() != column_name:
                        continue
                item = {
                    "application_id": row["APPLICATION_ID"],
                    "workspace": row["WORKSPACE"],
                    "page_id": row["PAGE_ID"],
                    "component_id": row["COMPONENT_ID"],
                    "component_name": row["COMPONENT_NAME"],
                    "component_type": row["COMPONENT_TYPE"],
                    "property_name": row["PROPERTY_NAME"],
                    "property_value": row["PROPERTY_VALUE"],
                    "object": f"{object_type}.{object_name}",
                    "column_name": column_name,
                    "source": source,
                }
                key = tuple(item.values())
                if key not in seen:
                    result.append(item)
                    seen.add(key)
        return result

    def uses_edges(self) -> dict[str, list[str]]:
        """Forward internal uses-map (``{node: [referenced…]}``) for lineage."""
        return _edges.uses_edges(self.connection)

    def foreign_key_edges(self) -> dict[str, list[str]]:
        """Forward FK map (``{TABLE.child: [TABLE.parent…]}``), see `edges`."""
        return _edges.foreign_key_edges(self.connection)
