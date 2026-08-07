"""Output rendering for the ``dependencies`` CLI query modes.

Pure formatting helpers split out of ``cli_commands_dependencies`` — no
argparse, gateway, or store coupling. Each printer renders one query result
in the shared table/yaml/md contract: table mode prints human chrome on
stdout, machine formats keep stdout pure data.
"""

from __future__ import annotations

import yaml

from adt_ai.cli.constants import print_adt_header, print_adt_table
from adt_ai.dependencies.classify import split_object_row


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
    print_adt_header(f"{heading} {query} ({len(items)}):")
    if items:
        print_adt_table([split_object_row(item) for item in items])
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
    print_adt_header(f"IMPACT OF {query} ({len(items)}):")
    if items:
        print_adt_table(
            [{**split_object_row(node), "DEPTH": depth} for node, depth in items]
        )
    else:
        print("  (none)")
    if columns:
        print_adt_header(f"AFFECTED COLUMNS ({len(columns)}):")
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
        print_adt_header(f"APEX CALLERS ({len(apex)}):")
        print_adt_table(
            [
                {
                    "APP": row["application_id"],
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

    print_adt_header(f"REFERENCES TO {query} ({len(references)}):")
    if references:
        print_adt_table(_foreign_key_tree_table_rows(references))
    else:
        print("  (none)")
    if dependencies:
        print_adt_header(f"DEPENDENCIES OF {query} ({len(dependencies)}):")
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


def _print_dependency_age(
    rows: list[dict[str, str]],
    output_format: str,
) -> int:
    """Render per-scope last-refresh stamps (offline ``-age`` mode)."""
    if output_format == "yaml":
        print(yaml.safe_dump({"age": rows}, sort_keys=False).rstrip())
        return 0
    if output_format == "md":
        lines = [f"## Age ({len(rows)})", ""]
        lines.extend(
            f"- {row['type']} {row['scope']}: {row['last_refresh']}" for row in rows
        )
        print("\n".join(lines))
        return 0
    print_adt_header(f"DEPENDENCY AGE ({len(rows)}):")
    if rows:
        print_adt_table(
            [
                {
                    "SCOPE TYPE": row["type"].upper(),
                    "SCOPE": row["scope"],
                    "LAST REFRESH": row["last_refresh"],
                }
                for row in rows
            ]
        )
    else:
        print("  (none)")
    return 0


def _print_dependencies_hint() -> None:
    print("Specify a query: -from OBJ, -to OBJ, -impact OBJ, or -tree CONSTRAINT.")
    print(
        "Add -schema OWNER[,OWNER] to a query to disambiguate by owner "
        "(offline, all tracked owners if omitted)."
    )
    print("Use -age to list when each schema/app scope was last refreshed (offline).")
    print("Use -refresh (with -schema and/or -app) to rebuild the database from the dictionary.")

__all__ = [name for name in globals() if not name.startswith("__")]
