"""Raw-mirror SQLite engine for the ``dependencies`` command."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from adt_ai.dependencies import apex_pages, queries, refresh, scope
from adt_ai.dependencies.classify import split_node
from adt_ai.dependencies.db import connect
from adt_ai.dependencies.foreign_key_tree import foreign_key_tree as _foreign_key_tree
from adt_ai.dependencies.schema import (
    APEX_TABLES,
    SCHEMA,
    USER_TABLES,
)
from adt_ai.dependencies.write import insert_rows as _insert_rows

DEFAULT_MAX_DEPTH = 20



def build_db(db_path: str | Path) -> DependencyStore:
    """Create a fresh database (replacing any existing file) and return its store."""
    if not (isinstance(db_path, str) and db_path == ":memory:"):
        Path(db_path).unlink(missing_ok=True)
    return DependencyStore.open(db_path)


class DependencyStore:
    """Query + write API over the raw-mirror dependency database."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection

    @classmethod
    def open(cls, db_path: str | Path) -> DependencyStore:
        """Open (creating the schema if absent) without wiping existing rows."""
        connection = connect(db_path)
        connection.executescript(SCHEMA)
        connection.commit()
        return cls(connection)

    # writers

    def refresh_schema(
        self,
        owner: str,
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> dict[str, int]:
        """Replace one owner's ``USER_*`` rows in a single transaction."""
        provided = {str(key).upper(): value for key, value in (tables or {}).items()}
        counts: dict[str, int] = {}
        with self.connection:
            for table in USER_TABLES:
                self.connection.execute(queries.delete_owner_rows_query(table), (owner,))
                counts[table] = _insert_rows(
                    self.connection, table, provided.get(table, ()), stamp={"OWNER": owner}
                )
        return counts

    def refresh_schema_incremental(
        self,
        owner: str,
        object_rows: Iterable[Mapping[str, Any]],
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Update one owner's ``USER_*`` rows without wiping unchanged objects."""
        return refresh.refresh_schema_incremental(
            self.connection, owner, object_rows, tables, force=force
        )

    def refresh_schema_deep(
        self,
        owner: str,
        object_rows: Iterable[Mapping[str, Any]],
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        *,
        object_names: Iterable[str],
    ) -> dict[str, int]:
        """Replace rows to/from named objects without wiping the whole owner."""
        return refresh.refresh_schema_deep(
            self.connection, owner, object_rows, tables, object_names=object_names
        )

    def schema_changed_objects(
        self,
        owner: str,
        object_rows: Iterable[Mapping[str, Any]],
        *,
        force: bool = False,
    ) -> list[tuple[str, str]]:
        """Return added or modified ``USER_OBJECTS`` keys for ``owner``."""
        return refresh.schema_changed_objects(self.connection, owner, object_rows, force=force)

    def refresh_app(
        self,
        app_id: int,
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
    ) -> dict[str, int]:
        """Replace one app's ``APEX_*`` rows in a single transaction."""
        provided = {str(key).upper(): value for key, value in (tables or {}).items()}
        counts: dict[str, int] = {}
        with self.connection:
            for table in APEX_TABLES:
                self.connection.execute(queries.delete_app_rows_query(table), (app_id,))
                counts[table] = _insert_rows(
                    self.connection,
                    table,
                    provided.get(table, ()),
                    stamp={"APPLICATION_ID": app_id},
                )
        return counts

    def refresh_app_incremental(
        self,
        app_id: int,
        tables: Mapping[str, Iterable[Mapping[str, Any]]] | None = None,
        *,
        force: bool = False,
    ) -> dict[str, int]:
        """Update one app's ``APEX_*`` rows without wiping unchanged rows."""
        return refresh.refresh_app_incremental(self.connection, app_id, tables, force=force)

    # queries

    def uses(self, node: str) -> list[str]:
        """Direct internal objects ``node`` depends on (``TYPE.NAME`` strings)."""
        type_, name = split_node(node)
        rows = self.connection.execute(
            queries.DEPENDENCY_USES_QUERY,
            (type_, name),
        ).fetchall()
        return sorted({f"{row['t']}.{row['n']}" for row in rows})

    def used_by(self, node: str) -> list[str]:
        """Direct internal objects that depend on ``node``."""
        type_, name = split_node(node)
        rows = self.connection.execute(
            queries.DEPENDENCY_USED_BY_QUERY,
            (type_, name),
        ).fetchall()
        return sorted({f"{row['t']}.{row['n']}" for row in rows})

    def impact(self, node: str, max_depth: int = DEFAULT_MAX_DEPTH) -> list[tuple[str, int]]:
        """Transitive reverse closure: every object broken by changing ``node``.

        Walks ``USER_DEPENDENCIES`` backward (``REFERENCED_* → dependent``) with a
        depth cap; ``UNION`` plus the cap make cycles terminate. ``MIN(depth)``
        per node, self excluded, ordered by depth then node.
        """
        type_, name = split_node(node)
        rows = self.connection.execute(
            queries.DEPENDENCY_IMPACT_QUERY,
            (type_, name, max_depth, type_, name),
        ).fetchall()
        result = [(f"{row['t']}.{row['n']}", row["d"]) for row in rows]
        result.sort(key=lambda item: (item[1], item[0]))
        return result

    def unused(
        self,
        type: str | None = None,
        exclude: Iterable[str] | None = None,
    ) -> list[str]:
        """Internal objects nothing depends on (no inbound ``USER_DEPENDENCIES``).

        Filtered to ``OBJECT_TYPE = type`` when given (case-insensitive);
        ``COLUMN`` returns nothing (no column-level objects). Entry points in
        ``exclude`` (full ``TYPE.NAME``, case-insensitive) are dropped even
        though nothing references them. Ordered by node.
        """
        sql = queries.UNUSED_OBJECTS_QUERY
        params: list[Any] = []
        if type is not None:
            sql += queries.UNUSED_OBJECTS_TYPE_FILTER_CLAUSE
            params.append(type.upper())
        rows = self.connection.execute(sql, params).fetchall()
        nodes = sorted({f"{row['t']}.{row['n']}" for row in rows})
        excluded = {item.upper() for item in (exclude or ())}
        if excluded:
            nodes = [node for node in nodes if node not in excluded]
        return nodes

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
        document = scope.columns_doc(refs, self._uses_edges())
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
                    "application_name": row["APPLICATION_NAME"],
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

    def dependency_alias(self) -> dict[str, list[str]]:
        """Internal-only uses map for ``config/db_dependencies.yaml``.

        ``{node: [internal referenced node…]}`` keyed by every internal object in
        ``USER_OBJECTS`` (so objects with no dependencies still appear and can be
        deploy-ordered) unioned with any internal dependent seen in
        ``USER_DEPENDENCIES``. Edges are owner-classified internal-only and
        de-duplicated/sorted for consumers that need deployment order.
        """
        edges = {node: sorted(set(refs)) for node, refs in self._uses_edges().items()}
        alias: dict[str, list[str]] = {}
        for row in self.connection.execute(queries.DEPENDENCY_ALIAS_OBJECTS_QUERY).fetchall():
            node = f"{row['t']}.{row['n']}"
            alias[node] = edges.get(node, [])
        for node, refs in edges.items():
            alias.setdefault(node, refs)
        return alias

    def _uses_edges(self) -> dict[str, list[str]]:
        """Forward internal uses-map (``{node: [referenced…]}``) for lineage."""
        rows = self.connection.execute(queries.USES_EDGES_QUERY).fetchall()
        edges: dict[str, list[str]] = {}
        for row in rows:
            edges.setdefault(f"{row['t']}.{row['n']}", []).append(f"{row['rt']}.{row['rn']}")
        return edges

    # --------------------------------------------------------------- lifecycle

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> DependencyStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
