"""`diff -rest`: the REST modules, privileges and roles two schemas publish (ADT #878, #880).

The object comparison cannot answer this. SQLcl puts the schema's whole ORDS
definition into the DIFF artifact whether or not the two sides differ, so
`diff` drops it from the screen rather than report a change nobody made (`#790`).
Jan split REST out of `#778` and chose the spelling himself, with chips, on
2026-09-18: `diff -rest`, and REST only, so a run skips the object export that
is most of what a plain `diff` costs.

**Each side runs the same `rest export;` `export_apex -rest` runs**, through
`export_apex.rest.export_rest`, so the text compared here is byte for byte the
text that command would commit. Templates and handlers are already sorted inside
each module, which is what makes an unchanged module compare equal.

**Modules, privileges and roles are compared; the schema itself is not.**
`ORDS.ENABLE_SCHEMA` names the schema, and the two sides are usually one schema
under two names, so comparing it would report a change on every run. Privileges
and roles came in with `#880`, after the live demo on 2026-09-18, where Jan changed a
privilege on SANDBOX_DIFF and the screen printed nothing for it.

The statuses are the object listing's, read the same way: one only the source
publishes is `MISSING`, one only the target publishes is `EXTRA`, and one both
publish with different text is `CHANGED`. A role is only its name, so a role is
never `CHANGED`.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from adt_ai.diff.inventory import CHANGED, EXTRA, MISSING
from adt_ai.diff.summary import DiffChange, DiffSummary
from adt_ai.export_apex.rest import RestExport, export_rest
from adt_ai.shared.db import QueryGateway

#: The object types a `-rest` screen prints, in the singular every other row of
#: the listing uses. They sort in this order, which is the order they print in.
REST_MODULE_TYPE    = "REST MODULE"
REST_PRIVILEGE_TYPE = "REST PRIVILEGE"
REST_ROLE_TYPE      = "REST ROLE"

RestExporter = Callable[[QueryGateway, Path, Mapping[str, object]], RestExport]


def _compare(
    object_type: str,
    source: Mapping[str, str],
    target: Mapping[str, str],
) -> list[DiffChange]:
    """One `DiffChange` per name the two sides disagree about, sorted by name."""
    changes: list[DiffChange] = []
    for name in sorted(set(source) | set(target)):
        if name not in target:
            status = MISSING
        elif name not in source:
            status = EXTRA
        elif source[name] != target[name]:
            status = CHANGED
        else:
            continue
        changes.append(DiffChange(object_type, name, status))
    return changes


def compare_rest_modules(
    source: Mapping[str, str],
    target: Mapping[str, str],
) -> DiffSummary:
    """One `DiffChange` per module the two sides disagree about, sorted by name."""
    return DiffSummary(changes=tuple(_compare(REST_MODULE_TYPE, source, target)))


def compare_rest_exports(source: RestExport, target: RestExport) -> DiffSummary:
    """Modules, then privileges, then roles, each sorted by name."""
    return DiffSummary(
        changes=(
            *_compare(REST_MODULE_TYPE, source.modules, target.modules),
            *_compare(REST_PRIVILEGE_TYPE, source.privileges, target.privileges),
            *_compare(REST_ROLE_TYPE, source.roles, target.roles),
        )
    )


class RestDiffRunner:
    """Export both sides' REST definitions at once and compare them."""

    def __init__(self, export: RestExporter = export_rest) -> None:
        self._export = export

    def run(
        self,
        source: QueryGateway,
        target: QueryGateway,
        config: Mapping[str, object],
        workdir: Path,
    ) -> DiffSummary:
        """Both exports in parallel, the way the object comparison runs its two.

        Each side runs SQLcl from a folder of its own under `workdir`, so the two
        sessions never share a working directory. A failed export on either side
        raises out of here and fails the run: a comparison with one side missing
        would report every module of the other side as a difference.
        """
        folders = (workdir / "source", workdir / "target")
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            source_export = pool.submit(self._export, source, folders[0], config)
            target_export = pool.submit(self._export, target, folders[1], config)
            return compare_rest_exports(source_export.result(), target_export.result())


__all__ = [name for name in globals() if not name.startswith("_")]
