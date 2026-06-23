from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from adt_ai.cli_constants import (
    ConfigLoader,
    ConnectionLoader,
    DependencyIndexRequest,
    DependencyIndexRunner,
    DependencyStore,
    GatewayFactory,
    OracleGateway,
    QueryGateway,
    print_adt_header,
    print_adt_table,
)
from adt_ai.cli_context import (
    DebugQueryGateway,
    _config_search_paths,
    _connection_file_candidates,
    _connection_search_paths,
    _print_connection_block,
    _repo_root,
    _wallet_roots,
)
from adt_ai.export_apex.inventory import ApexDiscovery
from adt_ai.progress import FixedWidthProgressPrinter

_NO_DEPENDENCY_INDEX_MESSAGE = (
    "No dependency database found. Run 'adt dependencies -refresh' to build it."
)


def _dependencies_argument_error(args: argparse.Namespace) -> str | None:
    """Reject ``-schema``/``-app`` outside ``-refresh`` and bad ``-app`` ids.

    Returned (non-``None``) by the dispatcher before the command runs, so misuse
    surfaces as a parser-style error screen — never a silently-accepted flag.
    """
    offenders = [
        flag
        for flag, present in (
            ("-schema", bool(args.schema)),
            ("-app", bool(args.app)),
            ("-force", bool(getattr(args, "force", False))),
        )
        if present
    ]
    refresh_requested = args.refresh is not None
    if offenders and not refresh_requested:
        return f"{' / '.join(offenders)} can only be used with -refresh"
    for value in args.app or []:
        for part in str(value).split(","):
            part = part.strip()
            if part and not _is_app_id(part):
                return f"invalid APP_ID: {part}"
    return None


def _is_app_id(value: str) -> bool:
    try:
        int(value)
    except ValueError:
        return False
    return True


def _resolve_app_ids(raw: list[str] | None) -> list[int]:
    """Flatten repeated/comma-joined ``-app`` values into unique ids in order."""
    apps: list[int] = []
    for value in raw or []:
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            app_id = int(part)
            if app_id not in apps:
                apps.append(app_id)
    return apps


def _resolve_refresh_names(raw: list[str] | None) -> list[str]:
    """Flatten repeated/comma-joined refresh names into unique uppercase values."""
    names: list[str] = []
    for value in raw or []:
        for part in str(value).split(","):
            part = part.strip().upper()
            if part and part not in names:
                names.append(part)
    return names


