"""Post-run result shaping for recompile.

What the final re-check *means*, as opposed to how it is performed: which objects
are still invalid and with what errors (:func:`enrich_invalid`), and how many of
each type the run repaired (:func:`with_validated`). Both are pure functions over
the re-check reads, so they are exercised through ``runner.run`` without a
database.

Split out of ``runner.py`` for the same reason ``contracts.py`` was — to keep that
module inside the repo's 20 KB context-size contract (``tests/contracts/
test_context_file_size.py``). ``runner.py`` is the only caller.
"""

from __future__ import annotations

from dataclasses import replace

from adt_ai.recompile.inventory import ObjectError, ObjectOverview, RecompileObject


def with_validated(
    overview: list[ObjectOverview],
    before_invalid: list[RecompileObject],
    remaining: list[RecompileObject],
) -> list[ObjectOverview]:
    """Stamp each overview row with how many of its objects the run repaired (#186).

    A set difference over object identity, never a before/after count delta:
    recompiling a spec invalidates its dependents, so a run that fixed one object
    and broke another leaves the INVALID count unchanged — and a delta would
    report that repair as nothing happening, which is the blindness this column
    exists to remove.
    """
    still_invalid = {(obj.object_type, obj.object_name) for obj in remaining}
    counts: dict[str, int] = {}
    for obj in before_invalid:
        if (obj.object_type, obj.object_name) not in still_invalid:
            counts[obj.object_type] = counts.get(obj.object_type, 0) + 1
    return [
        replace(row, validated=counts.get(row.object_type, 0))
        for row in overview
    ]


def enrich_invalid(
    remaining: list[RecompileObject],
    errors: list[ObjectError],
) -> list[ObjectError]:
    """Pair each still-invalid object with its error summary.

    An object with no ``user_errors`` row still gets an entry (zero errors, no
    code): a view invalidated by a dropped dependency is invalid without ever
    having been compiled, and dropping it here would hide it from the report.
    """
    index = {(error.object_type, error.object_name): error for error in errors}
    enriched: list[ObjectError] = []
    for obj in remaining:
        match = index.get((obj.object_type, obj.object_name))
        if match is not None:
            enriched.append(match)
        else:
            enriched.append(ObjectError(obj.object_type, obj.object_name, 0, None))
    return enriched
