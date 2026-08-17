"""What the last export of a schema cost, so the next one can say how long it has left.

`#373` gave `-compact` a countdown projected from the run's own rate, which is
the only thing a first run can do and is a poor answer for the first few
objects: on `CORE26` a 55 second export read `0:00:43`, then `0:10:12`, then
`0:06:46`, because object 2 of 71 happened to cost 17 seconds and two samples
were the whole sample. Jan, 2026-08-16: *"you know recent times so it would be
better to based it on that, the current version is very jumpy and not user
friendly."*

**The stored unit is seconds per object OF A TYPE, never a run total.** A run
total cannot be read back by a narrowed run, and a per-object average is worse
than useless across types: `export_db -type SEQUENCE` finishes four objects in
under a second, and a store that learned "an object costs 0.2s" from it would
tell the next full export of the same schema that its 71 objects, a dozen of
them tables with constraint blocks to fold, would take fifteen seconds. Keyed by
type, that same `-type SEQUENCE` run teaches the store only about sequences and
every other estimate is untouched.

**`SETUP_KEY` is the wait with no objects in it.** `start_export` draws the bar
before `setup_dbms_metadata()` and the comment pre-read, deliberately, so the
screen is not parked on a closed header while they run. That time is elapsed the
bar counts and no per-object rate can explain, so it is measured and stored on
its own.

**A type never measured contributes nothing rather than a guess.** The blend in
`ObjectProgressBar._target` corrects an under-estimate as the run proceeds; an
invented per-object cost for an unmeasured type would be a number nothing later
disagrees with.

Storage is `shared/recent_state.RecentStore`, the `config/internal/recent.yaml`
this module already owns for its per-schema watermarks, under its own top-level
key so the two cannot collide. No new internal file: a second one would need its
own `internal_paths` entry to record the same fact about the same command, and
`#368` is the card about a root carrying two files that answer one question.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from adt_ai.shared.recent_state import RecentStore

# Its own scope inside recent.yaml; `export_db` there is the watermark tree.
TIMER_MODULE = "export_db_timers"

# The DBMS_METADATA setup and comment pre-read. A colon cannot appear in an
# Oracle object type, so this can never collide with a real one.
SETUP_KEY = ":setup"


def load_object_rates(root: Path, environment: str | None, schema: str) -> dict[str, float]:
    """Stored seconds per object, by object type, for one environment and schema."""
    if not environment:
        return {}
    store = RecentStore.load(root)
    rates: dict[str, float] = {}
    for object_type in store.keys(TIMER_MODULE, [environment, schema]):
        value = _as_float(store.get(TIMER_MODULE, [environment, schema, object_type]))
        # A measured `0.0` is kept rather than filtered: it is what a type of
        # instant objects genuinely costs, and dropping it would make the stored
        # set disagree with the set of types the last run actually exported.
        # Only an unparseable value is skipped.
        if value is not None:
            rates[object_type] = value
    return rates


def store_object_rates(
    root: Path,
    environment: str | None,
    schema: str,
    measured: Mapping[str, tuple[int, float]],
) -> None:
    """Fold this run's measurements into the stored rates.

    ``measured`` maps an object type (or ``SETUP_KEY``) to ``(count, seconds)``.
    A new rate is averaged with the stored one, the smoothing
    ``export_apex/progress.py::_update_timer`` already applies, so a single run
    slowed by something outside ADT.ai cannot own the estimate.

    An environment-free run stores nothing: the connection file's environment is
    what makes a rate comparable, and a run without one cannot be told apart
    from a run against a different database.
    """
    if not environment:
        return
    store = RecentStore.load(root)
    changed = False
    for object_type, (count, seconds) in measured.items():
        if count <= 0 or seconds < 0:
            continue
        rate = seconds / count
        previous = _as_float(store.get(TIMER_MODULE, [environment, schema, object_type])) or 0.0
        blended = (rate + previous) / 2 if previous > 0 else rate
        store.set(TIMER_MODULE, [environment, schema, object_type], f"{blended:.4f}")
        changed = True
    if changed:
        store.save()


def estimate_seconds(rates: Mapping[str, float], counts: Mapping[str, int]) -> float:
    """What this run should cost: every selected object at its type's stored rate.

    ``counts`` maps object type to how many of them this run will export. The
    setup rate is added once when it is known, because it is paid once per
    schema segment however many objects follow it.
    """
    total = sum(rates.get(object_type, 0.0) * count for object_type, count in counts.items())
    if total > 0:
        total += rates.get(SETUP_KEY, 0.0)
    return total


def estimate_for(
    root: Path,
    environment: str | None,
    schema: str,
    objects: Iterable[object],
) -> float:
    """What this run should cost, priced from history for the objects selected.

    The one call `export_db/runner.py` makes to open a `-compact` bar, so the
    runner never has to know that the rates are keyed by object type.
    """
    return estimate_seconds(
        load_object_rates(root, environment, schema),
        Counter(getattr(item, "object_type", "") for item in objects),
    )


class SegmentTimer:
    """Wall clock for one schema segment, split the way the rates are stored.

    Lives here rather than in the runner because what it measures is defined by
    what the store keeps: a span per object type plus the one setup span. The
    runner says start, done, record, store, and owns none of the bookkeeping
    (`#377`; the split also keeps `runner.py` inside the 20 KB context budget
    that `#372` already refused to spend on `grants.py`).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._mark = clock()
        self._spans: dict[str, list[float]] = {}

    def setup_done(self) -> None:
        """Close the DBMS_METADATA setup and comment pre-read span."""
        now = self._clock()
        self._spans.setdefault(SETUP_KEY, []).append(now - self._mark)
        self._mark = now

    def record(self, object_type: str) -> None:
        """Close one object's span: its DDL pull plus the previous object's write.

        Those land between the same two marks because the export loop yields to
        its consumer, and averaged over a type that is what one of them costs.
        """
        now = self._clock()
        self._spans.setdefault(object_type, []).append(now - self._mark)
        self._mark = now

    def store(self, root: Path, environment: str | None, schema: str) -> None:
        store_object_rates(
            root,
            environment,
            schema,
            {key: (len(spans), sum(spans)) for key, spans in self._spans.items()},
        )


def _as_float(value: object) -> float | None:
    """The stored seconds, or ``None`` when the file holds something else.

    ``None`` and ``0.0`` are different answers here: the first says nothing was
    measured, the second says it was measured and is instant.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


__all__ = [name for name in globals() if not name.startswith("__")]
