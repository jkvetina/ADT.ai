"""The checks a `patch` build runs before it writes anything.

Three of them, and they share one shape: each answers whether the run's result
would be wrong in the target database rather than wrong in ADT, and each is
checked before the first byte is written so a refusal leaves nothing behind.

**Two refuse and one reports.** The graph gate and the deployed-folder guard
still stop the run; the export-freshness check became a `WARNING - OBJECTS
CHANGED:` section on `#468` (see :func:`export_freshness` for Jan's reasoning and
for what the trade costs).

Freshness gate over ``config/internal/dependencies.db`` for the ordering actions.

``patch -install`` and ``patch -create`` order objects from the dependency
mirror. A graph older than the objects it is ordering cannot describe them, so
the order it produces is a guess that only fails once SQLcl runs the script,
the failure lands in the database, not in ADT (Jan, 2026-07-31).

Both actions therefore refuse rather than degrade, and a missing or unreadable
graph is the same class of failure as a stale one: "no graph" and "the wrong
graph" both mean the ordering is unproven. There is deliberately no override,
``-force`` does not reach this gate, and the way past it is
``adtai dependencies -refresh``.

Staleness is measured against the mirror's own ``refreshes`` stamps, the
schema rows ``dependencies -age`` reads, versus the
newest mtime among the exported object files that would be ordered. Read-only
previews consume no ordering and are never gated.

Reading the mirror is `patch/graph_mirror.py`; this file is what the answers
mean, the same split `patch/clocks.py` already draws for the arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.patch.clocks import STAMP_FORMAT, ddl_seconds, stamp_seconds
from adt_ai.patch.files import _install_groups, _install_targets
from adt_ai.patch.graph_mirror import GRAPH_FILE
from adt_ai.patch.graph_mirror import object_ddl_times as _object_ddl_times
from adt_ai.patch.graph_mirror import schema_offsets as _schema_offsets
from adt_ai.patch.graph_mirror import schema_stamps as _schema_stamps
from adt_ai.patch.layout import database_object_type, database_schema, object_layouts
from adt_ai.shared.deploy_status import latest_deploy_status
from adt_ai.shared.object_files import object_name_for_type

REFRESH_COMMAND = "adtai dependencies -refresh"

# The label a layout without a `<schema>` placeholder reports under: the tree it
# collects cannot be attributed to one owner.
UNSCOPED_LABEL = "(all schemas)"


@dataclass(frozen=True)
class StaleScope:
    """One schema whose exported objects outran the graph's refresh stamp."""

    schema: str
    # None when the mirror holds no `refreshes` stamp for the schema at all,
    # the scope was never refreshed, which is stale in the strongest sense.
    last_refresh: str | None
    newest_object: str
    newest_file: str

    @property
    def label(self) -> str:
        return self.schema or UNSCOPED_LABEL


@dataclass(frozen=True)
class GraphFreshness:
    """Whether the mirror can be trusted to order the objects on disk."""

    graph_missing: bool
    stale: list[StaleScope]

    @property
    def is_fresh(self) -> bool:
        return not self.graph_missing and not self.stale

    def failure_message(self) -> str:
        if self.is_fresh:
            return ""
        if self.graph_missing:
            return (
                f"No readable {GRAPH_FILE}: objects cannot be ordered from a graph "
                f"that is absent or unreadable, and name order is not a runnable "
                f"script.\nRun: {REFRESH_COMMAND}"
            )
        lines = [
            f"Stale {GRAPH_FILE}: the graph is older than the objects it would order."
        ]
        for scope in self.stale:
            refreshed = scope.last_refresh or "never"
            lines.append(
                f"  {scope.label}: refreshed {refreshed}, "
                f"newest object {scope.newest_object} ({scope.newest_file})"
            )
        lines.append(f"Run: {self._refresh_hint()}")
        return "\n".join(lines)

    def _refresh_hint(self) -> str:
        schemas = [scope.schema for scope in self.stale if scope.schema]
        if len(schemas) != len(self.stale):
            # At least one stale scope has no owner to narrow by, so a `-schema`
            # hint would refresh less than the run needs.
            return REFRESH_COMMAND
        return f"{REFRESH_COMMAND} -schema {','.join(schemas)}"


