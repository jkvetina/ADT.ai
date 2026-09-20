"""`search TERM` refreshes the text it reads rather than naming the rebuild (ADT #900).

The graph questions build their stores first (`search_autobuild.py`); a TERM
search reads a third kind of store, the source text `rebuild` mirrors, and it
answered `NOT SEARCHED: ... run adtai rebuild -app <ID>` instead. Jan,
2026-09-19, on that screen: *"I still see this shit, so the something is still
broken"*. So before a TERM search reads a layer, the layer is brought up to
date the way `rebuild` would:

- **APEX and STATIC** read the applications `-app` names, or, with no `-app`,
  every application this checkout knows (the mirror, the page picture and
  `apex.db`, which holds every application ADT.ai exported). One nobody ever
  refreshed is refreshed; one refreshed before its last change in the database
  is refreshed again. A checkout that knows no application at all refreshes
  every application its connection reaches, `-app 1+`.
- **DB** is not refreshed here: it reads the object files `export_db` wrote,
  never a mirror and never the database (ADT #904), so there is nothing to
  bring up to date before it is read.
- **GIT**, and the history search, read the branch's commit store, levelled
  with git the way `patch` levels it before reading; that needs no database.

Freshness is measured the way `search_autobuild` measures it: seconds ago on
the database's clock against the local refresh stamp's age, so no offset
between the two clocks enters it. No connection, or a database that does not
answer, leaves the layers as they stand, and `NOT SEARCHED` still names what
could not be read.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any

from adt_ai.cli.constants import ConnectionConfigError, GatewayFactory, print_adt_header
from adt_ai.cli.context import (
    ApexAppSelection,
    _app_in_selection,
    _flatten_arg_groups,
    _load_startup_context,
    _parse_apex_app_selection,
)
from adt_ai.cli.patch_inputs import ensure_commit_store
from adt_ai.cli.search_autobuild import (
    _app_stamps,
    _refresh_namespace,
    changed_apps,
    refresh_stores,
    stamped_apps,
)
from adt_ai.dependencies.schema import APEX_SOURCE_SCOPE
from adt_ai.flow.store import ApexFlowStore
from adt_ai.rebuild.models import RebuildError
from adt_ai.shared.apex_store import ApexStore, apex_store_path
from adt_ai.shared.internal_paths import internal_path

APEX_SOURCE = APEX_SOURCE_SCOPE

#: Every application the connection reaches, for a checkout that knows none.
EVERY_APP = "1+"


def prepare_term_stores(
    args: argparse.Namespace,
    root: object,
    layers: Sequence[str],
    gateway_factory: GatewayFactory | None,
) -> None:
    """Refresh what the TERM search is about to read and lacks or holds stale."""
    from pathlib import Path

    root = Path(str(root))
    apps = _apps_to_refresh(root, args, gateway_factory) if {"APEX", "STATIC"} & set(layers) else []
    if not apps or not _connects(root):
        # Nothing owed, or nothing to refresh through: the layers are read as
        # they stand, and `NOT SEARCHED` names the ones that hold nothing.
        return
    if apps == [EVERY_APP]:
        # Resolving `1+` asks the database for its applications before the
        # refresh prints its first header, so the screen says so first.
        print_adt_header("SCANNING APPLICATIONS:")
    try:
        refresh_stores(
            root,
            gateway_factory,
            machine_format = False,
            apps           = apps,
        )
    except ConnectionConfigError:
        # A connection file that names no default schema resolves above and
        # fails here; the layers are read as they stand all the same.
        return


def _connects(root: object) -> bool:
    """Whether a connection resolves at all, asked before anything is announced."""
    from pathlib import Path

    try:
        startup = _load_startup_context(_refresh_namespace(Path(str(root))))
        return bool(startup.connections.default_environment)
    except ConnectionConfigError:
        return False


def level_commit_store(args: argparse.Namespace, root: object, config: dict[str, Any]) -> None:
    """Bring the branch's commit store level with git before a search reads it.

    A branch git does not have leaves the store as it stands, and the search
    names the branch it could not read rather than stopping on the top-up.
    """
    from pathlib import Path

    try:
        ensure_commit_store(args, Path(str(root)), config)
    except RebuildError:
        return


def _known_apps(root: object) -> set[int]:
    """Every application this checkout has met: mirror, page picture, `apex.db`."""
    from pathlib import Path

    root = Path(str(root))
    known = set(stamped_apps(root)) | set(_app_stamps(root, APEX_SOURCE))
    flow_db = internal_path(root, "flow.db")
    if flow_db.exists():
        with ApexFlowStore.open(flow_db) as store:
            known.update(store.all_app_ids())
    if apex_store_path(root).exists():
        with ApexStore.load(root) as store:
            known.update(int(app) for app in store.applications())
    return known


def _apps_to_refresh(
    root: object, args: argparse.Namespace, gateway_factory: GatewayFactory | None
) -> list[str]:
    """The `-app` tokens a refresh owes the APEX and STATIC layers, `[]` for none."""
    from pathlib import Path

    root = Path(str(root))
    held = set(_app_stamps(root, APEX_SOURCE))
    tokens = _flatten_arg_groups(args.app) or []
    selection = _parse_apex_app_selection(tokens) or ApexAppSelection()
    extra: list[str] = []
    if tokens:
        wanted = {int(app) for app in selection.explicit_ids}
        in_ranges = ApexAppSelection(ranges=selection.ranges)
        ranged = {app for app in held if _app_in_selection(app, in_ranges)}
        if selection.has_ranges and not ranged:
            extra = [token for token in tokens if not token.isdigit()]
        wanted |= ranged
    else:
        wanted = _known_apps(root)
        if not wanted:
            return [EVERY_APP]
    missing = {app for app in wanted if app not in held}
    stale = changed_apps(root, sorted(wanted & held), gateway_factory, scope_type=APEX_SOURCE)
    return [*(str(app) for app in sorted(missing | set(stale))), *extra]


__all__ = [name for name in globals() if not name.startswith("__")]
