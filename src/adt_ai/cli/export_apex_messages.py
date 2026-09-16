from __future__ import annotations

from collections.abc import Sequence

from adt_ai.cli.constants import print_adt_header

#: Constants rather than literals at the call site, so a rename sweeps through
#: one edit and the docs grep for it has something to find (`#509`, `#555`).
SCHEMA_NOT_CONFIGURED_HEADER = "WARNING - SCHEMA NOT CONFIGURED:"
APP_NOT_FOUND_HEADER = "WARNING - APP NOT FOUND:"


def print_apex_owner_not_configured(
    owners: Sequence[tuple[str, str, Sequence[str]]], *, reveal: bool = False
) -> None:
    """One warning section for every app whose owner the connection file lacks.

    Each entry is `(app id, owner, configured schemas that reached it)`.

    Jan, 2026-09-15, on the two bare lines this replaced: *"I want a warning
    header, then the note and make it shorter. skip this 'which is not
    configured for environment DEV'"* (`#858`). On a configured schema that can
    see the app anyway: *"The warning should get extra line and print schema
    able to reach it."* `-reveal` lists every such schema, sorted: *"I would
    like to see other schemas listed (and sorted)"*. The export no longer skips
    the app, it exports it through the schema that reached it: *"I am providing
    a schema which can reach the app, but you are not exporting the app"*
    (`#863`).
    """
    if not owners:
        return
    print_adt_header(SCHEMA_NOT_CONFIGURED_HEADER)
    for app_id, owner, reached_through in owners:
        through = ", ".join(sorted(reached_through))
        if not reveal:
            print(f"  APP {app_id} is owned by {owner}, exported through {through}")
            continue
        print(f"  APP {app_id} is owned by {owner}, add it to your connections to export it")
        print(f"  APP {app_id} is reachable through {through}")
    print()


def print_apex_app_not_found(app_ids: Sequence[str], *, reveal: bool = False) -> None:
    """One warning section for every requested app no read could see (`#858`).

    `-reveal` searched every workspace a configured connection can see, not
    only the configured owners, so its miss is the wider one.
    """
    if not app_ids:
        return
    where = (
        "any APEX workspace these connections reach"
        if reveal
        else "any configured APEX schema"
    )
    print_adt_header(APP_NOT_FOUND_HEADER)
    for app_id in app_ids:
        print(f"  APP {app_id} is not in {where}")
    print()
