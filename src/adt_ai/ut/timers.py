"""What the previous `ut` run cost, kept in `config/internal/ut_timers.yaml`.

The progress bar counts down, and a countdown needs a target. utPLSQL hands a
suite's verdict over only once `ut.run` returns, so nothing inside a run can
estimate its own remaining work before the first suite finishes, the first
thing this command can know about how long a run takes is what the last one
took, which is why the figure has to outlive the process.

`config/internal/` is where ADT.ai keeps the data it writes about a project,
distinct from the hand-authored `config/*.yaml` a project actually edits, the
split `#316` made in the same window as this card. The path is taken from
`shared.internal_paths.internal_path` rather than joined here: that accessor
exists precisely so the layout has one definition, after fourteen literal
`root / "config" / <name>` joins across twelve modules let it drift once already.
The cache this one is modelled on down to the rolling average, `apex_timers.yaml`,
now sits in the same folder.

**The key is the schema and the `-name` variant together.** `-name` selects the
suites that *run* (`#231`, the flag narrows the run, not just the printed rows),
so a filtered run is a genuinely smaller job than an unfiltered one. Keying on
the schema alone would let a two-second `-name APP_SEC%` run seed the countdown
of a thirty-eight-second full run, and the estimate would be wrong in whichever
direction the user last ran.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping

TIMERS_FILENAME = "ut_timers.yaml"

# The key an unfiltered run stores under: the SQL wildcard, because "every
# suite" is exactly what `-name %` would have selected. A word like `ALL` would
# be a name a suite could in principle collide with.
ALL_SUITES_KEY = "%"


def timers_path(root: Path) -> Path:
    """`<root>/config/internal/ut_timers.yaml`, resolved like every cache.

    Against the project root, not the ADT.ai install: the history describes a
    schema's suites, so it belongs beside that project's own config.

    The file is **not** in `internal_paths.INTERNAL_FILES`, and deliberately so:
    that tuple is the migration list, names that once lived under `config/` and
    have to be swept forward. This one was born in `config/internal/` and has no
    legacy location to sweep from, so listing it would add a `config/` probe on
    every run of every module to look for a file that has never existed there.
    """
    return internal_path(root, TIMERS_FILENAME)


def variant_key(names: Iterable[str]) -> str:
    """The `-name` patterns as one stable key, or `%` when nothing filtered.

    Upper-cased and sorted because neither spelling nor order changes which
    suites run (`matches_sql_like` folds case and the patterns are OR-ed) so
    two spellings of one run must not accumulate two separate histories.
    """
    patterns = sorted({str(name).upper() for name in names if str(name).strip()})
    return ",".join(patterns) if patterns else ALL_SUITES_KEY


def previous_seconds(path: Path, schema: str, variant: str) -> float:
    """How long that schema's variant took last time; `0.0` when unmeasured.

    Zero rather than a fallback constant. `export_apex` invents a 999-second
    guess because its bar is time-driven and would otherwise jump to 99% on the
    first-ever run; this bar is driven by finished suites, so it moves whether or
    not it has a target and needs no invention. The bar decides what an unknown
    target means, see `progress.SuiteProgressBar`.
    """
    variants = _variants(load_yaml_mapping(path), schema)
    recorded = variants.get(variant) or 0
    # Two refusals rather than one, because they answer different values: a
    # stored list or mapping is not a number at all, and a string that is not
    # a number only says so once `float()` has looked at it.
    if not isinstance(recorded, int | float | str):
        return 0.0
    try:
        return float(recorded)
    except ValueError:
        return 0.0


def record_seconds(path: Path, schema: str, variant: str, elapsed: float) -> None:
    """Fold this run into the stored figure with `(elapsed + previous) / 2`.

    The same rolling average `apex_timers.yaml` uses. A plain overwrite would
    make the estimate as noisy as the noisiest run, one slow round trip and
    every later run counts down from it, while a mean over all history would
    stop tracking a suite that genuinely got slower.
    """
    timers = load_yaml_mapping(path)
    key = _schema_key(schema)
    variants = _variants(timers, schema)
    previous = previous_seconds(path, schema, variant)
    variants[variant] = round((elapsed + previous) / 2 if previous > 0 else elapsed, 2)
    timers[key] = variants
    store_yaml_mapping(path, timers)


def _schema_key(schema: str) -> str:
    """Oracle schemas are uppercase identifiers; the key follows.

    ADT.ai learns the name from a connection-file key or a `-schema` argument,
    where `app_owner` is as likely as `APP_OWNER`, the same reason the console
    renders it through `schema_label()`. Folding it here too is what stops one
    run seeding a history no other run reads. Storage only: the raw spelling
    still owns paths and the connection itself.
    """
    return str(schema or "").upper()


def _variants(timers: Mapping[object, object], schema: str) -> dict[str, object]:
    """That schema's variant map, and an empty one when it holds anything else.

    A hand-edited or half-written store is a missing measurement, never a failed
    run, the same posture `load_yaml_mapping` takes on a corrupt file.
    """
    variants = timers.get(_schema_key(schema))
    return dict(variants) if isinstance(variants, Mapping) else {}


__all__ = [name for name in globals() if not name.startswith("__")]
