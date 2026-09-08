"""The order a deploy's scripts and APEX imports actually run in.

Split out of ``deploy_run.py`` when ADT #726 pushed that module past the 24 KB
context guard, following the rule its own docstring states: a module that crosses
the guard is split, never registered as debt. The seam is the one ADT #735 drew
when it made the order a thing the deploy states rather than a thing the folder's
glob happened to produce, and it pairs with ``stages.py``, which owns the stage
names this reads.

``deploy_run`` keeps the loop that walks the sequence; this answers what the
sequence IS.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from adt_ai.patch import stages
from adt_ai.patch.apex_deploy import ApexImportItem
from adt_ai.patch.deploy import _deployment_stage
from adt_ai.patch.models import DeploymentPlanItem


@dataclass(frozen=True)
class Step:
    """One row of the deploy: an install script, or an application's import.

    ``plan_item`` is what the table sizes on and what a `NOT RUN` row is built
    from; exactly one of ``script`` and ``imported`` is set.
    """

    plan_item : DeploymentPlanItem
    script    : DeploymentPlanItem | None = None
    imported  : ApexImportItem | None = None


def deployment_sequence(
    plan: list[DeploymentPlanItem],
    apex_items: list[ApexImportItem],
    config: dict[str, Any],
    *,
    commits: int,
) -> list[Step]:
    """The scripts and imports of a run, in the order they execute (ADT #735).

    Each application's import follows the last of its scripts that is not the
    `end` half: the `init` half of a split script, or the whole `<SCHEMA>.<APP>.sql`
    a folder built before the split still carries. An application no script
    opened keeps its import at the tail, where every import sat before this.
    `order` is renumbered over the sequence, so the ordinal names the row's
    position in the run rather than in the folder.
    """
    slot: dict[int, int] = {}
    for index, item in enumerate(plan):
        if item.app_id is None:
            continue
        if _deployment_stage(item.file, config) != stages.APP_SCRIPT_END:
            slot[item.app_id] = index
    steps: list[Step] = []
    placed: set[int] = set()

    def add_import(imported: ApexImportItem) -> None:
        plan_item = imported.plan_item(len(steps) + 1, commits)
        steps.append(Step(plan_item=plan_item, imported=imported))
        placed.add(id(imported))

    for index, item in enumerate(plan):
        steps.append(Step(plan_item=replace(item, order=len(steps) + 1), script=item))
        for imported in apex_items:
            if slot.get(imported.app_id) == index:
                add_import(imported)
    for imported in apex_items:
        if id(imported) not in placed:
            add_import(imported)
    return steps


__all__ = ["Step", "deployment_sequence"]