def graph_freshness(root: Path, config: dict[str, Any]) -> GraphFreshness:
    """Report whether the dependency mirror covers every object on disk."""
    newest = _newest_objects(root, config)
    if not newest:
        # Nothing to order, so there is nothing a graph could fail to describe.
        return GraphFreshness(graph_missing=False, stale=[])

    stamps = _schema_stamps(root)
    if stamps is None:
        # The scopes are reported even with no graph to be measured against.
        # `newest` already names one target per schema, which is exactly the
        # list an auto-refresh narrows itself to, and answering `[]` here is what
        # made `patch -create` hand the operator a command to retype instead of
        # running it (`#569`): the caller reads `stale` to learn what to refresh
        # and found nothing to name. `graph_missing` still decides the message,
        # so a root that genuinely cannot be refreshed reads as it always did.
        return GraphFreshness(graph_missing=True, stale=_never_refreshed(newest))

    stale: list[StaleScope] = []
    for schema, (relative, mtime) in newest.items():
        stamp = _covering_stamp(schema, stamps)
        refreshed = stamp_seconds(stamp) if stamp is not None else None
        # `int(mtime)`, deliberately: the refresh stamp covers its own whole
        # second. `#657` proposed comparing the full float, on the reading that
        # a same-second edit is being called fresh. Measured, that trades a
        # silent miss for a worse failure, because the precision that is missing
        # is on the STAMP side and no precision on this side can recover it: the
        # stamp is written to whole seconds, so a file exported at `11:30:46.9`
        # and a refresh that ran right after it at `11:30:46.95` record as
        # `11:30:46`, and a full-float compare calls that stale. `patch -create`
        # then auto-refreshes, records the same truncated stamp, and reports
        # stale again. `clocks.stamp_seconds` carries the reasoning.
        if refreshed is not None and refreshed >= int(mtime):
            continue
        stale.append(
            StaleScope(
                schema        = schema,
                last_refresh  = stamp,
                newest_object = datetime.fromtimestamp(mtime).strftime(STAMP_FORMAT),
                newest_file   = relative,
            )
        )
    return GraphFreshness(graph_missing=False, stale=stale)


def require_forced_refresh(folder: Path, *, force: bool) -> None:
    """Refuse to rebuild a patch folder that has already been deployed.

    Old ADT's PR `#3` (`33932192`) refused to wipe a patch's deploy logs, and
    Jan chose that shape over a content-hash skip on 2026-08-13: refuse, with
    `-force` as the override. `#309` carded it and `#366` is where it lands.

    The hazard is specific. A folder carrying a deploy log is a folder somebody
    has already run against a database, so its install scripts are the record of
    what that database received. Rewriting them in place leaves the logs
    describing a script that no longer exists, and the next reader compares a
    deployment against contents it never had.

    With ``force`` the rebuild is a REFRESH, which is Jan's word for it,
    2026-08-15: *"it should be basically treated as a refresh of the patch and the
    previous logs should be kept"*. The deploy log folders sit beside the
    generated SQL and no part of the create path removes them, so keeping them
    costs no code, only the guarantee that it stays that way
    (`tests/patch/test_create_force_refresh.py`).

    **The scripts folder used to be named in that same breath and no longer is**
    (ADT #508, 2026-08-24). Keeping it turned out to be the defect rather than
    the promise: `templates._script_payload` links every file in the slot with no
    commit filter, so a refresh shipped whatever an earlier window had generated.
    `scripts.reset_patch_scripts` empties it at the top of a forced create, and
    hands anything hand-authored back to the source folder rather than deleting
    it. `-force` covers both halves now, and they are opposite halves.
    """
    from adt_ai.patch.runner import PatchError

    if force or not folder.is_dir():
        return
    status = latest_deploy_status(folder)
    if status is None:
        return
    raise PatchError(
        f"{folder.name} has already been deployed ({status}), so rebuilding it "
        "would leave its deploy logs describing scripts that no longer exist.\n"
        "Run with -force to refresh it; the logs are kept and patch_scripts/ is\n"
        "rebuilt, hand-written scripts going back to the project folder first."
    )


def require_fresh_dependency_graph(root: Path, config: dict[str, Any]) -> None:
    """Raise ``PatchError`` unless the mirror describes the objects on disk."""
    from adt_ai.patch.runner import PatchError

    report = graph_freshness(root, config)
    if report.is_fresh:
        return
    raise PatchError(report.failure_message())


@dataclass(frozen=True)
class StaleExport:
    """One object the database has moved past since it was last exported."""

    schema: str
    object_type: str
    object_name: str
    file: str
    last_ddl: str
    exported: str


