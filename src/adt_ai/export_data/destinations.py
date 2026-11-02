"""Which files an `export_data` run is about to write, checked before it writes any (`#1111`).

`FOO` and a table created quoted as `"Foo"` are two tables, but a table's files
are named after its lower-cased name, so both wanted `foo.csv`, `foo.sql` and the
`foo/` sidecar folder, and the second replaced the first. The names are compared
the way a filesystem compares them, ignoring case and Unicode composition, so the
refusal holds on a case-insensitive volume and keeps the export portable to one.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from pathlib import Path

from adt_ai.shared.safe_paths import UnsafePathError


def table_destinations(csv_path: Path, merge_extension: str) -> tuple[Path, ...]:
    """The CSV, the MERGE script beside it, and the sidecar folder named after it."""
    return (
        csv_path,
        csv_path.with_name(csv_path.stem + merge_extension),
        csv_path.with_suffix(""),
    )


def destination_key(path: Path) -> str:
    """`path` as a filesystem that ignores case and Unicode composition reads it."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFD", str(path)).casefold())


def refuse_colliding_destinations(
    planned: Sequence[tuple[str, Path]],
    merge_extension: str,
) -> None:
    """Raise when two of the `(label, csv path)` pairs would write one file.

    Called with every selected table before the first one is written, so a
    refusal leaves the previous export untouched.
    """
    claimed: dict[str, tuple[str, Path]] = {}
    for label, csv_path in planned:
        for destination in table_destinations(csv_path, merge_extension):
            key = destination_key(destination)
            owner, first = claimed.setdefault(key, (label, destination))
            if owner != label:
                raise UnsafePathError(
                    f"Data export would write one file for two tables: {owner} and "
                    f"{label} both map to {first.name} "
                    f"({first.parent}), which the filesystem does not tell apart; "
                    "no data files were written. Use separate export roots and non-overlapping "
                    "schema prefix/ignore filters."
                )
