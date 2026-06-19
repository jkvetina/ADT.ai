from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _constraint_column_names,
    _normalize_sql_identifier,
)


def _format_table_suffix(suffix: str, context: NormalizationContext) -> list[str]:
    trailing_lines = _trailing_table_statements(suffix)
    cluster_match = re.search(
        r"\bCLUSTER\s+(?P<cluster>.*?)(?=;|\bCREATE\b|\bALTER\b|$)",
        suffix,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if cluster_match:
        cluster = _format_cluster_clause(cluster_match.group("cluster"), context)
        return [f") CLUSTER {cluster};", *trailing_lines]

    if re.search(r"\bON\s+COMMIT\s+DELETE\s+ROWS\b", suffix, flags=re.IGNORECASE):
        return ["ON COMMIT DELETE ROWS;", *trailing_lines]
    if re.search(r"\bON\s+COMMIT\s+PRESERVE\s+ROWS\b", suffix, flags=re.IGNORECASE):
        return ["ON COMMIT PRESERVE ROWS;", *trailing_lines]
    if re.search(r"\bUSAGE\s+QUEUE\b", suffix, flags=re.IGNORECASE):
        return [") USAGE QUEUE;", *trailing_lines]

    inmemory_lines = _format_inmemory_suffix(suffix)
    if inmemory_lines:
        return [*inmemory_lines, *trailing_lines]

    match = re.search(
        r"PARTITION BY RANGE \(([^)]+)\)\s+INTERVAL\s+\((NUMTODSINTERVAL\([^)]+\))\)",
        suffix,
        flags=re.IGNORECASE,
    )
    if not match:
        return [";", *trailing_lines]

    column_name, interval = match.groups()
    partition_name = _extract_partition_name(suffix)
    return [
        f"PARTITION BY RANGE ({column_name}) INTERVAL({interval}) (",
        f"    PARTITION {partition_name} VALUES()",
        ");",
        *trailing_lines,
    ]

def _format_inmemory_suffix(suffix: str) -> list[str]:
    match = re.search(
        r"\bINMEMORY\b(?P<body>.*?)(?=;|\bCREATE\b|\bALTER\b|$)",
        suffix,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return []

    raw = suffix[match.start() : match.end()].strip()
    lines = [re.sub(r"\s+", " ", line.strip()) for line in raw.splitlines() if line.strip()]
    if lines:
        lines[-1] = lines[-1].rstrip(";") + ";"
    return lines

def _format_cluster_clause(cluster: str, context: NormalizationContext) -> str:
    cluster = re.sub(r"\s+", " ", cluster).strip()
    match = re.fullmatch(
        r"(?P<name>(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)\.(?:\"[^\"]+\"|[A-Za-z0-9_$#]+)|"
        r"\"[^\"]+\"|[A-Za-z0-9_$#]+)\s*(?:\((?P<columns>.*)\))?",
        cluster,
        flags=re.IGNORECASE,
    )
    if not match:
        return cluster

    name = _normalize_sql_identifier(match.group("name"), context)
    columns = match.group("columns")
    if columns is None:
        return name
    return f"{name}({', '.join(_constraint_column_names(columns))})"

def _trailing_table_statements(suffix: str) -> list[str]:
    match = re.search(r"\b(?:CREATE|ALTER)\b", suffix, flags=re.IGNORECASE)
    if not match:
        return []
    return [line.rstrip() for line in suffix[match.start():].strip().splitlines()]

def _extract_partition_name(suffix: str) -> str:
    match = re.search(r"\(\s*PARTITION\s+(\S+)", suffix, flags=re.IGNORECASE)
    if not match:
        return "p00"
    return match.group(1).strip('"').lower()
