"""The per-schema baseline a windowed export compares scheduler jobs against.

A JOB is the one exported type the data dictionary cannot date. `user_objects`
carries a JOB row, but its `LAST_DDL_TIME` is the last **run**: measured on
CORE26/APPS 2026-08-20 with a dummy job, a scheduler execution carrying no DDL
moved it from 11:26:31 to 11:27:31 while `CREATED` stayed at 11:26:31, and a later
in-place `SET_ATTRIBUTE` moved it again and left `CREATED` alone. `CREATED` is
stable but only sees a create or a drop+create, never an in-place edit, and
`user_scheduler_job_log` records `RUN` only, because `DEFAULT_JOB_CLASS` ships at
`LOGGING_LEVEL = RUNS`.

So the signal is built. `JOBS_QUERY` hashes the columns the exported file is
rendered from, in the database, and this module remembers the last hash exported
per environment and schema. A windowed run exports the jobs whose hash moved.

**The store gates a WINDOW, never an explicit request** (Jan, 2026-08-20). A run
carrying no `-recent` means "give me all of them", so it exports every matching
job and the comparison never runs. Gating an explicit `-type JOB` would silently
skip objects the caller named, which is the defect ADT #414 was filed on.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping

MODULE = "export_db"


def job_signatures_path(root: Path) -> Path:
    """`config/internal/job_signatures.yaml`, gitignored beside `recent.yaml`."""
    return internal_path(root, "job_signatures.yaml")


class JobSignatureStore:
    """Read/modify/write access to the per-(environment, schema) signature map."""

    def __init__(self, path: Path, data: dict[str, Any] | None = None) -> None:
        self.path = path
        self._data: dict[str, Any] = data or {}

    @classmethod
    def load(cls, root: Path) -> JobSignatureStore:
        path = job_signatures_path(root)
        return cls(path, _string_keyed(load_yaml_mapping(path)))

    def get(self, environment: str, schema: str) -> dict[str, str]:
        """Every remembered signature for one schema, or an empty map when new.

        An empty map is what makes the first run a full export that seeds the
        store rather than a run that mistakes "nothing remembered" for "nothing
        changed" and exports no job at all.
        """
        node = self._data.get(MODULE, {})
        node = node.get(str(environment), {}) if isinstance(node, dict) else {}
        node = node.get(str(schema), {}) if isinstance(node, dict) else {}
        if not isinstance(node, dict):
            return {}
        return {str(name).upper(): str(value) for name, value in node.items()}

    def update(
        self,
        environment: str,
        schema: str,
        signatures: Mapping[str, str],
        replace: bool,
    ) -> None:
        """Merge this run's signatures in, or replace the schema's map wholesale.

        `replace` is for a run that covered the schema's whole job set: it also
        forgets jobs that have since been dropped, so a dropped-and-recreated name
        cannot match a stale hash. A narrowed run merges instead, because it never
        looked at the jobs it did not select and must not claim anything about them
        (the discipline `recent.yaml` already applies to its watermark).
        """
        module = self._data.setdefault(MODULE, {})
        env_node = module.setdefault(str(environment), {})
        current = {} if replace else dict(env_node.get(str(schema), {}) or {})
        current.update({str(name).upper(): str(value) for name, value in signatures.items()})
        env_node[str(schema)] = current

    def save(self) -> None:
        store_yaml_mapping(self.path, self._data)


def changed_jobs(
    discovered: Mapping[str, str],
    stored: Mapping[str, str],
) -> list[str]:
    """The job names whose signature is new or different, in discovery order.

    A name absent from `stored` is new and therefore changed. A name whose stored
    hash equals the fresh one is unchanged and is left out of the export entirely,
    which is the round trip this whole module exists to avoid.

    A job carrying no signature at all is reported as changed rather than skipped:
    an unanswerable window includes the object, it never drops one silently, which
    is the failure mode this whole card exists to remove.
    """
    return [
        name
        for name, signature in discovered.items()
        if not signature or stored.get(str(name).upper()) != signature
    ]


def job_baseline(request, schema: str) -> dict[str, str] | None:
    """The stored signatures a windowed run compares this schema's jobs against.

    `None` disables the comparison entirely, which is what an unwindowed run gets:
    no `-recent` means the caller asked for every job, so there is nothing to gate
    (Jan, 2026-08-20). An environment-less run is treated the same way, because the
    store is keyed by environment and has nothing to answer with.

    `request` is untyped for the reason `watermarks.py` gives: annotating it would
    import `ExportDbRequest` back from the module that imports this one.
    """
    if not request.recent or request.environment is None:
        return None
    return JobSignatureStore.load(request.root).get(request.environment, schema)


def advance_job_signatures(
    request,
    schema: str,
    signatures: Mapping[str, str] | None,
    narrowed: bool,
) -> None:
    """Record what this run exported, so the next windowed run has a baseline.

    Every run stamps, not just a windowed one: a `-type JOB` sweep is exactly the
    moment the baseline is most complete, and leaving it unrecorded would make the
    following `-recent` re-export the whole set. A narrowed run merges rather than
    replaces, so the jobs it never selected keep whatever the store already knew.
    """
    if not signatures or request.environment is None:
        return
    store = JobSignatureStore.load(request.root)
    store.update(request.environment, schema, signatures, replace=not narrowed)
    store.save()


def _string_keyed(data: Any) -> dict[str, Any]:
    return dict(data) if isinstance(data, dict) else {}
