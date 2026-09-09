"""Output rendering for the ``dependencies`` CLI query modes.

Pure formatting helpers split out of ``cli_commands_dependencies``, no
argparse, gateway, or store coupling. Each printer renders one query result
in the shared table/yaml/md contract: table mode prints human chrome on
stdout, machine formats keep stdout pure data.
"""

from __future__ import annotations

from collections.abc import Sequence

import yaml

from adt_ai.cli.constants import print_adt_header, print_adt_table
from adt_ai.dependencies.classify import split_object_row
from adt_ai.patch.apex_scan import ApexScanReport


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


def _scan_summary(report: ApexScanReport) -> str:
    """The right-hand cell of a scan row, in the shape `patch -deploy` prints."""
    if report.findings:
        return f"{len(report.findings)} error(s) in {report.analyzed} fragments"
    return f"{report.analyzed} fragments, no errors"


def _scan_scope(report: ApexScanReport) -> str:
    """What one report is about, as the row's left-hand cell.

    `APP 100` for an application-wide scan and `APP 100 PAGE 12` for a
    page-scoped one (ADT #751). The scope is on the row rather than announced
    once above the section because a run scans several pages of several
    applications, and every verdict under it answers a different question.
    """
    if report.page_id is None:
        return f"APP {report.app_id}"
    return f"APP {report.app_id} PAGE {report.page_id}"


def _scan_yaml_payload(report: ApexScanReport) -> dict[str, object]:
    payload: dict[str, object] = {"app": report.app_id}
    if report.page_id is not None:
        payload["page_scanned"] = report.page_id
    payload["status"] = report.status
    payload["analyzed"] = report.analyzed
    if report.reason:
        payload["reason"] = report.reason
    payload["findings"] = [
        {
            "page": finding.page_id,
            "component_type": finding.component_type,
            "component_name": finding.component_name,
            "property": finding.property_name,
            "error": " ".join(str(finding.error_message or "").split()),
        }
        for finding in report.findings
    ]
    return payload


def _print_component_scans(
    reports: Sequence[ApexScanReport],
    output_format: str,
) -> int:
    """`SCANNING APPLICATIONS:`, what the on-demand component scan found (`#751`).

    A clean application still prints its row, for the reason the post-deploy
    scan prints one (`#676`): silence and a verification that never happened
    read identically, and the second is what this mode exists to rule out.

    One row per SCAN, which under `-page` means one row per page rather than one
    per application. Nothing is filtered here any more: each report is already
    the answer to the question its own scan asked, so a row that reads `SUCCESS`
    is the database's verdict on that scope rather than arithmetic over a wider
    one.

    Findings are stanza lines rather than a table column. An `ORA-` message in a
    cell destroys the layout at 80 columns, which is the same call
    `_print_apex_scans` and `_print_deployment_errors` already make.
    """
    if output_format == "yaml":
        print(
            yaml.safe_dump(
                {"scan": [_scan_yaml_payload(report) for report in reports]},
                sort_keys=False,
            ).rstrip()
        )
    elif output_format == "md":
        lines: list[str] = []
        for report in reports:
            lines.append(f"## Scan: {_scan_scope(report)} ({report.status})")
            lines.append("")
            lines.append(f"- {_scan_summary(report)}")
            if report.reason:
                lines.append(f"- {report.reason}")
            lines.extend(f"- {finding.line()}" for finding in report.findings)
            lines.append("")
        print("\n".join(lines).rstrip())
    else:
        # The `SCANNING APPLICATIONS:` header belongs to the caller, printed
        # before the scan runs so it announces the reads under it. Printing it
        # here as well is how the section came to open twice on one screen.
        for report in reports:
            print(f"  {_scan_scope(report)} | {report.status} | {_scan_summary(report)}")
            # The reason under the row for every outcome that is not a plain
            # success: `FAILED`, `EMPTY` and `UNSUPPORTED` all print a row that
            # looks quiet, and this line says which of the three it is.
            if report.reason:
                print(f"    {report.reason}")
            for finding in report.findings:
                print(f"    {finding.line()}")
        print()
    return 1 if any(report.failed for report in reports) else 0


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


__all__ = [name for name in globals() if not name.startswith("__")]
