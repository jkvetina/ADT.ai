"""What `-app` selects, and the one export it refuses to ship.

The flag answers one question about each APEX application a patch touches: does
it ship as its own `f<id>.sql` full export, or as the components that changed?
Three call sites ask it (`selection._patch_files`, `selection._apex_copy_files`
and `create._apex_patch_payload`), so the answer lives here once rather than as a
set membership test written out three times, which is the shape ADT #474 already
made a rule of.

**An APEXlang application answers neither way, and ADT #606 is why that matters.**
There are two whole-application formats, not one: the legacy `f<id>.sql` export
and the `apexlang/` tree, and only the first is a file a patch can link. The
tree is imported from the folder it lives in (ADT #602), so a run naming an
APEXlang application had no way through. `-create -app 1000` refused to build
without a fresh `f1000.sql`, and committing one made `-deploy -app <sandbox>`
refuse the same patch for installing the source application in place. `-force`
was the only key that fit both locks, and it also silences the DRIFTED signature
check standing beside them, which is the route a client patch took into a live
application on 2026-08-30. `resolve_full_app_ids` therefore reads the format off
the selected commits and leaves an APEXlang application out of full export mode
altogether: its tree ships, its components ship, and no gate below compares it
against an export nothing imports.

**Selection only.** ADT #592 folded `-fullapp` into `-app`, whose optional value
is the application id the tree LANDS on rather than a filter over which
applications ship, so `resolve_target` hands this module the selection half and
keeps the target to itself (`patch/apex_import.py`). That is what leaves the
readers below reading the same `None`/`[]` they always did: retargeting moves
where a tree installs, never which files a patch carries.

**The flag carries three states, not two, and the third is what ADT #576 was
filed on.** It is declared `nargs="*"`, so the flag with no ids parses to an
empty list, and while the default was also `[]` a bare flag was byte-identical
to never passing it: `include_full_exports` read `bool([])`,
`_patch_files` skipped its whole APEX filter, and the patch listed every
individual component with nothing on screen saying so. `None` is now "never
given" and `[]` is "given with no ids", the same sentinel split `-hash` carries,
and an empty list means every application the patch touches. Jan, 2026-08-27:
*"If the app numbers are not passed, you will automatically add apps which are in
the patch files."*

**The refusal is the other half.** A full export carries the application as it
stood when it was exported, so a component committed after it is a change the
patch claims to ship and does not, invisibly, because the file count and the
screen are both correct. Jan, 2026-08-27: *"If we have changes in the app which
are newer than the latest full export, we have to stop and tell the user to
refresh the full export (and commit it)."* It is a stop rather than a
`WARNING - ` section, which is the opposite of what `#468` decided for
`WARNING - OBJECTS CHANGED:`, and deliberately so: that one names files the
operator may not even be working on, while this one is always about the
application the run asked for by name, and the fix is one re-export away.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from adt_ai.patch import settings as _settings
from adt_ai.patch.layout import (
    apex_app_id,
    is_apex_full_export,
    is_apex_path,
    is_apex_static_file,
    is_apexlang_path,
)
from adt_ai.shared.commit_discovery import CommitRecord

EXPORT_COMMAND = "adtai export_apex -full -app"


def is_full_app(app_id: int | None, full_app_ids: list[int] | None) -> bool:
    """Does this application ship as one full export?

    ``None`` is the flag never given, so nothing is full. An empty list is a bare
    `-app`, so everything is. A path carrying no application id at all
    (`apex/workspace/rest/`, and the `0` `create.py` falls back to) is never
    full, because it is not an application.
    """
    if full_app_ids is None or not app_id:
        return False
    return not full_app_ids or app_id in full_app_ids


def ships_in_patch(
    path: str,
    config: dict[str, Any],
    full_app_ids: list[int] | None,
) -> bool:
    """Does this file belong in a patch built with these `-app` ids?

    A full application ships its `f<id>.sql` and none of its components; every
    other application ships its components and never its full export. The second
    half is not symmetry for its own sake: `include_full_exports` is one boolean
    for the whole run, so a selection naming 1000 also lets `f2000.sql` past the
    commit scanner, and linking that would import a second whole application in
    the middle of a component patch.
    """
    if full_app_ids is None or not is_apex_path(path, config):
        return True
    if is_full_app(apex_app_id(path, config), full_app_ids):
        return is_apex_full_export(path, config)
    return not is_apex_full_export(path, config)


def resolve_full_app_ids(
    records: list[CommitRecord],
    config: dict[str, Any],
    requested: list[int] | None,
) -> list[int] | None:
    """The applications this run ships in full, with a bare `-app` resolved.

    Resolved once, at the top of the build, so every reader below is handed the
    same list: deriving it per call site is how three copies of one question
    start disagreeing about it.

    **An application shipping an APEXlang tree is never one of them** (ADT #606).
    `-app` names a whole-application mode and the repository's own export format
    picks which one: a tree deploys through `apex import`, out of the folder it
    lives in, so `f<id>.sql` is neither its payload nor a measure of its
    freshness. Jan, 2026-08-31: *"you dont need f1000.sql for anything, you are
    importing apexlang folder, you are not touching this file for anything."*
    Dropped here rather than at each of the four readers below, because the
    question is about the APPLICATION and every reader downstream is asked about
    one path at a time.

    An empty result is returned as ``None``, the value that already means "no
    application ships as a full export": `[]` is the flag's pre-resolution
    sentinel for "every application the patch touches", so handing it back after
    resolution would say the opposite of what was resolved.
    """
    if requested is None:
        return None
    apexlang = _apexlang_app_ids(records, config)
    if requested:
        return sorted(set(requested) - apexlang) or None
    return sorted({
        app_id
        for path in _apex_paths(records, config)
        if (app_id := apex_app_id(path, config)) and app_id not in apexlang
    }) or None


@dataclass(frozen=True)
class StaleFullApp:
    """One application whose components outran the export a patch would ship."""

    app_id: int
    # None when no `f<id>.sql` for this application is in the selected commits at
    # all, which is the same defect in its strongest form: there is no export to
    # ship, so every component change is newer than it. Reported through this
    # shape rather than a second kind of answer the caller would have to learn,
    # the way `StaleScope.last_refresh` already spells "never refreshed".
    export_commit: int | None
    newer_commits: tuple[int, ...]


def stale_full_apps(
    records: list[CommitRecord],
    config: dict[str, Any],
    full_app_ids: list[int] | None,
) -> list[StaleFullApp]:
    """Applications an `-app` build would ship an out-of-date export for.

    Measured inside the run's own selected commits, which is what the patch
    carries: a component committed outside that window is the existing
    `WARNING - OUTDATED FILES:` section's question, not this one.

    Two kinds of APEX file are never compared. An `apex_files_ignore` match never
    enters a patch at all, and a static-file payload is not in a full export by
    design (`-files` is the single static-file channel), so refusing on one would
    name a re-export that cannot possibly contain it and the operator would have
    no way past the gate.
    """
    if full_app_ids is None:
        return []
    exports: dict[int, int] = {}
    components: dict[int, set[int]] = {}
    for record in records:
        for path in _record_paths(record):
            app_id = apex_app_id(path, config) if is_apex_path(path, config) else None
            if app_id is None or not is_full_app(app_id, full_app_ids):
                continue
            if is_apex_full_export(path, config):
                exports[app_id] = max(exports.get(app_id, record.number), record.number)
            elif not _is_out_of_scope(path, config):
                components.setdefault(app_id, set()).add(record.number)
    stale: list[StaleFullApp] = []
    for app_id in sorted(components):
        exported = exports.get(app_id)
        newer = tuple(
            number
            for number in sorted(components[app_id])
            if exported is None or number > exported
        )
        if newer:
            stale.append(
                StaleFullApp(app_id=app_id, export_commit=exported, newer_commits=newer)
            )
    return stale


def stale_full_app_message(stale: list[StaleFullApp]) -> str:
    """The refusal, in the shape the other two build gates already print.

    A lead line, one indented row per application, and a `Run:` line naming the
    command that fixes it, exactly as `GraphFreshness.failure_message` reads.
    """
    lines = [
        "Stale full app export: the application changed after the export this "
        "patch would ship, so those changes are not in it."
    ]
    for item in stale:
        exported = (
            f"exported in commit {item.export_commit}"
            if item.export_commit is not None
            else "no full export in these commits"
        )
        changed = ", ".join(str(number) for number in item.newer_commits)
        lines.append(f"  APP {item.app_id}: {exported}, changed since in {changed}")
    apps = ",".join(str(item.app_id) for item in stale)
    lines.append(f"Run: {EXPORT_COMMAND} {apps}, then commit the export")
    return "\n".join(lines)


def require_fresh_full_app_exports(
    records: list[CommitRecord],
    config: dict[str, Any],
    full_app_ids: list[int] | None,
) -> None:
    """Raise ``PatchError`` when a full export is older than its own components."""
    from adt_ai.patch.runner import PatchError

    stale = stale_full_apps(records, config, full_app_ids)
    if not stale:
        return
    raise PatchError(stale_full_app_message(stale))


def _apexlang_app_ids(records: list[CommitRecord], config: dict[str, Any]) -> set[int]:
    """Applications the selected commits ship as an APEXlang tree.

    One `.apx` is enough: the tree is a whole-application format, so a commit
    touching one page of it is a change to an application that installs through
    the import rather than through any SQL the patch could link.
    """
    return {
        app_id
        for path in _apex_paths(records, config)
        if is_apexlang_path(path, config) and (app_id := apex_app_id(path, config))
    }


def _apex_paths(records: list[CommitRecord], config: dict[str, Any]) -> Iterator[str]:
    for record in records:
        for path in _record_paths(record):
            if is_apex_path(path, config):
                yield path


def _record_paths(record: CommitRecord) -> Iterator[str]:
    """Every path a commit touched, a deletion included.

    A deleted page is a change to the application exactly as an edited one is,
    and `_patch_files` already reads both sides for the same reason.
    """
    yield from record.usable_files
    yield from record.deleted_files


def _is_out_of_scope(path: str, config: dict[str, Any]) -> bool:
    return _settings.is_ignored_apex_file(path, config) or is_apex_static_file(path, config)


__all__ = [name for name in globals() if not name.startswith("_")]
