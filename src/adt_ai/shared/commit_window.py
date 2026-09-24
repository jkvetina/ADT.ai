"""How far back a from-scratch commit store reaches.

`patch_history_bottom_days` bounds the walk. A real project root is large,
APEXDEV_JANK carries 85,108 commits on HEAD, and the expensive half of a rebuild
is one `changed_files` call per commit, so walking years nobody will query is
work paid for and thrown away. Jan, 2026-08-15: *"if the repo is huge, we dont
need it all, last couple of months should be perfectly fine."*

A bounded first build does not start numbering at 1: every commit carries its
position on the branch's first-parent line (ADT #895), so a one-year window
whose oldest commit sits at 82,000 starts the store at 82,000, and a wider key
later fills in underneath. `rebuild/cache.py` reads those positions.
"""

from __future__ import annotations

from typing import Any

#: Shipped default for `patch_history_bottom_days`, in days.
DEFAULT_HISTORY_BOTTOM_DAYS = 365


def resolve_history_floor(config: dict[str, Any]) -> int | None:
    """`patch_history_bottom_days` from project config, or the shipped default.

    Returns None when the project asks for the whole history (0 or negative),
    which is the same shape every other "no bound" value in the rebuild request
    already uses, so the caller needs no special case.
    """
    raw = config.get("patch_history_bottom_days", DEFAULT_HISTORY_BOTTOM_DAYS)
    if raw is None or raw == "":
        return DEFAULT_HISTORY_BOTTOM_DAYS
    try:
        days = int(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"INVALID patch_history_bottom_days: {raw!r}\n\n"
            "It must be a whole number of days."
        ) from None
    return days if days > 0 else None
