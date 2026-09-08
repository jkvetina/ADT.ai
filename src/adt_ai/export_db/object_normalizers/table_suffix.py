from __future__ import annotations

import re

from adt_ai.export_db.normalizers import (
    NormalizationContext,
    _constraint_column_names,
    _matching_parenthesis_index,
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

    partition_lines = _format_partition_suffix(suffix)
    if partition_lines:
        return [*partition_lines, *trailing_lines]

    # Both are schema, and both used to fall through to a bare `;` (ADT #736).
    # Retention first because it is the clause an IMMUTABLE table is defined by;
    # a table can carry both, so they compose rather than short-circuit.
    tail = [*_retention_clause(suffix, context), *_annotations_clause(suffix)]
    if tail:
        tail[-1] += ";"
        return [*tail, *trailing_lines]

    return [";", *trailing_lines]

def _retention_clause(suffix: str, context: NormalizationContext) -> list[str]:
    """The `NO DROP UNTIL` / `NO DELETE UNTIL` / `VERSION` clauses of an immutable table.

    Dropping these does not export a table that looks different; it exports a
    MUTABLE table. Replaying such a file silently disarms the retention the
    original was created to guarantee, and the run exits 0 while doing it.

    The DDL is read first and the dictionary second. Under the exporter's own
    transform params `DBMS_METADATA` withholds these clauses -- they ride on
    `SEGMENT_ATTRIBUTES`, which ADT turns off to keep tablespaces and PCTFREE
    out of the repo -- so on a real export the suffix is empty here and
    `context.table_retention` is what carries them. A caller that does hand
    over the clauses still wins, which keeps the normalizer usable on DDL
    obtained any other way.
    """
    match = re.search(
        r"\bNO\s+(?:DROP|DELETE)\s+UNTIL\b"
        r".*?(?=;|\bANNOTATIONS\b|\bCREATE\b|\bALTER\b|$)",
        suffix,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return [re.sub(r"\s+", " ", match.group(0)).strip().rstrip(";")]
    if context.table_retention:
        return [context.table_retention]
    return []

def _annotations_clause(suffix: str) -> list[str]:
    """The TABLE-level `ANNOTATIONS(...)` clause, which the column-level pass never sees.

    `DBMS_METADATA` carries it after the closing parenthesis, so it reached this
    chain and nothing here claimed it. The parentheses are matched rather than
    regex-bounded because an annotation value may contain one.
    """
    match = re.search(r"\bANNOTATIONS\s*(?=\()", suffix, flags=re.IGNORECASE)
    if not match:
        return []
    open_index = suffix.find("(", match.end())
    close_index = _matching_parenthesis_index(suffix, open_index)
    if close_index is None:
        return []
    body = re.sub(r"\s+", " ", suffix[open_index : close_index + 1]).strip()
    return [f"ANNOTATIONS{body}"]

def _format_partition_suffix(suffix: str) -> list[str]:
    """The table's partitioning clause, folded for RANGE INTERVAL and kept as written otherwise.

    An interval-partitioned table grows its partitions itself, so exporting the
    seed alone round-trips. Every other scheme carries its partitions in the
    definition: a LIST partition's values, a plain RANGE partition's bounds and
    a HASH scheme's `PARTITIONS n` are all schema, and there is no VALUES clause
    in a HASH scheme to fold to in the first place. So they are preserved raw,
    the way the CLUSTER and INMEMORY branches above preserve theirs.

    Until ADT #662 anything but RANGE INTERVAL fell through to a bare `;` and the
    table exported as unpartitioned with exit 0.
    """
    interval_match = re.search(
        r"PARTITION BY RANGE \(([^)]+)\)\s+INTERVAL\s+\((NUMTODSINTERVAL\([^)]+\))\)",
        suffix,
        flags=re.IGNORECASE,
    )
    if interval_match:
        column_name, interval = interval_match.groups()
        partition_name = _extract_partition_name(suffix)
        return [
            f"PARTITION BY RANGE ({column_name}) INTERVAL({interval}) (",
            f"    PARTITION {partition_name} VALUES()",
            ");",
        ]

    match = re.search(
        r"\bPARTITION\s+BY\b(?P<body>.*?)(?=;|\bCREATE\b|\bALTER\b|$)",
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
