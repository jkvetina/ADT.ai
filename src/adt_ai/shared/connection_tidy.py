"""Keep a connection file down to what its user wrote (ADT #1079).

Two kinds of line accumulate in a connection file without anyone choosing them:

* SQLcl bookkeeping. ``sqlcl:`` (a saved-connection name) and ``sqlcl_sync:``
  (a credential fingerprint) were recorded on every schema ADT connected. The
  name is now derived on every run and nothing is fingerprinted, so
  ``sqlcl_sync`` is removed wherever it stands, and ``sqlcl`` wherever it is
  empty or is a name ADT derived (with or without #660's path hash). A
  ``sqlcl:`` the user wrote himself stays. A ``db:`` block, and a schema, left
  holding nothing once those lines go are removed with them.
* Empty export filters. ``export: {ignore: '', prefix: ''}`` under every schema
  says nothing. The first schema in the file keeps its block, so the file still
  shows where the filters go; every later schema loses a block whose filters are
  all empty. A block with any filter set is untouched.

The removal is done on the file's LINES, never by re-rendering the document: a
YAML round-trip attaches the comment and blank line after a key to that key, so
deleting the key through it deleted the user's comment too. The result is parsed
again and compared with the intended document before anything is written.

:func:`tidy_connection_file` runs whenever a connection file is loaded, so an old
file is cleaned the first time any command reads it, and :func:`tidy_text` runs
on what the `connection` editor is about to write, so nothing ADT writes brings
the lines back.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import yaml as pyyaml

from adt_ai.shared import text_files
from adt_ai.shared.sqlcl_names import is_generated_sqlcl_name

Path_ = tuple[str, ...]

# A block-mapping key line: indentation, then a plain or quoted key and a colon.
_KEY_LINE = re.compile(
    r"^(?P<indent> *)(?P<key>\"[^\"]*\"|'[^']*'|[^\s#'\"][^:#]*?)\s*:(?:\s|$)"
)


def tidy_document(data: Any, source_file: Path | str) -> bool:
    """Apply the removals to a loaded document in place; True when any applied."""
    removals = planned_removals(data, source_file)
    for path in sorted(removals, key=len, reverse=True):
        parent = data
        for key in path[:-1]:
            parent = parent[key]
        del parent[path[-1]]
    return bool(removals)


def planned_removals(data: Any, source_file: Path | str) -> list[Path_]:
    """Every key path the tidy pass removes from ``data``, outermost covering inner."""
    if not isinstance(data, dict):
        return []
    removals: list[Path_] = []
    first_schema_seen = False
    for environment, environment_node in data.items():
        if not isinstance(environment_node, dict):
            continue
        schemas = environment_node.get("schemas")
        if not isinstance(schemas, dict):
            continue
        env = str(environment)
        emptied = 0
        for schema, schema_node in schemas.items():
            if not isinstance(schema_node, dict):
                continue
            base: Path_ = (env, "schemas", str(schema))
            gone: list[Path_] = []
            db = schema_node.get("db")
            dropped_db = _sqlcl_keys(db, source_file, env, str(schema))
            if dropped_db and isinstance(db, dict) and len(dropped_db) == len(db):
                gone.append((*base, "db"))
            else:
                gone += [(*base, "db", key) for key in dropped_db]
            if first_schema_seen and _empty_export(schema_node):
                gone.append((*base, "export"))
            first_schema_seen = True
            left = set(schema_node) - {path[3] for path in gone if len(path) == 4}
            if gone and not left:
                removals.append(base)
                emptied += 1
            else:
                removals += gone
        if emptied and emptied == len(schemas):
            removals = [path for path in removals if path[:2] != (env, "schemas")]
            removals.append((env, "schemas"))
    return removals


def tidy_text(text: str, source_file: Path | str) -> str:
    """``text`` with the planned removals cut out line by line.

    Returns ``text`` unchanged when there is nothing to remove, or when the cut
    would not parse back to exactly the intended document (a shape this line
    scanner does not understand is left alone rather than damaged).
    """
    try:
        plain = pyyaml.safe_load(text)
    except Exception:
        return text
    removals = planned_removals(plain, source_file)
    if not removals:
        return text
    cut = _cut_lines(text, set(removals))
    intended = copy.deepcopy(plain)
    tidy_document(intended, source_file)
    try:
        if pyyaml.safe_load(cut) != intended:
            return text
    except Exception:
        return text
    return cut


def tidy_connection_file(path: Path | str) -> bool:
    """Tidy a connection file on disk; best-effort, never raises.

    An already-tidy file is never rewritten, and `write_private_text` keeps a
    file that holds `pwd:` owner-only.
    """
    try:
        target = Path(path)
        text = target.read_text(encoding="utf-8")
        tidied = tidy_text(text, target)
        if tidied == text:
            return False
        text_files.write_private_text(target, tidied)
        return True
    except Exception:
        return False


def _sqlcl_keys(db: Any, source_file: Path | str, environment: str, schema: str) -> list[str]:
    if not isinstance(db, dict):
        return []
    keys = []
    if "sqlcl_sync" in db:
        keys.append("sqlcl_sync")
    if "sqlcl" in db:
        value = db.get("sqlcl")
        if _is_blank(value) or is_generated_sqlcl_name(value, source_file, environment, schema):
            keys.append("sqlcl")
    return keys


def _empty_export(schema_node: dict[str, Any]) -> bool:
    if "export" not in schema_node:
        return False
    export = schema_node.get("export")
    return export is None or (
        isinstance(export, dict) and all(_is_blank(value) for value in export.values())
    )


def _cut_lines(text: str, removals: set[Path_]) -> str:
    out: list[str] = []
    stack: list[tuple[int, str]] = []
    skip_indent: int | None = None
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))
        match = _KEY_LINE.match(line)
        if skip_indent is not None:
            if match is None:
                # A comment or continuation indented under the removed key is
                # part of it; a blank line, or a comment at the block's own
                # level or above, belongs to what follows and stays.
                if stripped and indent > skip_indent:
                    continue
                out.append(line)
                continue
            if indent > skip_indent:
                continue
            skip_indent = None
        if match is None:
            out.append(line)
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, _unquote(match.group("key"))))
        if tuple(key for _, key in stack) in removals:
            skip_indent = indent
            continue
        out.append(line)
    return "".join(out)


def _unquote(key: str) -> str:
    key = key.strip()
    if len(key) > 1 and key[0] == key[-1] and key[0] in "'\"":
        return key[1:-1]
    return key


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


__all__ = ["planned_removals", "tidy_connection_file", "tidy_document", "tidy_text"]