def stale_exports(root: Path, config: dict[str, Any], files: list[str]) -> list[StaleExport]:
    """Patch files whose object changed in the database after the export.

    Jan, 2026-08-09: "BEFORE you create patch, you HAVE TO make sure that
    export_db is up to date, so the snapshots are not created on stalled
    objects." A patch snapshots repo FILES, so an object edited in the schema and
    not re-exported ships its previous body, and deploying it silently reverts
    the live change. Nothing in ADT caught that: `require_fresh_dependency_graph`
    compares the graph against the same files, so a repo that has not been
    exported for a week passes it cleanly, both sides equally stale.

    Read offline on purpose. The mirror already carries the dictionary's own
    ``LAST_DDL_TIME`` per object, and `-create` runs this gate immediately after
    the graph-freshness gate, so the mirror is known to be at least as new as
    the files, and its DDL times are the database's answer, not a guess about
    when ADT last ran. That is what a watermark (`config/internal/recent.yaml`, a
    ``refreshes`` stamp) could not have given: a watermark says when an export
    happened, never whether the schema moved after it.

    An object whose owner has no recorded database UTC offset is skipped here
    and reported by :func:`unclocked_scopes`: those two readings cannot be
    compared at all, which is a different answer from "not stale".
    """
    return [item for item in _compare(root, config, files) if isinstance(item, StaleExport)]


def unclocked_scopes(root: Path, config: dict[str, Any], files: list[str]) -> list[str]:
    """Owners whose mirrored DDL times cannot be resolved to an instant.

    A mirror refreshed before ADT #394 carries no ``db_utc_offset`` for the
    schema (see :mod:`adt_ai.patch.clocks`), so its DDL times are naive readings off a
    clock nothing recorded. Scoped to the owners actually being compared: an
    object absent from the mirror is skipped before any clock question arises,
    so a brand-new file cannot drag its whole schema into a refusal.
    """
    return sorted({item for item in _compare(root, config, files) if isinstance(item, str)})


def _compare(
    root: Path,
    config: dict[str, Any],
    files: list[str],
) -> list[StaleExport | str]:
    """One walk: the stale exports, plus the owners with no recorded clock.

    Both questions read the same rows and skip on the same conditions, so they
    share the walk rather than drifting apart as two copies of it.
    """
    ddl_times = _object_ddl_times(root)
    if not ddl_times:
        return []
    offsets = _schema_offsets(root)
    layouts = object_layouts(config.get("object_types", {}))
    found: list[StaleExport | str] = []
    for relative in files:
        path = root / relative
        object_type = database_object_type(relative, config)
        if object_type is None or not path.is_file():
            continue
        # defensive: `database_object_type` can only return None, "REST" (gated on a configured
        # REST layout), or a key already drawn from this same `layouts` dict
        if object_type.upper() not in {value.upper() for value in layouts}:  # pragma: no cover
            continue
        schema = database_schema(relative, config)
        owner = schema.upper()
        # The mirror holds Oracle's own name, so the key has to be read through
        # the type's configured extension. `Path.stem` asked for `CORE.SPEC`,
        # matched no row, and dropped every package spec and type body out of
        # this walk at the `continue` below (ADT #471).
        object_name = object_name_for_type(Path(relative).name, object_type, layouts)
        # defensive: `object_type_under` already matched this filename's extension against the
        # same layout to resolve `object_type`, so `object_name_for_type` cannot itself fail to
        # strip it
        if not object_name:  # pragma: no cover
            continue
        key = (owner, object_type.upper(), object_name)
        last_ddl = ddl_times.get(key)
        if not last_ddl:
            continue
        offset = offsets.get(owner)
        if offset is None:
            found.append(owner)
            continue
        changed = ddl_seconds(last_ddl, offset)
        # Spelled like `graph_freshness` above, and here the truncation changes
        # nothing either way: `changed` is whole seconds, so `<= floor(mtime)`
        # and `<= mtime` agree on every input. Kept identical so the two
        # staleness reads cannot drift into two different rules.
        if changed is None or changed <= int(path.stat().st_mtime):
            continue
        found.append(
            StaleExport(
                schema      = schema,
                object_type = object_type.upper(),
                object_name = object_name,
                file        = relative,
                last_ddl    = str(last_ddl),
                exported    = datetime.fromtimestamp(path.stat().st_mtime).strftime(STAMP_FORMAT),
            )
        )
    return found


