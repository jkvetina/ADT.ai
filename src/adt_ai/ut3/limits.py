"""The two bounds a project puts on a `ut3` run: how much it prints, and how low it may go.

`ut_limit_errors` caps the `ERRORS & FAILURES:` stanzas. `ut_coverage_gate` is
the percentage below which a package fails the run, and it applies only when
`-gate` asks for it. Both are read here rather than in the renderer, because a
renderer that reads configuration is a renderer that cannot be tested by handing
it rows.

Neither value ever reaches Oracle, unlike the four in `naming.py`, which are all
binds, so they are plain functions over the loaded config rather than a second
record threaded through the query layer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adt_ai.ut3.inventory import PackageCoverage

# What a run prints before the cap is a deliberate decision rather than a
# fallback: a project with no `ut_limit_errors` at all gets the shipped 20, the
# same number `config/config.yaml` seeds. The alternative, absent means
# unlimited, would leave exactly the schema this cap was written for printing
# 3 000 lines of stanzas and pushing `SUMMARY:` out of the terminal's scrollback.
DEFAULT_ERROR_LIMIT = 20

# The gate's own default, used when `-gate` is passed bare. It gates nothing on
# its own: with `-gate` absent no package is ever compared, whatever this says.
DEFAULT_COVERAGE_GATE = 80.0

# `-gate` with no value. A float rather than a string or None so argparse's
# `type=float` can never disagree with it, and one no user can type: argparse
# reads `-inf` as an option-looking token, and a negative coverage threshold is
# not a number a real config carries either.
GATE_FROM_CONFIG = float("-inf")


def error_limit(config: Mapping[str, Any] | None) -> int:
    """How many `ERRORS & FAILURES:` stanzas print. ``0`` means every one.

    Zero is the documented escape hatch rather than a degenerate case, the same
    meaning `search_repo`'s `-limit 0` already carries, so a reader who knows one
    knows the other. A negative or unparsable value is treated as absent: a
    typo'd cap should print the shipped 20, never silently suppress the section.
    """
    value = _number(config, "ut_limit_errors")
    if value is None or value < 0:
        return DEFAULT_ERROR_LIMIT
    return int(value)


def coverage_gate(config: Mapping[str, Any] | None) -> float:
    """The threshold `-gate` uses when passed bare."""
    value = _number(config, "ut_coverage_gate")
    if value is None or value < 0:
        return DEFAULT_COVERAGE_GATE
    return float(value)


def resolve_gate(value: float | None, config: Mapping[str, Any] | None) -> float | None:
    """What `-gate` means on this run: a threshold, or None for no gating at all.

    Three states, and the middle one is why the flag takes an optional value:
    absent gates nothing and the run behaves exactly as it did before the flag
    existed; bare takes the project's own `ut_coverage_gate`; with a number, that
    number wins over the config, so `-name CORE% -gate 90` sets a stricter bar for
    one group without a second configuration surface.
    """
    if value is None:
        return None
    if value == GATE_FROM_CONFIG:
        return coverage_gate(config)
    return float(value)


def packages_below(
    packages: Sequence[PackageCoverage],
    threshold: float,
) -> list[PackageCoverage]:
    """The measured packages under the bar, worst first, then A-Z.

    **Only a package with a figure is compared.** A blank `COVERAGE` cell has
    nothing to compare, the suite paired to nothing, or Oracle never
    instrumented the target, and gating a blank would fail every real schema
    permanently from the first run. A `0.0` is a measurement and does gate: the
    package was instrumented and nothing entered it.

    **At the boundary, `>=` passes.** A package sitting exactly on the threshold
    has met it; `-gate 80` asks for eighty percent, not more than eighty. This is
    a contract rather than an implementation detail, so it is pinned by its own
    test.

    Worst first because the list is a work queue, and A-Z under it so two runs
    over the same figures print the same order.
    """
    below = [
        package
        for package in packages
        if package.percent is not None and package.percent < threshold
    ]
    return sorted(below, key=lambda package: (package.percent, package.name.upper()))


def _number(config: Mapping[str, Any] | None, key: str) -> float | None:
    value = (config or {}).get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


__all__ = [name for name in globals() if not name.startswith("__")]
