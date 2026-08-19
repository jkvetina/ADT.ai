"""Per-scope "last successful export" watermarks behind bare ``-recent``.

``-recent N`` keeps its day-window meaning everywhere. **Bare** ``-recent`` means
"everything changed since my last covering export of this scope", so repeated
refreshes stop re-exporting the whole schema. The cutoff for that mode is a
timestamp stored per scope, generated runtime metadata that lives next to
``config/internal/dependencies.db`` and is gitignored for the same reason: it
describes one checkout's export history, not the project.

Where it is stored depends on what it describes. ``export_db``'s watermarks are
in ``config/internal/recent.yaml``, which is what :class:`RecentStore` below
reads and writes. ``export_apex``'s went into ``config/internal/apex.db`` with
`#369`, beside the other facts ADT caches about an application, and are reached
through ``shared/apex_store.ApexStore``. The rules in this module are shared by
both: :func:`may_advance` decides whether a pass earns a stamp regardless of
which store holds it.

Two rules keep the watermark honest, and both are the reason this is a shared
module rather than per-command bookkeeping:

* **The candidate is read from the database clock before the object listing.**
  ``LAST_DDL_TIME`` is written by the database, so a client clock that runs slow
  would silently skip objects forever. Capturing before the listing also means an
  object changed *during* the run still has ``last_ddl_time >= candidate`` and is
  re-selected next time, the overlap window shrinks from a day to the duration
  of the previous run instead of becoming a hole.
* **The watermark advances only for a run that actually covered the scope**
  (:func:`may_advance`). A ``-name``/``-type``/``-by``/``-my``-narrowed export, or
  a ``-recent N`` window too short to reach the stored watermark, leaves it
  untouched: a packages-only export must never claim the whole schema is current.
"""

from __future__ import annotations

import datetime
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from adt_ai.shared import queries
from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.row_values import row_value
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def read_db_now(gateway: Any) -> str | None:
    """Read the database clock as the scope's candidate watermark.

    Called BEFORE the object listing so anything changed during the run stays at
    or after this instant and is re-selected next time. Returns ``None`` when the
    database gives nothing usable, which callers treat as "do not advance", a
    missing candidate must never be mistaken for a covered scope.
    """
    rows = gateway.fetch_all(queries.DB_NOW_QUERY)
    if not rows:
        return None
    value = row_value(rows[0], "DB_NOW")
    return str(value) if value else None


class _BareRecent:
    """Sentinel for a bare ``-recent`` (no day count).

    argparse never passes ``const`` through ``type``, so a non-int sentinel is
    safe as ``const`` on an ``nargs="?", type=int`` flag. A single shared
    instance keeps ``repr(action.const)`` identical across modules, which is what
    the shared-argument-shape contract compares.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "BARE_RECENT"

    def __bool__(self) -> bool:
        # `-recent` was given, so the flag is present even with no day count.
        return True


BARE_RECENT = _BareRecent()


def is_bare_recent(value: object) -> bool:
    """True when ``-recent`` was passed with no day count."""
    return isinstance(value, _BareRecent)


def recent_days(value: object) -> int | float | None:
    """The day window for ``value``; ``None`` for bare ``-recent`` and for absent.

    The window is handed back exactly as the parser produced it. It used to be
    cast to ``int`` here, which floored every sub-day window to ``0`` on its way
    to the ``:recent_days`` bind, and ``SYSDATE - 0`` selects nothing at all.
    """
    if value is None or is_bare_recent(value):
        return None
    return value  # type: ignore[return-value]


def recent_state_path(root: Path) -> Path:
    """Location of the watermark file for a project root."""
    return internal_path(root, "recent.yaml")


def parse_timestamp(value: str | None) -> datetime.datetime | None:
    """Parse a stored watermark; ``None`` when absent or unreadable."""
    if not value:
        return None
    try:
        return datetime.datetime.strptime(str(value).strip(), TIMESTAMP_FORMAT)
    except ValueError:
        return None


def may_advance(
    *,
    recent: object,
    stored: str | None,
    db_now: str,
    narrowed: bool,
) -> bool:
    """Whether a *successful* pass may stamp ``db_now`` as the scope's watermark.

    Callers own the success half of the gate (per-schema isolation: one broken
    schema must not stop or falsely stamp the others); this decides the rest.
    """
    if narrowed:
        return False
    if recent is None or is_bare_recent(recent):
        # A full export always covers the scope. Bare -recent either resumed from
        # the stored watermark (gap covered) or degraded to a full export because
        # there was none (so it may seed).
        return True
    window_days = recent_days(recent)
    stored_at = parse_timestamp(stored)
    if window_days is None or stored_at is None:
        # -recent N cannot seed: nothing proves the objects older than its window
        # were ever exported.
        return False
    now = parse_timestamp(db_now)
    if now is None:
        return False
    return now - datetime.timedelta(days=window_days) <= stored_at


class RecentStore:
    """Read/modify/write access to ``config/internal/recent.yaml``.

    Scope keys are nested mappings, ``export_db`` to env to schema, so the file
    stays readable and one module's scopes can never collide with another's.
    Every key is stored as a string, which is what let ``export_apex`` share the
    file while it lived here.

    ``export_db`` is the only module left in it. The generic shape stays: it
    costs nothing, and a second file-backed watermark scope would otherwise
    arrive to find a store hard-coded to one module.
    """

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        self._data: dict[str, Any] = data or {}
        self._dirty = False

    @classmethod
    def load(cls, root: Path) -> RecentStore:
        path = recent_state_path(root)
        return cls(path, _string_keyed(load_yaml_mapping(path)))

    def get(self, module: str, keys: Sequence[Any]) -> str | None:
        node: Any = self._data.get(module)
        for key in keys:
            if not isinstance(node, dict):
                return None
            node = node.get(str(key))
        return str(node) if isinstance(node, str) else None

    def keys(self, module: str, keys: Sequence[Any] = ()) -> list[str]:
        """The child keys under a scope, or an empty list when it holds no map.

        A watermark is read by a caller that already knows its environment and
        schema. `export_db_timers` is the other shape: the per-object-type rates
        under one schema are read all at once, and which types are in there is
        the answer rather than the question (`#377`).
        """
        node: Any = self._data.get(module)
        for key in keys:
            if not isinstance(node, dict):
                return []
            node = node.get(str(key))
        return sorted(node) if isinstance(node, dict) else []

    def set(self, module: str, keys: Sequence[Any], timestamp: str) -> None:
        if not keys:
            raise ValueError("a watermark scope needs at least one key")
        node = self._data.setdefault(module, {})
        for key in list(keys)[:-1]:
            next_node = node.get(str(key))
            if not isinstance(next_node, dict):
                next_node = {}
                node[str(key)] = next_node
            node = next_node
        node[str(keys[-1])] = timestamp
        self._dirty = True

    def save(self) -> None:
        """Write the file when something changed; a read-only run touches nothing."""
        if not self._dirty:
            return
        store_yaml_mapping(self.path, self._data)
        self._dirty = False


def _string_keyed(data: Any) -> dict[str, Any]:
    """Normalise loaded YAML so ``9055`` and ``"9055"`` address the same scope."""
    if not isinstance(data, dict):
        return {}
    return {str(key): _string_keyed(value) if isinstance(value, dict) else value
            for key, value in data.items()}