def _run_dependencies(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    # Human (table) output carries the generic banner/footer on stdout like every
    # other command; machine output (-format yaml/md) keeps stdout pure data and
    # sends the chrome to stderr so it stays pipeable.
    chrome = sys.stdout if args.format == "table" else sys.stderr
    print_adt_header("APEX DEPLOYMENT TOOL: DEPENDENCIES", file=chrome)

    root    = Path(args.root).expanduser().resolve()
    db_path = root / "config" / "dependencies.db"

    if args.refresh is not None:
        return _refresh_dependency_index(args, root, gateway_factory)

    if not db_path.exists():
        print(_NO_DEPENDENCY_INDEX_MESSAGE, file=sys.stderr)
        return 1

    with DependencyStore.open(db_path) as store:
        if args.uses:
            exit_code = _print_dependency_list(
                args.uses, store.uses(args.uses), "uses", args.format
            )
        elif args.used_by:
            exit_code = _print_dependency_list(
                args.used_by, store.used_by(args.used_by), "used_by", args.format
            )
        elif args.impact:
            exit_code = _print_dependency_impact(
                args.impact,
                store.impact(args.impact),
                args.format,
                store.affected_columns(args.impact),
                store.apex_callers(args.impact),
            )
        elif args.tree:
            exit_code = _print_foreign_key_tree(
                args.tree,
                store.foreign_key_tree(args.tree),
                args.format,
            )
        elif args.unused:
            exit_code = _print_unused(store.unused(type=args.type), args.format)
        else:
            _print_dependencies_hint()
            exit_code = 0

    return exit_code


def _print_dependency_list(
    query: str,
    items: list[str],
    relation: str,
    output_format: str,
) -> int:
    if output_format == "yaml":
        print(yaml.safe_dump({"object": query, relation: items}, sort_keys=False).rstrip())
        return 0
    if output_format == "md":
        heading = "Uses" if relation == "uses" else "Used by"
        lines = [f"## {heading}: {query} ({len(items)})", ""]
        lines.extend(f"- {item}" for item in items)
        print("\n".join(lines))
        return 0
    heading = "USES" if relation == "uses" else "USED BY"
    print_adt_header(f"{heading}: {query} ({len(items)})")
    if items:
        print_adt_table([{"OBJECT": item} for item in items])
    else:
        print("  (none)")
    return 0


def _print_dependency_impact(
    query: str,
    items: list[tuple[str, int]],
    output_format: str,
    columns: list[dict[str, str]] | None = None,
    apex: list[dict[str, str]] | None = None,
) -> int:
    # Column lineage exists only when the index was refreshed with PL/Scope
    # data; every format omits the section entirely when there is none.
    columns = columns or []
    apex = apex or []
    if output_format == "yaml":
        payload = {
            "object": query,
            "impact": [{"object": node, "depth": depth} for node, depth in items],
        }
        if columns:
            payload["columns"] = [
                {
                    "view": row["view_name"],
                    "column": row["column_name"],
                    "source": f"{row['src_table']}.{row['src_column']}",
                }
                for row in columns
            ]
        if apex:
            payload["apex"] = [_apex_payload(row) for row in apex]
        print(yaml.safe_dump(payload, sort_keys=False).rstrip())
        return 0
    if output_format == "md":
        lines = [f"## Impact: {query} ({len(items)})", ""]
        lines.extend(f"- {node} (depth {depth})" for node, depth in items)
        if columns:
            lines.extend(["", f"## Affected columns ({len(columns)})", ""])
            lines.extend(
                f"- {row['view_name']}.{row['column_name']} "
                f"(from {row['src_table']}.{row['src_column']})"
                for row in columns
            )
        if apex:
            lines.extend(["", f"## APEX callers ({len(apex)})", ""])
            lines.extend(
                f"- APP {row['application_id']} page {row['page_id']}: "
                f"{row['component_name']} [{row['component_type']}] "
                f"{row['property_name']}={row['property_value']} -> {row['object']}"
                for row in apex
            )
        print("\n".join(lines))
        return 0
    print_adt_header(f"IMPACT: {query} ({len(items)})")
    if items:
        print_adt_table([{"OBJECT": node, "DEPTH": depth} for node, depth in items])
    else:
        print("  (none)")
    if columns:
        print_adt_header(f"AFFECTED COLUMNS ({len(columns)})")
        print_adt_table(
            [
                {
                    "VIEW": row["view_name"],
                    "COLUMN": row["column_name"],
                    "SOURCE": f"{row['src_table']}.{row['src_column']}",
                }
                for row in columns
            ]
        )
    if apex:
        print_adt_header(f"APEX CALLERS ({len(apex)})")
        print_adt_table(
            [
                {
                    "APP": row["application_id"],
                    "APPLICATION": row["application_name"],
                    "PAGE": row["page_id"],
                    "COMPONENT": row["component_name"],
                    "TYPE": row["component_type"],
                    "PROPERTY": row["property_name"],
                    "OBJECT": row["object"],
                    "COLUMN": row["column_name"],
                }
                for row in apex
            ]
        )
    return 0


def _apex_payload(row: dict[str, str]) -> dict[str, str]:
    payload = {
        "app": row["application_id"],
        "workspace": row["workspace"],
        "application": row["application_name"],
        "page": row["page_id"],
        "component": row["component_name"],
        "component_type": row["component_type"],
        "property": row["property_name"],
        "value": row["property_value"],
        "object": row["object"],
    }
    if row["column_name"]:
        payload["column"] = row["column_name"]
    if row["source"]:
        payload["source"] = row["source"]
    return payload


def _print_foreign_key_tree(
    query: str,
    tree: dict[str, list[dict[str, str]]],
    output_format: str,
) -> int:
    references = tree["references"]
    dependencies = tree["dependencies"]
    if output_format == "yaml":
        print(
            yaml.safe_dump(
                {
                    "constraint": query,
                    "references": references,
                    "dependencies": dependencies,
                },
                sort_keys=False,
            ).rstrip()
        )
        return 0
    if output_format == "md":
        lines = [f"## References: {query} ({len(references)})", ""]
        lines.extend(_foreign_key_tree_markdown_rows(references))
        if dependencies:
            lines.extend(["", f"## Dependencies: {query} ({len(dependencies)})", ""])
            lines.extend(_foreign_key_tree_markdown_rows(dependencies))
        print("\n".join(lines))
        return 0

    print_adt_header(f"REFERENCES: {query} ({len(references)})")
    if references:
        print_adt_table(_foreign_key_tree_table_rows(references))
    else:
        print("  (none)")
    if dependencies:
        print_adt_header(f"DEPENDENCIES: {query} ({len(dependencies)})")
        print_adt_table(_foreign_key_tree_table_rows(dependencies))
    return 0


def _foreign_key_tree_table_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {
            "TABLE NAME": row["table_name"],
            "COLUMN NAME": row["column_name"],
            "CONSTRAINT NAME": row["constraint_name"],
            "TYPE": row["type"],
        }
        for row in rows
    ]


def _foreign_key_tree_markdown_rows(rows: list[dict[str, str]]) -> list[str]:
    if not rows:
        return ["- (none)"]
    return [
        "- "
        f"{row['table_name']}.{row['column_name']} "
        f"`{row['constraint_name']}` ({row['type']})"
        for row in rows
    ]


