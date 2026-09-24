"""What the previous comparison of this schema pair cost (`#763`).

The progress bar counts down, and a countdown needs a target. SQLcl DIFF
reports nothing about its own progress and returns only when it is finished, so
nothing inside a run can estimate its own remaining work: the first thing this
command can know about how long a comparison takes is what the last one took,
which is why the figure has to outlive the process.

Modelled on `ut/timers.py` and `apex_timers.yaml` down to the rolling average,
and it exists for the same reason both of those do. Jan, 2026-09-10, on a first
attempt that dodged the problem by counting UP instead: *"I SAID IMPLEMENT IT AS
OTHER APEX_EXPORT COUNTDOWN, you dont have the timer on first run there
either"*. The answer to an unmeasured first run is `export_apex`'s answer, a
fallback constant that keeps the bar crawling; it is not a different column.

**The key is the PAIR of sides plus the filter**, a side being an environment
and its schema. A comparison's cost is a property of the two schemas being
walked, not of either one, and the same source measured against a near-empty
target and against a full one are jobs of different sizes. Keying on the source
alone would let one seed the other's countdown, and keying on the schemas alone
did exactly that across environments: DEV -> UAT and DEV -> PROD on one schema
shared a figure (#923).

The filter belongs in the key for the same reason, and `#790` is what made it
matter: `-name` and `-type` now narrow the EXPORT rather than only the screen, so
a filtered run and a full one are jobs of different sizes too. Measured on
SANDBOX, a `-name` run matching nothing finishes in 11s against 47s for the full
pair, and one shared figure describes neither. `ut/timers.py` reached this shape
under the same pressure and calls the component a `variant`.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping

TIMERS_FILENAME = "diff_timers.yaml"


def timers_path(root: Path) -> Path:
    """`<root>/config/internal/diff_timers.yaml`, resolved like every cache.

    Against the project root rather than the ADT.ai install: the history
    describes this project's schemas, so it belongs beside its own config. Not
    in `internal_paths.INTERNAL_FILES`, which is the migration list for names
    that once lived under `config/`; this one was born here.
    """
    return internal_path(root, TIMERS_FILENAME)


#: The variant an unfiltered run stores under. `%` is the LIKE pattern matching
#: everything, which is what an absent filter means, and it sorts clear of any
#: real pattern in the stored file.
ALL_OBJECTS_KEY = "%"


def pair_key(
    source: str,
    target: str,
    *,
    types: Iterable[str] = (),
    names: Iterable[str] = (),
    environments: tuple[str, str] = ("", ""),
) -> str:
    """The two sides and the filter as one stable key, upper-cased.

    ADT.ai learns a schema from a connection-file key or a `-schema` argument,
    where `app_owner` is as likely as `APP_OWNER`, so folding the case here is
    what stops one run seeding a history no other run reads. Direction is kept:
    the artifact is written against the source, so `A -> B` and `B -> A` are
    different jobs and must not share a figure.

    Each side is `<environment>.<schema>`, the spelling of the screen's own
    comparison row, whenever the caller names the environments (#923): `APP`
    walked against UAT and against PROD is two jobs on two databases, and a key
    of the schemas alone let whichever ran last set the other's countdown. A
    figure stored before the environments joined the key sits under the old
    `APP -> APP` spelling, which nothing reads any more: the first run of each
    pair counts down from the fallback and records under its own key, and the
    old entry stays in the file untouched, since `record_seconds` rewrites the
    mapping without pruning it.

    The filter is appended as `[<types>|<names>]`, sorted and de-duplicated for
    the reason `ut.variant_key` does the same: neither order nor spelling changes
    which objects a run walks, so two spellings of one job must not accumulate two
    histories. An unfiltered run stores under `[%|%]`, so it is a variant like any
    other rather than a special case the readers have to know about.
    """
    pair = f"{_side(environments[0], source)} -> {_side(environments[1], target)}"
    return f"{pair} [{_variant(types)}|{_variant(names)}]"


def _side(environment: str, schema: str) -> str:
    schema = str(schema or "").upper()
    return f"{environment.upper()}.{schema}" if environment else schema


def _variant(patterns: Iterable[str]) -> str:
    found = sorted({str(pattern).upper() for pattern in patterns if str(pattern).strip()})
    return ",".join(found) if found else ALL_OBJECTS_KEY


def previous_seconds(path: Path, pair: str) -> float:
    """How long that pair took last time; `0.0` when unmeasured.

    Zero rather than a fallback constant, so the caller decides what an unknown
    target means. `commands_history` answers it the way `export_apex` does.
    """
    recorded = load_yaml_mapping(path).get(pair) or 0
    # Two refusals rather than one, because they answer different values: a
    # stored list or mapping is not a number at all, and a string that is not a
    # number only says so once `float()` has looked at it.
    if not isinstance(recorded, int | float | str):
        return 0.0
    try:
        return float(recorded)
    except ValueError:
        return 0.0


def record_seconds(path: Path, pair: str, elapsed: float) -> None:
    """Fold this run into the stored figure with `(elapsed + previous) / 2`.

    The same rolling average `apex_timers.yaml` and `ut_timers.yaml` use. A
    plain overwrite makes the estimate as noisy as the noisiest run, one slow
    round trip and every later run counts down from it; a mean over all history
    would stop tracking a pair that genuinely got bigger.
    """
    timers = dict(load_yaml_mapping(path))
    previous = previous_seconds(path, pair)
    timers[pair] = round((elapsed + previous) / 2 if previous > 0 else elapsed, 2)
    store_yaml_mapping(path, timers)


__all__ = [name for name in globals() if not name.startswith("__")]