@dataclass(frozen=True)
class ExportFreshness:
    """What the mirror says about the files a patch is about to snapshot."""

    # Objects the database moved past after they were exported, so the patch
    # would ship the previous body.
    changed: list[StaleExport]
    # Owners whose mirrored DDL times cannot be resolved to an instant at all.
    unclocked: list[str]

    @property
    def is_clean(self) -> bool:
        return not self.changed and not self.unclocked


def export_freshness(
    root: Path,
    config: dict[str, Any],
    files: list[str],
) -> ExportFreshness:
    """Report the files the database has already moved past (ADT #261, then #468).

    **This reports; it does not refuse.** It raised until `#468`, on the same
    posture as the two gates above it: the failure it describes lands in the
    target database rather than in ADT, and it is invisible, the patch reports
    the right file count and deploys the previous body over the live change. Jan
    traded that for a warning after a build of his own stopped over three jobs,
    2026-08-22: *"it is a blocker, but I might not even be working with the files
    mentioned, so it is a stupid blocker ... it will be just a warning, not a
    show stopper"*.

    **What that costs belongs where the answer is produced.** The comparison
    reads only the files the patch actually SELECTED, so every object named here
    IS in the patch and the build ships the older body for it.

    **It is still the whole of the protection for ITS window, which is the
    repository already being behind at BUILD time.** ADT #665 added a second
    check over a different window, the target moving AFTER the build, and that
    one refuses rather than warns (`patch/signatures.py`). Neither covers the
    other: an object stale here signs against its stale base and then deploys
    cleanly, which is precisely the case this section exists to name.

    The clock half moved with it rather than staying fatal, because it was the
    harder consequence sitting on the WEAKER signal: an unclocked owner means
    "these two readings are not comparable", which is less than "this object is
    provably out of date". `REFRESH_COMMAND` is still what fixes it, named in
    `docs/patch.md` rather than repeated on every row (Jan: *"no stupid dates,
    nothing else; make it simple!"*).

    Both answers come off one walk (`_compare`), which reads the same rows and
    skips on the same conditions for each. Neither opens a database.
    """
    return ExportFreshness(
        changed   = stale_exports(root, config, files),
        unclocked = unclocked_scopes(root, config, files),
    )


def _newest_objects(root: Path, config: dict[str, Any]) -> dict[str, tuple[str, float]]:
    """Per schema, the newest object file that ``-install`` would order.

    Keyed by the target's schema (empty string for a layout with no ``<schema>``
    placeholder); the value is the repo-relative path and its mtime. A schema
    root holding no objects is absent from the map, it orders nothing, so it
    has nothing to be stale about.
    """
    newest: dict[str, tuple[str, float]] = {}
    for target in _install_targets(root, config):
        for files in _install_groups(target, config, {}).values():
            for relative in files:
                path = target.root / relative
                try:
                    mtime = path.stat().st_mtime
                # defensive: `relative` is drawn from `_install_groups`' own filesystem `rglob`,
                # which just found this exact path, so a `stat()` failure here is a race (deleted
                # between the walk and this read), not a normal path
                except OSError:  # pragma: no cover
                    continue
                current = newest.get(target.schema)
                if current is None or mtime > current[1]:
                    newest[target.schema] = (
                        path.relative_to(root).as_posix(),
                        mtime,
                    )
    return dict(sorted(newest.items()))


def _never_refreshed(newest: dict[str, tuple[str, float]]) -> list[StaleScope]:
    """Every target as a scope carrying no refresh stamp at all.

    `last_refresh=None` is the dataclass's own spelling of "the scope was never
    refreshed, which is stale in the strongest sense", so a missing graph
    reports through the same shape a stale one does rather than a second kind of
    answer the caller would have to learn.
    """
    return [
        StaleScope(
            schema        = schema,
            last_refresh  = None,
            newest_object = datetime.fromtimestamp(mtime).strftime(STAMP_FORMAT),
            newest_file   = relative,
        )
        for schema, (relative, mtime) in newest.items()
    ]


def _covering_stamp(schema: str, stamps: dict[str, str]) -> str | None:
    """The stamp that has to cover this target's objects.

    A named schema is measured against its own stamp. An unnamed target (a
    ``path_objects`` with no ``<schema>``) collects a tree that spans every
    owner, and a graph is only as fresh as its stalest scope, so it is measured
    against the oldest stamp in the mirror.
    """
    if schema:
        return stamps.get(schema.upper())
    if not stamps:
        return None
    return min(stamps.values(), key=lambda value: (stamp_seconds(value) or 0, value))



__all__ = [name for name in globals() if not name.startswith("_")]