def _print_unused(
    items: list[str],
    output_format: str,
) -> int:
    if output_format == "yaml":
        print(yaml.safe_dump({"unused": items}, sort_keys=False).rstrip())
        return 0
    if output_format == "md":
        lines = [f"## Unused ({len(items)})", ""]
        lines.extend(f"- {item}" for item in items)
        print("\n".join(lines))
        return 0
    print_adt_header(f"UNUSED ({len(items)})")
    if items:
        print_adt_table([{"OBJECT": item} for item in items])
    else:
        print("  (none)")
    return 0


def _print_dependencies_hint() -> None:
    print("Specify a query: -uses OBJ, -used-by OBJ, -impact OBJ, -tree CONSTRAINT, or -unused.")
    print("Use -refresh (with -schema and/or -app) to rebuild the database from the dictionary.")


def _refresh_dependency_index(
    args: argparse.Namespace,
    root: Path,
    gateway_factory: GatewayFactory | None,
) -> int:
    repo_root = _repo_root()
    config_search_paths = _config_search_paths(args.config_dir, root, repo_root)
    config = ConfigLoader(config_search_paths).load().data
    connection_search_paths = _connection_search_paths(config, args.config_dir, root, repo_root)
    connection_files = _connection_file_candidates(config, args.config_dir, root, repo_root)
    connections = ConnectionLoader(
        connection_search_paths,
        wallet_roots = _wallet_roots(config, root, repo_root, connection_search_paths),
        key          = getattr(args, "key", None),
    ).load(candidates=connection_files)
    environment = args.env or connections.default_environment
    # Two independent refresh axes (both only with -refresh): -schema drives the
    # USER_* mirror, -app the APEX_* mirror. Bare -refresh keeps the old default
    # (every default schema); -app alone refreshes only the APEX axis.
    refresh_names = _resolve_refresh_names(args.refresh)
    if args.schema:
        schemas = connections.expand_schemas(args.schema, environment=environment)
    elif args.app and not refresh_names:
        schemas = []
    else:
        schemas = connections.default_schemas(environment)

    apps = _resolve_app_ids(args.app)

    # APEX_* views are pulled over one schema's connection; default to the first
    # refreshed schema, else the environment's first default schema.
    app_schema: str | None = schemas[0] if schemas else None
    if apps and app_schema is None:
        defaults = connections.default_schemas(environment)
        app_schema = defaults[0] if defaults else None

    connect_schemas = list(schemas)
    if app_schema and app_schema not in connect_schemas:
        connect_schemas.append(app_schema)

    schema_connections = {
        schema: connections.resolve(environment=environment, schema=schema)
        for schema in connect_schemas
    }

    debug = getattr(args, "debug", False)
    gateway_cache: dict[str, QueryGateway] = {}

    def default_gateway_factory(schema: str) -> QueryGateway:
        return OracleGateway(schema_connections[schema])

    base_gateway_factory = gateway_factory or default_gateway_factory

    def selected_gateway_factory(schema: str) -> QueryGateway:
        if schema not in gateway_cache:
            gateway = base_gateway_factory(schema)
            gateway_cache[schema] = DebugQueryGateway(gateway) if debug else gateway
        return gateway_cache[schema]

    apex_versions: dict[str, str] = {}
    for schema in connect_schemas:
        versions = _print_connection_block(
            selected_gateway_factory(schema), schema_connections[schema], debug=debug
        )
        if versions.get("APEX"):
            apex_versions[schema] = versions["APEX"]

    scope_bits: list[str] = []
    if schemas:
        scope_bits.append(", ".join(schemas))
    if apps:
        app_labels = _apex_app_labels(
            selected_gateway_factory(app_schema) if app_schema else None,
            app_schema,
            apps,
        )
        if schemas:
            scope_bits.append(", ".join(f"APEX APP {label}" for label in app_labels))
        elif len(app_labels) == 1:
            print_adt_header(f"REFRESHING APEX APP: {app_labels[0]}")
        else:
            print_adt_header(f"REFRESHING APEX APPS: {', '.join(app_labels)}")
    if schemas:
        print_adt_header(f"REFRESHING DEPENDENCY DATABASE: {' | '.join(scope_bits)}")
    silent = getattr(args, "silent", False)
    DependencyIndexRunner(selected_gateway_factory).refresh(
        DependencyIndexRequest(
            root       = root,
            schemas    = schemas,
            config     = config,
            apps       = apps,
            app_schema = app_schema,
            force      = getattr(args, "force", False),
            progress   = None if silent else FixedWidthProgressPrinter(),
            apex_versions = apex_versions,
            refresh_names = refresh_names,
        )
    )
    print()
    return 0


def _apex_app_labels(
    gateway: QueryGateway | None,
    owner: str | None,
    apps: list[int],
) -> list[str]:
    labels = {app: str(app) for app in apps}
    if gateway is None or owner is None:
        return [labels[app] for app in apps]
    discovered = ApexDiscovery(gateway).applications(owner=owner, app_ids=apps)
    for application in discovered:
        alias = application.app_alias or application.app_name or str(application.app_id)
        labels[application.app_id] = f"{application.app_id}/{alias.upper()}"
    return [labels[app] for app in apps]

__all__ = [name for name in globals() if not name.startswith("__")]
