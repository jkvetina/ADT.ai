"""Page locks, the deploy's lock below APEX 26.2 where the target allows it (ADT #1059).

Below 26.2 an import deletes the application lock and APEX offers no API to take
it, so `apex_lock` held the deploy with build status. Jan, 2026-10-08: *"As a
page lock fallback on pre 26.2 apex versions, lets use undocumented
wwv_flow_property_dev package and lock_page and unlock_page procedures."* He
chose page locks INSTEAD of `RUN_ONLY`, with `RUN_ONLY` kept as the fallback.

The package carries no grant, so this is a target a DBA has opened with
`GRANT EXECUTE ON <APEX schema>.WWV_FLOW_PROPERTY_DEV TO <parsing schema>`.
Without that grant nothing here runs and the deploy is the one it was, silently
(Jan: "Silent fallback").

**What is locked:** every page nobody holds, as the developer. A page another
developer holds stays theirs and is already in the `PAGE LOCKS REMOVED BY THE
IMPORT` warning; a page the developer held already keeps its own comment. The
import deletes the deploy's locks with the rest, and the release unlocks
whatever a deploy that never imported left behind.

**Nothing here raises.** A block that failed answers its first line, and the
caller turns it into the fallback or a failed release, as `apex_app_lock` does.
"""

from __future__ import annotations

import re
from contextlib import suppress
from typing import Any

from adt_ai.patch import queries
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared.row_values import row_value

#: The package, named the way a reason has to name it for a DBA to act on it.
PACKAGE = "WWV_FLOW_PROPERTY_DEV"

#: An owner formatted into PL/SQL is an identifier or it is not used.
_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9_$#]{0,127}")


def page_lock_owner(gateway: Any) -> str:
    """The APEX schema whose page lock package this schema may call, or ""."""
    try:
        rows = gateway.fetch_all(queries.APEX_PAGE_LOCK_OWNER_QUERY) or []
    except Exception:  # noqa: BLE001 - an unreadable grant is no grant
        return ""
    for row in rows:
        owner = str(row_value(row, "OWNER") or "").strip()
        if _IDENTIFIER.fullmatch(owner):
            return owner
    return ""


def lock_pages(
    gateway   : Any,
    owner     : str,
    app_id    : int,
    *,
    workspace : str,
    user      : str,
    comment   : str,
) -> tuple[tuple[int, ...], str]:
    """Lock every free page of ``app_id`` as ``user``; the pages held, or why not.

    A page that would not lock releases every page this call took, so the caller
    falls back to build status with nothing of this deploy left on the target.
    """
    try:
        answer = gateway.fetch_clob(_block(
            queries.APEX_LOCK_PAGES_BLOCK, owner, app_id,
            workspace=workspace, user=user, comment=comment,
        ))
    except Exception as error:  # noqa: BLE001 - reported as a fallback, never raised
        _release_quietly(gateway, owner, app_id, workspace=workspace, user=user, comment=comment)
        return (), first_line(error)
    states = _states(answer)
    failed = [page for page, state in states if state == "FAILED"]
    if failed:
        _release_quietly(gateway, owner, app_id, workspace=workspace, user=user, comment=comment)
        return (), f"page {failed[0]} did not lock"
    return tuple(page for page, state in states if state == "HELD"), ""


def unlock_pages(
    gateway   : Any,
    app_id    : int,
    *,
    workspace : str,
    user      : str,
    comment   : str,
) -> str:
    """Unlock the deploy's own page locks; "" when none is left, else why."""
    owner = page_lock_owner(gateway)
    if not owner:
        return f"{PACKAGE} is no longer callable by this schema"
    try:
        left = gateway.fetch_clob(_block(
            queries.APEX_UNLOCK_PAGES_BLOCK, owner, app_id,
            workspace=workspace, user=user, comment=comment,
        ))
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return first_line(error)
    count = int(str(left or "0").strip() or 0)
    return f"{count} page locks are still held" if count else ""


def first_line(error: object) -> str:
    """The ORA line itself, without the ORA-06512 stack the driver appends."""
    return (str(error).strip().splitlines() or [""])[0]


def _release_quietly(gateway: Any, owner: str, app_id: int, **names: str) -> None:
    # The lock error is the one worth reporting, so a failed cleanup is swallowed.
    with suppress(Exception):
        gateway.fetch_clob(_block(queries.APEX_UNLOCK_PAGES_BLOCK, owner, app_id, **names))


def _block(template: str, owner: str, app_id: int, *, workspace: str, user: str,
           comment: str) -> str:
    return template.format(
        owner     = owner,
        app_id    = int(app_id),
        workspace = escape_literal(workspace),
        user      = escape_literal(user),
        comment   = escape_literal(comment),
    )


def _states(answer: object) -> list[tuple[int, str]]:
    """`2:HELD` lines -> `[(2, "HELD")]`; anything unreadable is skipped."""
    states = []
    for line in str(answer or "").splitlines():
        page, _, state = line.strip().partition(":")
        if page.lstrip("-").isdigit() and state:
            states.append((int(page), state))
    return states


__all__ = [
    "PACKAGE",
    "first_line",
    "lock_pages",
    "page_lock_owner",
    "unlock_pages",
]
