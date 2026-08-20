"""The `export_db -groups` move action: its own module, per the CLI size guard.

`-groups` is the one branch of `export_db` that never connects and never exports,
so it shares nothing with the handler beside it but the argument namespace. It
lives here because `commands_exports.py` sits against the repo's 20 KB context
budget, the same split `#395` and `#407` made in `shared/connections.py`.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping

from adt_ai.export_db.files import ObjectFileResolver
from adt_ai.export_db.group_moves import (
    build_prefix_rules,
    execute_group_move,
    parse_group_prefixes,
    plan_group_moves,
)
from adt_ai.export_db.groups import GroupRules, detect_groups_by_prefix


def run_groups_move(
    args: argparse.Namespace,
    root: object,
    config: Mapping[str, object],
    schemas: list[str],
) -> int:
    """Reorganize already-exported object files into <object_type>/<group>/ folders.

    Explicit prefixes (`-groups ABC DEF`) route only those prefixes and narrow the
    preview to them (ADT #408); bare `-groups` auto-detects per object type using
    ``groups_min``. The plan prints on every run and moves nothing; `-force` is
    what applies it (ADT #409). Groups are uppercased.

    `-force GROUP` applies the plan and renames every group it routes to that one
    folder, so several prefixes land together (ADT #416). It needs prefixes to
    rename: folding a whole auto-detected layout into one folder is not a layout,
    so a name beside bare `-groups` is refused rather than obeyed.
    """
    resolver = ObjectFileResolver.from_config(root=root, config=config)
    prefixes = parse_group_prefixes(args.groups)
    forced_group = args.force if isinstance(args.force, str) else None
    if forced_group and not prefixes:
        print(
            f"export_db: -force {forced_group} needs the prefixes it renames; "
            f"name them on -groups, as in -groups ICT_VPD ICT_ABC -force {forced_group}.",
            file=sys.stderr,
        )
        return 2
    if prefixes:
        rules = build_prefix_rules(prefixes, group=forced_group)
    else:
        groups_min = int(config.get("groups_min", 5))
        type_rules: dict[str, dict[str, str]] = {}
        for object_type, names in resolver.flat_object_names(schemas).items():
            detected = detect_groups_by_prefix(names, groups_min)
            if detected:
                type_rules[object_type.upper()] = detected
        rules = GroupRules(type_rules=type_rules)
    plan = plan_group_moves(resolver.iter_type_roots(schemas), rules)
    return execute_group_move(
        plan,
        emit=print,
        show_unmatched=not prefixes,
        force=bool(args.force),
    )
