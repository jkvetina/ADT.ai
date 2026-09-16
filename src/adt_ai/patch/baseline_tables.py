"""Tables stored beside a hash baseline (ADT #857).

A baseline line records a table's hash and nothing about its shape, so a target
whose table matches no commit, a hand hotfix on PROD, left `-create -hash` with
no version to diff against: `WARNING - NO TABLE BASELINE:` and a patch shipping
a `CREATE TABLE IF NOT EXISTS` that does nothing. Jan, 2026-09-15: *"baseline
should contain the tables too"* ... *"and not as XML!"*.

So every table is kept as plain DDL under a folder named after the log, at its
repo path, and a stored table is trusted only while it hashes to the value its
log line records. Its own module rather than more of `hashes.py`, which is the
log's reader and writer and nothing else.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch.hashes import Baseline
from adt_ai.patch.layout import database_object_type
from adt_ai.shared import text_files
from adt_ai.shared.git_files import file_payload_hash


def baseline_tables_folder(path: Path) -> Path:
    """Where a baseline keeps its tables: beside the log, named after it.

    `patch_hashes/baseline.PROD.log` keeps them under `patch_hashes/baseline.PROD/`,
    so `ls` pairs each log with its folder and a copied baseline travels as the
    two of them. A `-baseline FILE` with no suffix gets `FILE.tables/`, because
    stripping nothing would name the log itself.
    """
    return path.with_suffix("") if path.suffix else path.with_name(f"{path.name}.tables")


def working_tree_tables(
    root: Path,
    config: Mapping[str, Any],
    hashes: Mapping[str, str],
) -> dict[str, str]:
    """Each table file in ``hashes`` whose bytes on disk still hash to its value.

    Only tables, because a table is the one object a patch alters rather than
    replaces, so it is the one whose previous shape anything ever reads back.
    A file edited since it was hashed is left out: its body is not the version
    the hash names, and storing it would record a shape the target never held.
    """
    layout = dict(config)
    tables: dict[str, str] = {}
    for file, value in hashes.items():
        if database_object_type(file, layout) != "TABLE":
            continue
        target = root / file
        if not target.is_file():
            continue
        payload = target.read_bytes()
        if file_payload_hash(payload) != value:
            continue
        try:
            tables[file] = payload.decode("utf-8")
        except UnicodeDecodeError:
            # A non-UTF-8 table is left to the commit-history lookup rather than
            # stored as text it is not (`#834`).
            continue
    return tables


def write_baseline_tables(
    path: Path,
    tables: Mapping[str, str],
    *,
    covered: Callable[[str], bool],
) -> Path:
    """Store each table beside the baseline ``path``, at its repo path.

    Plain DDL, the file `export_db` writes, never XML. The same rule as the log
    lines applies inside the folder: a stored table ``covered`` claims and
    ``tables`` no longer carries is removed, and everything outside the scope is
    left as it was. A deploy passes a scope claiming nothing, so it only writes.
    """
    folder = baseline_tables_folder(path)
    if folder.is_dir():
        # Deepest first, so a folder is visited after its files and one emptied
        # here is removed rather than left behind as an empty shell.
        for stored in sorted(folder.rglob("*"), reverse=True):
            if stored.is_dir():
                if not any(stored.iterdir()):
                    stored.rmdir()
                continue
            relative = stored.relative_to(folder).as_posix()
            if relative not in tables and covered(relative):
                stored.unlink()
    for file, body in sorted(tables.items()):
        relative_path = Path(file)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            continue
        target = folder / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_text(target, body)
    return folder


def read_baseline_tables(baseline: Baseline) -> dict[str, str]:
    """Every stored table the baseline's own log still agrees with.

    A body is trusted only while it hashes to the value its log line records.
    Anything else describes some other moment, a table file left behind by an
    older baseline or edited by hand, and an ALTER built from it would be wrong
    in a way nothing downstream can see, so it is dropped and the caller falls
    back to the commit-history lookup.
    """
    folder = baseline_tables_folder(baseline.path)
    if not folder.is_dir():
        return {}
    tables: dict[str, str] = {}
    for stored in sorted(folder.rglob("*")):
        if not stored.is_file():
            continue
        relative = stored.relative_to(folder).as_posix()
        recorded = baseline.hashes.get(relative)
        payload = stored.read_bytes()
        if recorded is None or file_payload_hash(payload) != recorded:
            continue
        try:
            tables[relative] = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
    return tables


__all__ = [
    "baseline_tables_folder",
    "read_baseline_tables",
    "working_tree_tables",
    "write_baseline_tables",
]
