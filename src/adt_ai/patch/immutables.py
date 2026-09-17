"""Object types a patch never drops and never re-creates (ADT #830).

`immutables` in `config.yaml` names them, `TABLE` and `SEQUENCE` as shipped. Both
hold something a second `CREATE` cannot give back: a table its rows, a sequence
the values it has already handed out. Jan, 2026-09-17: *"We should have never
drop table or sequence! If we are doing it, it is 100% wrong! Create a key in
config and never drop them!"*

Old ADT carried the key (config.yaml:51-61) and read it in one place, the link of
a table file that had generated ALTERs. ADT.ai read it nowhere, and two things
followed, both measured on SANDBOX before this module existed: a deleted table
file shipped a DROP helper that ran on deploy, and a changed sequence file
shipped its `CREATE SEQUENCE` again, which stopped the deploy on `ORA-00955`.

The decision is taken where the install script LINKS a file, never where a
helper is written. The DROP helper is still written, so dropping stays one
uncommented line away for a person who means it, and the object file is still
snapshotted, so the patch keeps its record of what changed. A helper recovered
from an older patch folder is linked through the same rows as a fresh one, which
is `#503`'s lesson about carriers applied in advance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch.content import file_text
from adt_ai.patch.layout import database_object_type
from adt_ai.patch.table_versions import _table_baseline
from adt_ai.shared.commit_discovery import CommitRecord

#: The shipped default, and what a config that never names the key reads.
DEFAULT_IMMUTABLES: tuple[str, ...] = ("TABLE", "SEQUENCE")

NEVER_DROPPED = "NEVER DROPPED BY A PATCH"
NEVER_RECREATED = "NEVER RE-CREATED BY A PATCH"

_IF_NOT_EXISTS_RE = re.compile(r"\bIF\s+NOT\s+EXISTS\b", re.IGNORECASE)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def immutable_types(config: Mapping[str, Any]) -> frozenset[str]:
    """`immutables`: the object types a patch never drops or re-creates.

    A missing or malformed value reads as the shipped default rather than as an
    empty list, because the failure it guards against loses data: a project
    turns the protection off by writing `immutables: []`, never by accident.
    A single type written as a scalar is read as a list of one.
    """
    raw = config.get("immutables")
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list | tuple):
        return frozenset(DEFAULT_IMMUTABLES)
    return frozenset(str(item).strip().upper() for item in raw if str(item).strip())


def immutable_drop_helper_type(name: str, config: Mapping[str, Any]) -> str | None:
    """The immutable type a generated DROP helper called ``name`` would drop.

    The same `drop.<type>.<name>.sql` shape `generated_helpers.drop_helper_filename`
    writes, read against `immutables` rather than `object_types`: the question is
    no longer whether the file is a helper but whether its object may be dropped.
    The caller pairs it with the DROP slot, as every helper-name test must.
    """
    if not name.startswith("drop.") or not name.endswith(".sql"):
        return None
    middle = name[len("drop.") : -len(".sql")]
    for object_type in sorted(immutable_types(config)):
        prefix = f"{object_type.replace(' ', '_').lower()}."
        if middle.startswith(prefix) and len(middle) > len(prefix):
            return object_type
    return None


def disabled_link(rows: list[str], object_type: str, reason: str) -> list[str]:
    """``rows`` with its closing `@` line commented out and the reason above it.

    The label row stays live, so the deploy log still names the file the patch
    passed over. `deploy_progress` counts live `@` lines, never labels, so a
    commented link inflates no total (ADT #321).
    """
    *label, link = rows
    return [*label, f"-- [!] IMMUTABLE {object_type}, {reason}", f"--{link}"]


def never_recreated(
    root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    content_mode: str,
    hash_previous: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Immutable object files whose `CREATE` must not run, mapped to their type.

    A file qualifies when the target already holds its object and the version
    this patch ships would create it a second time. "Already holds" is the same
    answer the ALTER writers use: the baseline hash in hash mode, the version
    before the first selected commit otherwise. A file whose `CREATE` says
    `IF NOT EXISTS` stays linked, because it is a no-op on the target and its
    `COMMENT ON` lines are the ones `#753` moved after the ALTER.
    """
    types = immutable_types(config)
    blocked: dict[str, str] = {}
    for file in files:
        object_type = database_object_type(file, config)
        if object_type not in types:
            continue
        if hash_previous is not None:
            held = bool(hash_previous.get(file))
        else:
            held = _table_baseline(root, file, records) is not None
        if not held:
            continue
        text = file_text(root, file, mode=content_mode, records=records, config=config)
        if text is None or _IF_NOT_EXISTS_RE.search(_LINE_COMMENT_RE.sub("", text)):
            continue
        blocked[file] = object_type
    return blocked


__all__ = [
    "DEFAULT_IMMUTABLES",
    "NEVER_DROPPED",
    "NEVER_RECREATED",
    "disabled_link",
    "immutable_drop_helper_type",
    "immutable_types",
    "never_recreated",
]
