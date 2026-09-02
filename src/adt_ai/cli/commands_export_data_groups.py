"""The `export_data -groups` move action: its own module, mirroring `export_db`'s
own split (`commands_export_db_groups.py`) against the same 20 KB context guard.

`-groups` never connects and never exports, so it shares nothing with the
handler beside it but the argument namespace and the config/root it was given.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping
from pathlib import Path

from adt_ai.export_data.groups import (
    GroupRules,
    build_prefix_rules,
    detect_groups_by_prefix,
    execute_group_move,
    parse_group_prefixes,
    plan_data_group_moves,
)
from adt_ai.export_data.runner import _data_folder
from adt_ai.shared.config import as_int


def run_data_groups_move(
    args: argparse.Namespace,
    root: Path,
    config: Mapping[str, object],
    schemas: list[str],
) -> int:
    """Reorganize already-exported table files into `data/<GROUP>/` folders.

    Same contract as `export_db -groups`: explicit prefixes (`-groups ABC DEF`)
    route only those prefixes and narrow the preview to them; bare `-groups`
    auto-detects groups from how the tree is already arranged. The plan prints
    on every run and moves nothing; `-force` applies it. A table's CSV, its
    `.sql` merge script, and its sidecar directory move together, the one
    place export_data's own file shape differs from `export_db`'s.
    """
    prefixes = parse_group_prefixes(args.groups)
    forced_group = args.force if isinstance(args.force, str) else None
    if forced_group and not prefixes:
        print(
            f"export_data: -force {forced_group} needs the prefixes it renames; "
            f"name them on -groups, as in -groups INV_ INV_ARCHIVE -force {forced_group}.",
            file=sys.stderr,
        )
        return 2

    groups_min = as_int(config.get("groups_min", 5))
    exit_code = 0
    for schema in schemas:
        data_folder = _data_folder(root, dict(config), schema)
        if prefixes:
            rules = build_prefix_rules(prefixes, group=forced_group)
        else:
            names = [
                file_path.stem.upper()
                for file_path in data_folder.glob("*.csv")
                if file_path.is_file()
            ]
            detected = detect_groups_by_prefix(names, groups_min)
            rules = GroupRules(type_rules={"DATA": detected}) if detected else GroupRules.empty()
        plan = plan_data_group_moves(data_folder, rules)
        code = execute_group_move(
            plan,
            emit=print,
            show_unmatched=not prefixes,
            force=bool(args.force),
        )
        exit_code = exit_code or code
    return exit_code
