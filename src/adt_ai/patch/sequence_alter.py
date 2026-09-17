"""The `ALTER SEQUENCE` a changed sequence ships in place of its CREATE (ADT #830).

A sequence in `immutables` is never re-created by a patch, so a change to its
file reaches the target the only way Oracle allows, as an `ALTER SEQUENCE`. Jan
chose this over a warning on the card: the change deploys rather than waiting for
somebody to write the statement by hand.

No database is asked, unlike the table diff (`table_diff_runner.py`). A sequence
is one statement of independent clauses, so the comparison is exact in Python:
each clause Oracle lets `ALTER SEQUENCE` change is read from both versions, and
one that differs is written. A clause the new version no longer states is back at
Oracle's default, which is exactly why `export_db` stripped it from the file
(`object_normalizers/sequence.py`), so the default is what gets written.
`START WITH` is ignored on both sides: the export strips it and `ALTER` cannot
set it.

The helper is written into the table ALTER slot under the name the table writer
uses, `<stem>.<commit>.sql` or `<stem>.hash.sql`. A sequence and a table share
one schema namespace, so the stems cannot collide, and every reader that already
recognises a generated ALTER (`#503` recovery, `#508` reset, `#753` ordering)
recognises this one without a second rule.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch.generated_helpers import ALTER_HELPER_SLOT
from adt_ai.patch.immutables import immutable_types
from adt_ai.patch.layout import database_object_stem, database_object_type
from adt_ai.patch.models import AlterHelper
from adt_ai.patch.table_versions import _body_at_content_hash, _table_baseline, _table_versions
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.sql_identifiers import safe_identifier

_CREATE_RE = re.compile(
    r"\bCREATE\s+SEQUENCE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\w$#.\"]+(?P<options>[^;/]*)",
    re.IGNORECASE,
)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

#: Oracle's value for each clause `ALTER SEQUENCE` can change, in the order the
#: statement is written.
_DEFAULTS: dict[str, str] = {
    "INCREMENT": "INCREMENT BY 1",
    "MINVALUE": "NOMINVALUE",
    "MAXVALUE": "NOMAXVALUE",
    "CYCLE": "NOCYCLE",
    "CACHE": "CACHE 20",
    "ORDER": "NOORDER",
    "KEEP": "NOKEEP",
    "SCALE": "NOSCALE",
    "SHARD": "NOSHARD",
    "SCOPE": "GLOBAL",
}

#: A bare keyword and the clause it sets.
_SWITCHES: dict[str, str] = {
    "NOMINVALUE": "MINVALUE",
    "NOMAXVALUE": "MAXVALUE",
    "CYCLE": "CYCLE",
    "NOCYCLE": "CYCLE",
    "NOCACHE": "CACHE",
    "ORDER": "ORDER",
    "NOORDER": "ORDER",
    "KEEP": "KEEP",
    "NOKEEP": "KEEP",
    "NOSCALE": "SCALE",
    "NOSHARD": "SHARD",
    "SESSION": "SCOPE",
    "GLOBAL": "SCOPE",
}


def _clauses(text: str) -> dict[str, str] | None:
    """Every clause one `CREATE SEQUENCE` states, keyed by what it sets."""
    match = _CREATE_RE.search(_LINE_COMMENT_RE.sub("", text))
    if match is None:
        return None
    words = match.group("options").upper().split()
    clauses: dict[str, str] = {}
    index = 0
    while index < len(words):
        word = words[index]
        following = words[index + 1] if index + 1 < len(words) else ""
        if word in {"INCREMENT", "START"} and following in {"BY", "WITH"}:
            if word == "INCREMENT":
                step = words[index + 2] if index + 2 < len(words) else ""
                clauses["INCREMENT"] = f"INCREMENT BY {step}".strip()
            index += 3
        elif word in {"MINVALUE", "MAXVALUE", "CACHE"}:
            clauses[word] = f"{word} {following}".strip()
            index += 2
        elif word in {"SCALE", "SHARD"}:
            modifier = following in {"EXTEND", "NOEXTEND"}
            clauses[word] = f"{word} {following}" if modifier else word
            index += 2 if modifier else 1
        else:
            clauses[_SWITCHES.get(word, word)] = word
            index += 1
    return clauses


def sequence_alter_sql(name: str, previous: str, current: str) -> str:
    """The `ALTER SEQUENCE` taking ``previous`` to ``current``, or "" for none."""
    safe_identifier(name, role="sequence name")
    before = _clauses(previous)
    after = _clauses(current)
    if before is None or after is None:
        return ""
    changes = [
        after.get(clause, default)
        for clause, default in _DEFAULTS.items()
        if after.get(clause, default) != before.get(clause, default)
    ]
    # A clause this module does not know is carried when the new version states
    # it differently. Removing one has no default to return to, so it is left.
    changes.extend(
        value
        for clause, value in after.items()
        if clause not in _DEFAULTS and before.get(clause) != value
    )
    if not changes:
        return ""
    return f"ALTER SEQUENCE {name} {' '.join(changes)};\n"


def write_sequence_alter_helpers(
    root: Path,
    script_root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    hash_previous: Mapping[str, str] | None = None,
    window: list[CommitRecord] | None = None,
) -> list[AlterHelper]:
    """One `ALTER SEQUENCE` per version step a sequence takes in this patch.

    Only while `SEQUENCE` is immutable: a project that took it out of the list
    ships the `CREATE` it asked for, and an ALTER beside it would run twice.
    """
    if "SEQUENCE" not in immutable_types(config):
        return []
    written: list[AlterHelper] = []
    for file in sorted(f for f in files if database_object_type(f, config) == "SEQUENCE"):
        name = database_object_stem(file, config)
        # defensive: `file` already resolved a SEQUENCE type off the same layout
        if not name:  # pragma: no cover
            continue
        for label, previous, current in _steps(root, file, records, hash_previous, window):
            sql = sequence_alter_sql(name, previous, current)
            if not sql:
                continue
            folder = script_root / ALTER_HELPER_SLOT
            folder.mkdir(parents=True, exist_ok=True)
            helper = folder / f"{Path(file).stem}.{label}.sql"
            text_files.write_text(helper, sql)
            written.append(
                AlterHelper(source=file, path=helper.relative_to(root).as_posix(), statements=1)
            )
    return written


def _steps(
    root: Path,
    file: str,
    records: list[CommitRecord],
    hash_previous: Mapping[str, str] | None,
    window: list[CommitRecord] | None,
) -> list[tuple[str, str, str]]:
    """`(helper label, previous text, current text)` for each change to compare.

    The same two bases the table writers use: in hash mode the version the
    baseline recorded against the working tree, otherwise the version before the
    window followed by each in-window version standing in for the next.
    """
    if hash_previous is not None:
        baseline = hash_previous.get(file)
        current = root / file
        if not baseline or not current.is_file():
            return []
        previous = _body_at_content_hash(
            root, file, window if window is not None else records, baseline
        )
        if previous is None:
            return []
        return [("hash", previous, current.read_text(encoding="utf-8"))]
    versions = _table_versions(root, file, records)
    # A file the window deleted has no version to reach; its DROP is the answer.
    if not versions:
        return []
    previous_bodies = [
        _table_baseline(root, file, records),
        *(body for _, body in versions[:-1]),
    ]
    return [
        (str(number), previous, current)
        for previous, (number, current) in zip(previous_bodies, versions, strict=True)
        if previous is not None
    ]


__all__ = ["sequence_alter_sql", "write_sequence_alter_helpers"]
