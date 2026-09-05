"""Column geometry for the `DEPLOYING PATCH:` table, shared by all three renders.

Split out of ``patch_deploy_render`` when the live reporter pushed that module
past the 20 KB context guard (ADT #434). The reporter needs the same widths the
batch render uses, and an import back into the renderer would be a cycle, so the
geometry both of them read now sits under both of them.

Nothing here prints. That is the property worth keeping: the batch table, the
two-half streamed row and the live repaint all size themselves from this one
place, which is what stops the three from drifting apart (SOP §Console output
contract).
"""

from __future__ import annotations

from collections.abc import Sequence

from adt_ai.export_db.render import _compute_adt_layout
from adt_ai.export_db.table import _AdtTableLayout
from adt_ai.patch.models import DeploymentPlanItem, DeploymentResult

# The columns the deploy table carries, in the order it prints them. `COMMITS`
# is not one of them: a script's commits are named in the `RELEVANT COMMITS:`
# listing that now precedes the run, not in a per-script row.
#
# That sentence lost its opening clause when ADT #273 split the render out of
# `commands_patch_deploy`, and the fragment has sat here without a subject ever
# since; ADT #670 gives it one back rather than deleting the reasoning.
#
# No `#`, and STATUS closes the row rather than sitting a column short of the end
# (ADT #444). Jan, 2026-08-21: *"swap columns STATUS and TIMER in deploying patch
# section and remove the '#' column"*. The ordinal was `result.order`, which is
# the deploy SEQUENCE and is already the order the rows are printed in, so the
# column restated the reader's own position in the table; the ordinary patch has
# one driving script, where it only ever said `1`. `DeploymentPlanItem.order` is
# untouched, it still sequences the deploy and names the logs, it just stops
# being rendered. STATUS last is what a reader scans for: it is the answer the
# table exists to give, and TIMER is the detail beside it.
DEPLOY_COLUMNS = ("FILE", "SCHEMA", "FILES", "TIMER", "STATUS")

# Every status this table can render. The streamed header is sized before the
# first script runs, so the widest one is reserved up front: a run that fails on
# script 1 of 3 appends unrun rows, and without the reservation those rows would
# widen STATUS after the header was already on screen, leaving every row above
# them mis-aligned.
#
# All four fit in 7 characters, which is old ADT's `'status': 7` (patch.py:517)
# and the width of the longest thing a normal run prints. `NOT RUN` is what
# `NOT DEPLOYED` was called until ADT #284: the longer spelling reserved twelve
# columns on every deploy to describe a state only a *failed* one reaches, so the
# table was five characters wider than its own content forever (Jan, 2026-08-10:
# "the status and timer columns are wider than the content, any reason?"). The
# distinction `#254` drew is in the words, not their length, SKIPPED is a
# target already at SUCCESS, NOT RUN is a script an earlier failure cut off.
DEPLOY_STATUSES = ("SUCCESS", "ERROR", "SKIPPED", "NOT RUN")

# What an OPEN row says while its script is still running (ADT #441). Not a
# `DEPLOY_STATUSES` member, because that tuple is the statuses a finished result
# can carry and this one never reaches a result row. It sizes the column all the
# same: the live paint and the row that replaces it have to draw the same
# geometry, or the shorter closing row leaves the tail of the longer paint on
# screen. Jan asked for this word, 2026-08-21: "Also status should be IN
# PROGRESS", so the reservation goes from `#284`'s 7 to 11 and every deploy table
# is four characters wider. That is the cost of the word rather than an accident:
# `#284` narrowed this column on Jan's own complaint that it was wider than its
# content, and it is only wider now because a run genuinely prints eleven
# characters here.
DEPLOY_STATUS_RUNNING = "IN PROGRESS"

# `TIMER` is a quantity carrying a unit, so cell-sniffing reads `12s` as text and
# would print it left-aligned; declaring the column numeric is the caller saying
# what it IS (`_compute_adt_layout`). `FILES` carries its own digits.
DEPLOY_NUMERIC = ("FILES", "TIMER")

# No TIMER reservation: its own header is 5 characters, which holds every value
# up to `9999s`, a single script running 2h46m. ADT #273 reserved 6 to cover 27
# hours and bought that with a permanently empty column on every real deploy
# (ADT #284). Old ADT sized it 5 too (patch.py:518).


def _files_cell(result: DeploymentResult) -> str:
    """`n/m` files reached, blank when the run reported no progress at all.

    A script that died before its first file has nothing measured, and `0/0` would
    read as a finished empty deploy rather than an unknown one (ADT #254).
    """
    deployed = getattr(result, "deployed", None)
    total    = getattr(result, "deployed_total", None)
    return "" if deployed is None or total is None else f"{deployed}/{total}"

def _timer_cell(result: DeploymentResult) -> str:
    """Whole seconds spent in SQLcl, `Ns`, blank for a script that never ran.

    Rounded the way the shared footer rounds (`context.py` `_print_completion_timer`,
    `int(elapsed + 0.5)`) so a script's own row and the run's total are the same
    kind of number; old ADT rounded identically (`patch.py:572`). A SKIPPED or
    NOT RUN row has no measurement at all, and `0s` there would claim one.
    """
    seconds = getattr(result, "seconds", None)
    return "" if seconds is None else f"{int(seconds + 0.5)}s"

def _deployment_row_values(result: DeploymentResult) -> list[object]:
    return [
        result.file,
        result.schema,
        _files_cell(result),
        _timer_cell(result),
        result.status,
    ]

def _deployment_rows(results: Sequence[DeploymentResult]) -> list[dict[str, object]]:
    return [
        dict(zip(DEPLOY_COLUMNS, _deployment_row_values(result), strict=True))
        for result in results
    ]

def _deployment_min_widths(plan: Sequence[DeploymentPlanItem]) -> dict[str, int]:
    """Column reservations both renders share, so neither can drift from the other.

    `FILES` is seeded from the plan's per-item counts as `<n>/<n>`, which is a
    real upper bound rather than an estimate: `item.files` is a count of the
    finished install script's own live `@` file references, excluding anything
    under `patch_template_dir` (ADT #321), `deployed_total` on a run's result is
    that same number, and `deployed` (how far a run got) can only be less than
    or equal to it.
    """
    return {
        # `FILE` and `SCHEMA` are known exactly from the plan, and the streamed
        # layout sees no rows at all, without them it sized those from their
        # headers alone and every streamed row came out narrower than the batch
        # render of the same results.
        "FILE": max((len(item.file) for item in plan), default = 0),
        "SCHEMA": max((len(item.schema) for item in plan), default = 0),
        "FILES": max(
            (len(f"{item.files}/{item.files}") for item in plan),
            default = 0,
        ),
        # The running word is measured beside the finished ones, so the column
        # is sized once for every string this table can put in it.
        "STATUS": max(
            len(status) for status in (*DEPLOY_STATUSES, DEPLOY_STATUS_RUNNING)
        ),
    }

def _deployment_layout(
    rows: list[dict[str, object]], plan: Sequence[DeploymentPlanItem]
) -> _AdtTableLayout:
    return _compute_adt_layout(
        rows,
        list(DEPLOY_COLUMNS),
        _deployment_min_widths(plan),
        DEPLOY_NUMERIC,
    )
