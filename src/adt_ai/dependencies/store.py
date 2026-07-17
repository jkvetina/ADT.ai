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
    DROP_SCHEMA,
    LEGACY_TABLES,
    SCHEMA,
    SCHEMA_VERSION,
    USER_TABLES,
)
from adt_ai.dependencies.write import insert_rows as _insert_rows

DEFAULT_MAX_DEPTH = 20


def _meta_table_exists(connection: Any) -> bool:
    row = connection.execute(queries.META_TABLE_EXISTS_QUERY).fetchone()
    return row is not None


def _owner_params(owners: Iterable[str] | None) -> list[str]:
    """Normalize the query-mode ``-schema`` owner filter to deduped uppercase
    params. Empty/whitespace entries (and ``None``) collapse to ``[]``, so an
    empty owner filter behaves exactly like an absent one."""
    params: list[str] = []
    for owner in owners or ():
        owner = str(owner).strip().upper()
        if owner and owner not in params:
            params.append(owner)
    return params


def build_db(db_path: str | Path) -> DependencyStore:
    """Create a fresh database (replacing any existing file) and return its store."""
    if not (isinstance(db_path, str) and db_path == ":memory:"):
        Path(db_path).unlink(missing_ok=True)
    return DependencyStore.open(db_path)


class DependencyStore:
    """Query + write API over the raw-mirror dependency database."""

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self.schema_was_wiped: bool = False

    @classmethod
    def open(cls, db_path: str | Path, *, rebuild: bool = False) -> DependencyStore:
        """Open, creating the schema when absent.

        Pass ``rebuild=True`` (refresh path only) to wipe and recreate all
        tables when the stored schema version doesn't match SCHEMA_VERSION.
        Query-mode callers leave this False so a version mismatch never
        silently destroys data mid-query.
        """
        connection = connect(db_path)
        version_sql = (
            queries.META_SCHEMA_VERSION_QUERY
            if _meta_table_exists(connection) else
            queries.META_SCHEMA_VERSION_NULL_QUERY
        )
        stored = connection.execute(version_sql).fetchone()
        wiped = rebuild and (stored is None or stored["value"] != SCHEMA_VERSION)
        if wiped:
            connection.executescript(DROP_SCHEMA)
        connection.executescript(SCHEMA)
        for _legacy in LEGACY_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS [{_legacy}]")
        connection.execute(queries.META_UPSERT_SCHEMA_VERSION, (SCHEMA_VERSION,))
        connection.commit()
        instance = cls(connection)
        instance.schema_was_wiped = wiped
        return instance

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

    def record_refresh(self, scope_type: str, scope_name: str, timestamp: str) -> None:
        """Stamp one refreshed scope's completion time into ``_meta``.

        ``scope_type`` is ``"schema"`` or ``"app"``; the key is
        ``last_refresh:<type>:<name>`` so re-refreshing a scope replaces its
        stamp rather than duplicating it.
        """
        key = f"{queries.META_LAST_REFRESH_PREFIX}{scope_type}:{scope_name}"
        with self.connection:
            self.connection.execute(queries.META_UPSERT_QUERY, (key, timestamp))

    def last_refreshes(self) -> list[dict[str, str]]:
        """Per-scope last-refresh stamps, schemas first then apps (offline).

        Reads the ``last_refresh:*`` rows from ``_meta`` and splits each key back
        into ``{type, scope, last_refresh}``. Backs the ``-age`` query mode, so
        an agent can check staleness without the file-mtime heuristic.
        """
        rows = self.connection.execute(queries.META_LAST_REFRESH_QUERY).fetchall()
        parsed: list[dict[str, str]] = []
        for row in rows:
            _, scope_type, scope_name = row["key"].split(":", 2)
            parsed.append(
                {"type": scope_type, "scope": scope_name, "last_refresh": row["value"]}
            )
        schemas = sorted(
            (item for item in parsed if item["type"] == "schema"),
            key=lambda item: item["scope"],
        )
        apps = sorted(
            (item for item in parsed if item["type"] == "app"),
            key=lambda item: int(item["scope"]) if item["scope"].isdigit() else item["scope"],
        )
        others = [item for item in parsed if item["type"] not in ("schema", "app")]
        return schemas + apps + others

    # queries

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

        ``owners`` narrows the referenced side (``d.REFERENCED_OWNER``) — the
        owner of the queried object — to disambiguate same-named objects.
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
