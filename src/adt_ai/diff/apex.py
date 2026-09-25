"""`diff -apex`: the APEX applications and static files two schemas own (ADT #778).

The object comparison cannot answer this. SQLcl writes `releases/apex/f<id>/f<id>.sql`
into its artifact whether or not the application changed, which is the row `#779`
took off the screen rather than report a change nobody made. Jan chose the
spelling with chips on 2026-09-19: `diff -apex`, with an optional repeatable
`-app`, and APEX only, so a run skips the object export the way `-rest` does.

**Applications pair by id.** ADT deploys an application under the id it was
exported with and keeps no per-environment id map, so application 100 on DEV is
application 100 on UAT. One only the source owns is `MISSING`, one only the target
owns is `EXTRA`.

**An application both sides own is `CHANGED` when APEX's own fingerprint differs.**
`APEX_EXPORT.GET_APPLICATION(p_type => 'CHECKSUM-SH256')` is the value `export_apex`
already caches per application (`#343`), and APEX computes it independently of
component ids precisely so two instances can be compared; so a second environment
imported with a different id offset still compares equal.

**Static files compare by content.** An application's own files are read for the
applications both sides own, since a missing application already says its files
are missing; the workspace's files are read beside them. Both through
`APEX_FILES_QUERY`, the query `export_apex -files` and `-files_ws` write from, so a
file compares equal exactly when that export would write the same bytes.

**A CHANGED application is then exported on both sides and compared component
by component** (`apex_export` reads it, `apex_components` compares it). Jan
rejected the first cut on the pair it was built for: *-verbose says only that the
app changed, not WHICH page, WHICH components, WHAT the change is.* The
fingerprint stays the gate, so only an application whose fingerprints differ
costs the two exports.

**Everything an application differs in is reported under it** (ADT #893). Jan:
*"I am always comparing specific app(s), so only things which can change outside
of the app are workspace files."* So an application's own files ride in its
block beside its pages and components, a missing or extra application gets a
block of its own, and the workspace's files are the one list outside them, each
with its size.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace

from adt_ai.diff.apex_components import (
    READABLE,
    SPLIT,
    ComponentChange,
    compare_components,
    export_format,
)
from adt_ai.diff.apex_export import read_both
from adt_ai.diff.inventory import CHANGED, EXTRA, MISSING

# Named `as` itself because `diff/pull_apex.py` imports it from here too: the
# block lives in the module's SQL home (#923), and this keeps it exported.
from adt_ai.diff.queries import WORKSPACE_START_QUERY as WORKSPACE_START_QUERY

# The name-by-name comparison `-rest` already runs, so both modes read MISSING,
# EXTRA and CHANGED by one rule; and the pattern match `-name` narrows by.
from adt_ai.diff.rest import _compare
from adt_ai.diff.summary import DiffSummary, _matches_name, _wildcards
from adt_ai.export_apex import queries
from adt_ai.export_apex.inventory import ApexDiscovery
from adt_ai.export_apex.postprocess import _blob_bytes, _checksum_value
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.queries.versions import APEX_VERSION_QUERY
from adt_ai.shared.row_values import row_value
from adt_ai.shared.session_scope import require_database_session

#: The object types a `-apex` comparison counts, which decide whether the two
#: sides match; the screen lists per application and the workspace's files (#896).
APEX_APPLICATION_TYPE    = "APEX APPLICATION"
APEX_APP_FILE_TYPE       = "APEX APP FILE"
APEX_WORKSPACE_FILE_TYPE = "APEX WORKSPACE FILE"

#: The application id `wwv_flow_files` files a workspace's own static files under.
WORKSPACE_FILES_APP_ID = 0


@dataclass(frozen=True)
class ApexSide:
    """One side of the comparison: where it connects and which applications it means.

    `selects` is the `-app` selection, ids and ranges alike, applied to what the
    owner's applications are; `None` keeps every one of them. `ids` maps the id
    the comparison files an application under to the id this side keeps it
    under, which is how `-target-app` pairs a working copy (ADT #893).
    """

    gateway   : QueryGateway
    owner     : str
    workspace : str | None = None
    group     : str | None = None
    selects   : Callable[[int], bool] | None = field(default=None, compare=False)
    ids       : Mapping[int, int] = field(default_factory=dict)

    def own_id(self, app_id: int) -> int:
        """The id this side keeps the compared application `app_id` under."""
        return self.ids.get(app_id, app_id)


@dataclass(frozen=True)
class ApexSnapshot:
    """What one side answered, reduced to a comparable value per name."""

    checksums            : Mapping[int, str]
    app_files            : Mapping[str, str]
    workspace_files      : Mapping[str, str]
    #: The instance's APEX release, which picks the format a CHANGED
    #: application is exported in. `None` when the probe answered nothing.
    apex_version         : str | None = None
    #: Each file's bytes, beside its digest, for the screen's `SIZE` (#893).
    app_file_sizes       : Mapping[str, int] = field(default_factory=dict)
    workspace_file_sizes : Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class FileChange:
    """A static file the two sides disagree about, and how big it is."""

    name   : str
    status : str
    #: The source's copy in bytes, or the target's when only the target has it.
    size   : int = 0


@dataclass(frozen=True)
class ApplicationChanges:
    """One application that differs: its pages, components and files.

    A `MISSING` or `EXTRA` application carries no component, since only one
    side has it and nothing was exported to compare. `target` labels the
    target's application when `-target-app` paired it under another id, and
    `app_id` is the id the comparison filed it under.
    """

    application : str
    components  : tuple[ComponentChange, ...] = ()
    status      : str = CHANGED
    files       : tuple[FileChange, ...] = ()
    target      : str = ""
    app_id      : int = field(default=0, compare=False)


@dataclass(frozen=True)
class ApexDiff:
    """The counts, what each application differs in, and the workspace's files."""

    summary         : DiffSummary
    applications    : tuple[ApplicationChanges, ...] = ()
    workspace_files : tuple[FileChange, ...] = ()

    def select(self, names: Sequence[str]) -> ApexDiff:
        """What `-name` keeps: applications by their label, workspace files by name."""
        if not names:
            return self
        patterns = _wildcards(names)
        return ApexDiff(
            self.summary.select(names=names),
            tuple(app for app in self.applications if _matches_name(app.application, patterns)),
            tuple(file for file in self.workspace_files if _matches_name(file.name, patterns)),
        )

    def select_pages(self, selects: Callable[[int], bool]) -> ApexDiff:
        """What `-page` keeps: the selected pages, and nothing application-wide (#893).

        Jan: *"-page to check specific page, at that point you would ignore app
        changes and workspace changes"*. So a block keeps only its selected page
        rows, the application's own properties, shared components and files go,
        and so do the workspace's files. An application only one side has stays:
        its pages cannot match on a side without it. The counts follow, so an
        application whose selected pages match is neither counted nor listed,
        and a run where none differ reads as two matching sides.
        """
        applications: list[ApplicationChanges] = []
        for app in self.applications:
            if app.status != CHANGED:
                applications.append(app)
                continue
            pages = tuple(
                change
                for change in app.components
                if change.page is not None and selects(change.page)
            )
            if pages:
                applications.append(replace(app, components=pages, files=()))
        kept = {app.application for app in applications}
        changes = tuple(
            change
            for change in self.summary.changes
            if change.object_type == APEX_APPLICATION_TYPE and change.object_name in kept
        )
        return ApexDiff(DiffSummary(changes, filtered=self.summary.filtered), tuple(applications))


def application_label(app_id: int, alias: str) -> str:
    """`100/ORDERS`: the id pairs the two sides, the alias says which one it is.

    Slash-joined since ADT #973, the one spelling every command uses.
    """
    return f"{app_id}/{alias}" if alias else str(app_id)


def compare_apex(
    source_apps: Mapping[int, str],
    target_apps: Mapping[int, str],
    source: ApexSnapshot,
    target: ApexSnapshot,
) -> DiffSummary:
    """Applications, then their files, then the workspace's files.

    `*_apps` maps each application id to its alias. A paired application is
    labelled with the source's alias, because the source is the side the row
    describes; one side only is labelled with the alias of the side that has it.
    """
    labels = {**target_apps, **source_apps}
    applications = _compare(
        APEX_APPLICATION_TYPE,
        {application_label(i, labels[i]): source.checksums.get(i, "") for i in source_apps},
        {application_label(i, labels[i]): target.checksums.get(i, "") for i in target_apps},
    )
    return DiffSummary(
        changes=(
            *applications,
            *_compare(APEX_APP_FILE_TYPE, source.app_files, target.app_files),
            *_compare(APEX_WORKSPACE_FILE_TYPE, source.workspace_files, target.workspace_files),
        )
    )


def list_applications(side: ApexSide) -> dict[int, str]:
    """The applications this side's owner has, narrowed by `-app`, as id to alias.

    Each is keyed by the id the comparison files it under, its own unless `ids`
    pairs it with another.
    """
    applications = ApexDiscovery(side.gateway).applications(
        owner     = side.owner,
        workspace = side.workspace,
        group     = side.group,
    )
    shared = {own: app_id for app_id, own in side.ids.items()}
    return {
        shared.get(application.app_id, application.app_id): application.app_alias
        for application in applications
        if side.selects is None or side.selects(application.app_id)
    }


def read_snapshot(side: ApexSide, app_ids: Sequence[int]) -> ApexSnapshot:
    """The workspace's files, then each named application's fingerprint and files.

    Workspace files come first, while the only workspace set is the one this side
    maps to; every application after it sets its own. The fingerprint and the
    collection it is read back from have to reach one database session, exactly
    as they do in `export_apex`, so a transport that cannot promise one is refused
    rather than read as an application with no fingerprint.
    """
    gateway = side.gateway
    require_database_session(gateway, "diff -apex")
    gateway.execute(WORKSPACE_START_QUERY, {"owner": side.owner, "workspace": side.workspace})
    workspace_files, workspace_sizes = _files(gateway, WORKSPACE_FILES_APP_ID, prefix="")
    checksums: dict[int, str] = {}
    app_files: dict[str, str] = {}
    app_sizes: dict[str, int] = {}
    for app_id in app_ids:
        own = side.own_id(app_id)
        gateway.execute(queries.EXPORT_START_QUERY, {"app_id": own})
        gateway.execute(queries.EXPORT_CHECKSUM_QUERY, {"app_id": own})
        checksums[app_id] = _checksum_value(gateway.fetch_all(queries.FETCH_FILES_QUERY))
        # Filed under the compared id, so a working copy's files pair with the
        # original's rather than reading as another application's.
        digests, sizes = _files(gateway, own, prefix=f"{app_id}/")
        app_files.update(digests)
        app_sizes.update(sizes)
    return ApexSnapshot(
        checksums,
        app_files,
        workspace_files,
        _apex_version(gateway),
        app_file_sizes       = app_sizes,
        workspace_file_sizes = workspace_sizes,
    )


def _apex_version(gateway: QueryGateway) -> str | None:
    rows = gateway.fetch_all(APEX_VERSION_QUERY)
    value = row_value(rows[0], "VERSION") if rows else None
    return str(value) if value else None


def _files(
    gateway: QueryGateway, app_id: int, *, prefix: str
) -> tuple[dict[str, str], dict[str, int]]:
    """File name to a digest of its bytes, and file name to how many bytes.

    `wwv_flow_files` can hold one name twice (SANDBOX's workspace does), so every
    copy is digested and the digests are joined in order: two sides holding the
    same copies compare equal, a side holding one copy fewer does not. The size
    is every copy's together, what the name holds. Only the digest is compared.
    """
    digests: dict[str, list[str]] = {}
    sizes: dict[str, int] = {}
    for row in gateway.fetch_all(queries.APEX_FILES_QUERY, {"app_id": app_id}):
        name = f"{prefix}{row_value(row, 'FILENAME') or ''}"
        payload = _blob_bytes(row_value(row, "BLOB_CONTENT"))
        digests.setdefault(name, []).append(hashlib.sha256(payload).hexdigest())
        sizes[name] = sizes.get(name, 0) + len(payload)
    return {name: ",".join(sorted(values)) for name, values in digests.items()}, sizes


def _file_changes(
    source: Mapping[str, str],
    target: Mapping[str, str],
    source_sizes: Mapping[str, int],
    target_sizes: Mapping[str, int],
) -> list[FileChange]:
    """Each file the sides disagree about, sized by the side the row describes."""
    return [
        FileChange(
            change.object_name,
            change.status,
            (target_sizes if change.status == EXTRA else source_sizes).get(change.object_name, 0),
        )
        for change in _compare("", source, target)
    ]


def _applications(
    source_apps: Mapping[int, str],
    target_apps: Mapping[int, str],
    source: ApexSnapshot,
    target: ApexSnapshot,
    compared: Mapping[int, tuple[ComponentChange, ...]],
    target_ids: Mapping[int, int],
) -> tuple[ApplicationChanges, ...]:
    """A block per application that differs, by id, with its own files in it.

    A target application `-target-app` paired under another id is labelled by
    its own id and alias, beside the source's, since the two are not one name.
    """
    files: dict[int, list[FileChange]] = {}
    for change in _file_changes(
        source.app_files, target.app_files, source.app_file_sizes, target.app_file_sizes
    ):
        owner, _, name = change.name.partition("/")
        files.setdefault(int(owner), []).append(replace(change, name=name))
    blocks: list[ApplicationChanges] = []
    for app_id in sorted(set(source_apps) | set(target_apps)):
        own = target_ids.get(app_id, app_id)
        theirs = application_label(own, target_apps.get(app_id, ""))
        if app_id not in target_apps:
            label = application_label(app_id, source_apps[app_id])
            blocks.append(ApplicationChanges(label, status=MISSING, app_id=app_id))
        elif app_id not in source_apps:
            blocks.append(ApplicationChanges(theirs, status=EXTRA, app_id=app_id))
        elif app_id in compared or app_id in files:
            blocks.append(
                ApplicationChanges(
                    application_label(app_id, source_apps[app_id]),
                    compared.get(app_id, ()),
                    CHANGED,
                    tuple(files.get(app_id, ())),
                    target = theirs if own != app_id else "",
                    app_id = app_id,
                )
            )
    return tuple(blocks)


def changed_applications(source: ApexSnapshot, target: ApexSnapshot) -> list[int]:
    """The paired applications whose fingerprints differ, the ones worth exporting."""
    return sorted(
        app_id
        for app_id, checksum in source.checksums.items()
        if app_id in target.checksums and checksum != target.checksums[app_id]
    )


class ApexDiffRunner:
    """List both sides' applications, read both snapshots, then drill into what changed."""

    def run(self, source: ApexSide, target: ApexSide) -> ApexDiff:
        """Three parallel rounds, the way `-rest` runs its two exports.

        The first lists each side's applications; the second fingerprints only
        the ones both sides own, since a missing application needs no
        fingerprint to be reported missing, and every fingerprint is a whole
        application export on the database side. The third exports only the
        applications whose fingerprints differ, in the one format both
        instances have.
        """
        with ThreadPoolExecutor(max_workers=2) as pool:
            source_listing = pool.submit(list_applications, source)
            target_listing = pool.submit(list_applications, target)
            source_apps, target_apps = source_listing.result(), target_listing.result()
            paired = sorted(set(source_apps) & set(target_apps))
            source_read = pool.submit(read_snapshot, source, paired)
            target_read = pool.submit(read_snapshot, target, paired)
            source_snapshot, target_snapshot = source_read.result(), target_read.result()
            summary = compare_apex(source_apps, target_apps, source_snapshot, target_snapshot)
            changed = changed_applications(source_snapshot, target_snapshot)
            compared: dict[int, tuple[ComponentChange, ...]] = {}
            if changed:
                export = export_format(
                    source_snapshot.apex_version, target_snapshot.apex_version
                )
                components = read_both(pool, source, target, changed, export)
                if export == READABLE:
                    # An instance without READABLE_YAML answers an empty export,
                    # and an application with no component at all does not exist.
                    fallback = [
                        app for app in changed if not all(side[app] for side in components)
                    ]
                    if fallback:
                        split = read_both(pool, source, target, fallback, SPLIT)
                        for side, again in zip(components, split, strict=True):
                            side.update(again)
                compared = {
                    app_id: compare_components(components[0][app_id], components[1][app_id])
                    for app_id in changed
                }
        return ApexDiff(
            summary,
            _applications(
                source_apps, target_apps, source_snapshot, target_snapshot, compared, target.ids
            ),
            tuple(
                _file_changes(
                    source_snapshot.workspace_files,
                    target_snapshot.workspace_files,
                    source_snapshot.workspace_file_sizes,
                    target_snapshot.workspace_file_sizes,
                )
            ),
        )


__all__ = [name for name in globals() if not name.startswith("_")]
