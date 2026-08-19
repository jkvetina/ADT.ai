"""Same-connection PL/Scope prerequisite for the dependencies refresh.

Before a schema's ``USER_IDENTIFIERS`` / ``USER_STATEMENTS`` mirrors can be
pulled, every PL/SQL object must have been compiled with full PL/Scope
(``IDENTIFIERS:ALL`` + ``STATEMENTS:ALL``). This runs that prerequisite *on the
already-open gateway*, it sets the session setting, asks the recompile module
which VALID objects are still missing scope, optionally narrows that list to
objects whose dictionary rows changed, and recompiles each with ``REUSE
SETTINGS`` so only PL/Scope changes. No new connection is opened and
``RecompileRunner`` (which reconnects through a no-arg factory) is never used,
honouring the "do all of this on the same connection" constraint.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from adt_ai.dependencies.queries import PLSCOPE_SESSION_STATEMENT
from adt_ai.recompile.inventory import RecompileDiscovery, RecompileObject
from adt_ai.recompile.queries import build_compile_statement
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.oracle_session import is_ddl_lock_timeout
from adt_ai.shared.progress import DottedProgressBar

# A skip interrupts the ``USER_OBJECTS`` progress row, so it renders one level
# deeper than that row's own two-space indent, the same row/detail indent pair
# ``validate/report.py`` uses for its per-record stanzas.
_DETAIL_INDENT = "    "

#: The crawling row's label. Jan's own wording, 2026-08-19, when `#381` put the
#: three candidates to him. The label alone: `shared/progress.row_left_margin`
#: prepends the indent every streamed row carries, so a bar never spells its own
#: (`#380`).
PROGRESS_HEADER = "RECOMPILING DUE TO WRONG PL/SCOPE"


def ensure_plscope(
    gateway: QueryGateway,
    *,
    candidates: Iterable[tuple[str, str]] | None = None,
    progress: Callable[[str], None] | None = None,
    bar: DottedProgressBar | None = None,
) -> list[RecompileObject]:
    """Enable full PL/Scope and recompile selected VALID objects that still lack it.

    Returns the objects recompiled (empty when the schema already has full
    PL/Scope everywhere or none of the candidates lack it). ``progress``
    receives exceptional skip lines only, not one line per successful recompile.

    ``bar`` is the crawling row all three of the waits below run under (ADT
    #381). None means the caller has no console reporter, and then nothing is
    drawn: a progress row is not required chrome. `#372` deleted the per-object
    row on purpose and this does not restore it, there is one row, redrawn in
    place, which is what that card said was missing.
    """
    _progress = progress or (lambda _: None)
    crawl = _Crawl(bar)

    # 1. Turn full PL/Scope on for this session so the recompiles below populate
    #    the identifier / statement dictionaries.
    gateway.execute(PLSCOPE_SESSION_STATEMENT)

    # 2. Discover VALID PL/SQL objects whose stored PL/Scope settings are not
    #    already IDENTIFIERS:ALL + STATEMENTS:ALL (reuses the recompile catalog
    #    read, no RecompileRunner, no second connection). This is a whole-schema
    #    scan, and it runs behind the row opened above rather than behind the
    #    section header, which cannot say that anything is still moving.
    pending = RecompileDiscovery(gateway).objects_missing_plscope()
    if candidates is not None:
        candidate_keys = set(candidates)
        pending = [
            database_object
            for database_object in pending
            if (database_object.object_type, database_object.object_name) in candidate_keys
        ]

    # 3. Recompile each with scope=["ALL"] + REUSE SETTINGS on the same gateway.
    recompiled: list[RecompileObject] = []
    total = len(pending)
    for index, database_object in enumerate(pending, start=1):
        statement = build_compile_statement(
            database_object.object_type,
            database_object.object_name,
            scope=["ALL"],
        )
        try:
            gateway.execute(statement)
        except Exception as exc:
            if not is_ddl_lock_timeout(exc):
                raise
            # End the row before the message: the bar rewrites in place with a
            # carriage return, so a line printed under an open row is erased by
            # the next redraw.
            crawl.interrupt()
            _progress(
                f"{_DETAIL_INDENT}SKIPPED LOCKED "
                f"{database_object.object_type} {database_object.object_name}"
            )
            continue
        finally:
            crawl.advance(index, total)
        recompiled.append(database_object)
    crawl.close()

    return recompiled


class _Crawl:
    """The one redrawn row, and the arithmetic behind its percent and its ETA.

    A no-op when there is no bar, so every call site above reads the same with
    or without a console and no branch has to be repeated around each draw.
    """

    def __init__(self, bar: DottedProgressBar | None) -> None:
        self._bar = bar
        self._started_at = time.monotonic()
        self._open = False
        self._done = False
        # Opened before the first database call, so the session ALTER and the
        # catalog scan behind it both block against an open line rather than a
        # finished one (`#379`: a row is not an announcement once it closes).
        self._draw(0, 0)

    def advance(self, index: int, total: int) -> None:
        if total <= 0:
            return
        percent = min(int(((index / total) * 100) + 0.5), 100)
        elapsed = time.monotonic() - self._started_at
        remaining = (elapsed / index) * (total - index) if index else 0.0
        self._draw(percent, int(elapsed if index == total else remaining), close=index == total)

    def interrupt(self) -> None:
        """Terminate the open row so something else can print under it."""
        if self._open:
            self._draw(self._last_percent, int(time.monotonic() - self._started_at), close=True)

    def close(self) -> None:
        """Close a row the loop never closed, a schema with nothing pending."""
        if not self._done:
            self._draw(100, int(time.monotonic() - self._started_at), close=True)

    def _draw(self, percent: int, seconds: int, *, close: bool = False) -> None:
        self._last_percent = percent
        if self._bar is None:
            self._open = not close
            self._done = self._done or close
            return
        self._bar.print_line(PROGRESS_HEADER, percent, seconds, close=close)
        self._open = not close
        self._done = self._done or close
