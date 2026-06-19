"""Recursive mapping merge shared by config and connection loading."""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return a new dict with ``overlay`` deep-merged over ``base``.

    Nested dicts are merged recursively; any non-dict value in ``overlay``
    replaces the value at the same key in ``base``. Neither input is mutated.
    """
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = deep_merge(existing, value)
        else:
            merged[key] = value
    return merged
