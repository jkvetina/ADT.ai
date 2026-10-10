"""The working copy `patch -deploy -app 0` lands a tree on (ADT #1069).

Jan, 2026-10-09: *"On 26.2+ if -app 0 is passed, lets use working copy, if any
number is provided, respect that number"*. A sandbox on a derived id
(`apex_import.derive_sandbox_app_id`) has no relation to the application it was
copied from, so it carries no `MAIN_APPLICATION_ID` and a front-end test cannot
tell what it is testing. A working copy carries it.

Measured on the 26.2 PDB (`tests/tools/working_copy_probe.py`, 2026-10-09):

  * `APEX_APPLICATION_ADMIN.CREATE_WORKING_COPY` returns the new id and takes
    none, so APEX picks it: app 100's first copy was `101`, alias `ORDERS101`.
    That is why zero names no landing and the copy is asked for here;
  * an APEXlang tree imported over the copy's id and alias keeps
    `IS_WORKING_COPY = Yes` and `MAIN_APPLICATION_ID = 100`, so the import
    `apex_deploy` already runs is all the rest of it;
  * no other API sets either column, so a working copy on an id of the
    developer's choosing does not exist, and `-app <id>` stays what it was.

**`-drop` removes what `0` made, and only that.** Dropping the main application
leaves its copies standing, so the round trip owes the step; the description
every copy is created with, `FINGERPRINT`, is what proves ADT made it, the way
the derived alias does for a derived id.

**One copy per application per patch.** The copy is named for the patch folder,
so a redeploy of the same patch finds the copy its first run made and imports
over it, the way a redeploy onto a derived id lands on the same sandbox; a
different patch makes its own.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from adt_ai.patch import queries
from adt_ai.patch.apex_app_lock import release_at_least
from adt_ai.patch.apex_import import AppTarget, WorkingCopy, landing_id
from adt_ai.patch.models import PatchError
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared.row_values import row_value

# `CREATE_WORKING_COPY` and the columns it fills are APEX 26.2's.
WORKING_COPY_FROM = (26, 2)

# The copy's name when the deploy knows no patch folder, which only a caller
# outside `patch -deploy` does.
DEFAULT_NAME = "ADT.ai"

# The description every copy is created with, and what `-drop` reads to know
# ADT made it (`read_deployed_copies`).
FINGERPRINT = "adtai patch -deploy -app 0"


@dataclass(frozen=True)
class Landing:
    """The id a tree lands on; for a working copy, its alias and whether this
    run made it."""

    app_id  : int
    alias   : str = ""
    created : bool = False


def resolve_landing(
    target          : AppTarget | None,
    app_id          : int,
    gateway_factory : Callable[[str], Any],
    *,
    owner           : str,
    workspace       : str,
    name            : str,
) -> Landing:
    """Where `patch -deploy` lands ``app_id`` under ``target``.

    `0` is the working copy, refused as `PatchError`; anything else is
    `apex_import.landing_id`, the application's own id when that names none.
    The working copy is asked for here and never reaches that fallback, which
    would read it as in place.
    """
    if not isinstance(target, WorkingCopy):
        return Landing(app_id=landing_id(target, app_id) or app_id)
    try:
        return land_working_copy(gateway_factory(owner), app_id, workspace=workspace, name=name)
    except ValueError as error:
        raise PatchError(str(error)) from None


def land_working_copy(
    gateway   : Any,
    app_id    : int,
    *,
    workspace : str,
    name      : str,
) -> Landing:
    """The working copy of ``app_id`` named ``name``, created when there is none.

    Raises ``ValueError`` with the message the deploy prints: below APEX 26.2,
    or when APEX reports no copy after creating one.
    """
    if not release_at_least(gateway, WORKING_COPY_FROM):
        raise ValueError(
            "-app 0 NEEDS APEX 26.2\n\n"
            f"A working copy of application {app_id} is an APEX 26.2 feature, and this target\n"
            "is older or does not say.\n"
            "Pass -app <id> to land the tree on that id instead."
        )
    name = name.strip() or DEFAULT_NAME
    if found := _find(gateway, app_id, name):
        return Landing(app_id=found[0], alias=found[1])
    gateway.execute(queries.APEX_CREATE_WORKING_COPY_BLOCK.format(
        workspace   = escape_literal(workspace),
        app_id      = int(app_id),
        name        = escape_literal(name),
        description = escape_literal(FINGERPRINT),
    ))
    if found := _find(gateway, app_id, name):
        return Landing(app_id=found[0], alias=found[1], created=True)
    raise ValueError(
        f"WORKING COPY OF {app_id} NOT FOUND AFTER CREATING IT\n\n"
        f"APEX reported no copy named {name} of application {app_id}.\n"
        "Pass -app <id> to land the tree on that id instead."
    )


def read_deployed_copies(gateway: Any) -> dict[int, int]:
    """Every working copy a `-deploy -app 0` made, keyed by id, to its main id.

    `-drop`'s half of the round trip: dropping the main application leaves its
    copies standing (measured on 26.2.0, `working_copy_probe.py --cascade`), so
    without this nothing ADT made with `0` could be removed by ADT. Empty below
    26.2, where neither the copies nor the columns exist.
    """
    if not release_at_least(gateway, WORKING_COPY_FROM):
        return {}
    copies: dict[int, int] = {}
    for row in gateway.fetch_all(
        queries.APEX_DEPLOYED_WORKING_COPIES_QUERY, {"description": FINGERPRINT}
    ) or []:
        copies[int(row_value(row, "APP_ID"))] = int(row_value(row, "MAIN_ID"))
    return copies


def _find(gateway: Any, app_id: int, name: str) -> tuple[int, str] | None:
    for row in gateway.fetch_all(
        queries.APEX_WORKING_COPY_QUERY, {"app_id": int(app_id), "name": name}
    ) or []:
        return int(row_value(row, "APP_ID")), str(row_value(row, "APP_ALIAS") or "")
    return None


__all__ = [name for name in globals() if not name.startswith("_")]
