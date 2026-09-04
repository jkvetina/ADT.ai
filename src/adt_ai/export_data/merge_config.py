"""Which sections a table's generated MERGE file carries.

`tables_global.merge` is the default for every exported table and `merge_tables`
overrides it flag by flag, so a table listed in neither keeps the shape
`export_data` has always written. The per-table block used to live at
`tables.<TABLE>.merge`; `#684` moved it out to its own top-level map, because
`tables:` also carries the row filters and the two read as one setting.

It sits beside `runner.py` rather than inside it because the resolver is the
whole of a config surface, validation included, and the runner is already at
the repository's context-size cap.
"""

from __future__ import annotations

from typing import Any

from adt_ai.shared.config import InvalidConfigValueError

#: The three sections a generated MERGE file can carry, and the only keys a
#: merge block may name. Anything else is a typo that would otherwise be read as
#: "leave that section at its default" and silently ship the wrong file.
MERGE_FLAGS = ("delete", "insert", "update")

#: `is_enabled` reads any value and answers False for one it does not know, so
#: the words it accepts either way are listed here and everything else is a
#: configuration error rather than a quiet False (`#684`).
_MERGE_WORDS = frozenset({"1", "TRUE", "Y", "YES", "ON", "0", "FALSE", "N", "NO", "OFF"})


def merge_config(config: dict[str, Any], table_name: str) -> dict[str, Any]:
    """The merge flags in force for one table, global defaults overlaid."""
    resolved: dict[str, Any] = {}
    tables_global = config.get("tables_global", {})
    if isinstance(tables_global, dict) and tables_global.get("merge") is not None:
        resolved.update(_merge_block(tables_global["merge"], "tables_global.merge"))
    _refuse_renamed_merge_key(config)
    resolved.update(_merge_tables_entry(config, table_name))
    return resolved


def _merge_tables_entry(config: dict[str, Any], table_name: str) -> dict[str, Any]:
    merge_tables = config.get("merge_tables")
    if merge_tables is None:
        return {}
    if not isinstance(merge_tables, dict):
        raise InvalidConfigValueError(
            "Config merge_tables must be a mapping of table name to merge flags, "
            f"got {type(merge_tables).__name__}"
        )
    table_key = table_name.upper()
    for key, value in merge_tables.items():
        if str(key).upper() != table_key:
            continue
        return _merge_block(value, f"merge_tables.{key}")
    return {}


def _merge_block(block: Any, where: str) -> dict[str, Any]:
    if not isinstance(block, dict):
        raise InvalidConfigValueError(
            f"Config {where} must be a mapping of {', '.join(MERGE_FLAGS)} "
            f"to true or false, got {type(block).__name__}"
        )
    resolved: dict[str, Any] = {}
    for key, value in block.items():
        flag = str(key).strip().lower()
        if flag not in MERGE_FLAGS:
            raise InvalidConfigValueError(
                f"Config {where} has an unknown merge flag {key!r}; "
                f"expected one of {', '.join(MERGE_FLAGS)}"
            )
        if not isinstance(value, bool) and str(value).strip().upper() not in _MERGE_WORDS:
            raise InvalidConfigValueError(
                f"Config {where}.{flag} must be true or false, got {value!r}"
            )
        resolved[flag] = value
    return resolved


def _refuse_renamed_merge_key(config: dict[str, Any]) -> None:
    """A `tables.<TABLE>.merge` block left over from before `#684`.

    Reading it as an unknown key and ignoring it would drop the table's modes
    without a word and export a MERGE the project never asked for, so the whole
    `tables:` map is checked rather than only the table being exported.
    """
    tables = config.get("tables")
    if not isinstance(tables, dict):
        return
    stale = sorted(
        str(name)
        for name, entry in tables.items()
        if isinstance(entry, dict) and entry.get("merge") is not None
    )
    if not stale:
        return
    raise InvalidConfigValueError(
        f"Config tables.{stale[0]}.merge moved to merge_tables.{stale[0]}; "
        f"move the merge block of {', '.join(stale)} under the top-level "
        "merge_tables key. See docs/export_data.md."
    )
